"""Persistent, bounded Xiaohongshu extraction through Kimi Browser Extension.

Only navigates ordinary public/share URLs. No cookie export, signed API replay,
or CAPTCHA handling. The browser supplies the note state it normally loads.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from .errors import FetchError
from .links import extract_note_id, extract_url, validate_url
from .parser import parse_html, parse_state

BASE_URL = "http://127.0.0.1:10086"
EXPLORE_URL = "https://www.xiaohongshu.com/explore"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 120:
        raise FetchError("INVALID_ARGUMENT", "timeout 必须大于 0 且不超过 120 秒。")
    return float(value)


class BridgeClient:
    """UTF-8 file-body requests, as required by WebBridge on Windows."""
    def __init__(self, session: str):
        self.session = session
        self.command_count = 0

    def request(self, action: str | None, args: dict | None = None, *, timeout: float = 10) -> dict:
        body_path = None
        command = ["curl.exe" if os.name == "nt" else "curl", "-sS", "--noproxy", "*",
                   "--connect-timeout", "2", "--max-time", str(max(.05, timeout))]
        try:
            if action is None:
                command += [BASE_URL + "/status"]
            else:
                self.command_count += 1
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="xhs-wb-", suffix=".json", delete=False) as body:
                    body_path = body.name
                    json.dump({"action": action, "args": args or {}, "session": self.session}, body, ensure_ascii=False, separators=(",", ":"))
                command += ["-X", "POST", BASE_URL + "/command", "-H", "Content-Type: application/json", "--data-binary", "@" + body_path]
            process = subprocess.run(command, capture_output=True, timeout=max(.001, timeout),
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if process.returncode:
                code = "TIMEOUT" if process.returncode == 28 else "BRIDGE_UNAVAILABLE"
                raise FetchError(code, "WebBridge 请求超时。" if code == "TIMEOUT" else "无法连接 WebBridge 本地服务，请启动服务并连接浏览器扩展。")
            try:
                result = json.loads(process.stdout.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise FetchError("BRIDGE_PROTOCOL_ERROR", "WebBridge 返回了无效 JSON。") from exc
            if not isinstance(result, dict):
                raise FetchError("BRIDGE_PROTOCOL_ERROR", "WebBridge 返回了无效响应。")
            if action is None:
                return result
            if result.get("ok") is False or result.get("success") is False:
                error = result.get("error") or result.get("message") or "WebBridge command failed"
                raise FetchError("BRIDGE_ERROR", str(error)[:500])
            data = result.get("data", result)
            if not isinstance(data, dict):
                raise FetchError("BRIDGE_PROTOCOL_ERROR", "WebBridge data 不是对象。")
            if data.get("success") is False or data.get("error"):
                raise FetchError("BRIDGE_ERROR", str(data.get("error") or data.get("message"))[:500])
            return data
        except subprocess.TimeoutExpired as exc:
            raise FetchError("TIMEOUT", "WebBridge 请求超时。") from exc
        except OSError as exc:
            raise FetchError("BRIDGE_UNAVAILABLE", "无法运行 curl 或写入临时请求文件。") from exc
        finally:
            if body_path:
                Path(body_path).unlink(missing_ok=True)

    def evaluate(self, code: str, *, timeout: float = 10):
        result = self.request("evaluate", {"code": code}, timeout=timeout)
        value = result.get("value")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                pass
        return value


@contextmanager
def _session_lock(session: str, deadline: float):
    """Serialize full note operations across threads AND MCP server processes."""
    digest = hashlib.sha256(session.encode()).hexdigest()[:24]
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(digest, threading.Lock())
    if not lock.acquire(timeout=max(0, deadline - time.perf_counter())):
        raise FetchError("TIMEOUT", "等待浏览器会话空闲超时。")
    handle = None
    locked = False
    try:
        path = Path(tempfile.gettempdir()) / ("xhs-wb-session-" + digest + ".lock")
        handle = path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (OSError, BlockingIOError):
                if time.perf_counter() >= deadline:
                    raise FetchError("TIMEOUT", "另一个进程正在使用此浏览器会话。")
                time.sleep(.025)
        yield
    finally:
        if handle:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        lock.release()


class WebBridgeFetcher:
    def __init__(self, session: str | None = None):
        self.session = session if session is not None else os.environ.get("XHS_WEBBRIDGE_SESSION") or "xhs-mcp-" + uuid.uuid4().hex[:12]
        if not isinstance(self.session, str) or not self.session.strip() or len(self.session) > 100:
            raise FetchError("INVALID_ARGUMENT", "session 必须是 1–100 个字符。")
        self.bridge = BridgeClient(self.session)
        self._ready = False
        self._cache: dict[str, tuple[float, dict]] = {}

    def status(self) -> dict:
        raw = self.bridge.request(None, timeout=3)
        return {key: raw.get(key) for key in ("running", "version", "extension_connected", "extension_version")} | {"session": self.session}

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise FetchError("TIMEOUT", "在限定时间内未取得完整笔记详情。")
        return remaining

    def _ensure_tab(self, deadline: float):
        if self._ready:
            return
        data = self.bridge.request("list_tabs", timeout=self._remaining(deadline))
        tabs = data.get("tabs", [])
        if tabs:
            self.bridge.request("find_tab", {"url": tabs[0]["url"]}, timeout=self._remaining(deadline))
        else:
            self.bridge.request("navigate", {"url": EXPLORE_URL, "newTab": True, "group_title": "小红书 MCP 抓取"}, timeout=self._remaining(deadline))
        self._ready = True

    def discover_notes(self, limit: int = 20) -> list[dict]:
        try:
            return self._discover_notes(limit)
        except FetchError as exc:
            if exc.code in {"BRIDGE_ERROR", "BRIDGE_UNAVAILABLE", "BRIDGE_PROTOCOL_ERROR", "TIMEOUT"}:
                self._ready = False
            raise

    def _discover_notes(self, limit: int) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise FetchError("INVALID_ARGUMENT", "limit 必须是 1–50 的整数。")
        deadline = time.perf_counter() + 20
        with _session_lock(self.session, deadline):
            self._ensure_tab(deadline)
            previous_origin = self.bridge.evaluate("JSON.stringify(performance.timeOrigin)", timeout=self._remaining(deadline))
            self.bridge.request("cdp", {"method": "Page.navigate", "params": {"url": EXPLORE_URL}}, timeout=self._remaining(deadline))
            while True:
                result = self.bridge.evaluate(_DISCOVER_JS.replace("__LIMIT__", str(limit)).replace("__PREVIOUS_ORIGIN__", json.dumps(previous_origin)), timeout=self._remaining(deadline))
                if isinstance(result, dict) and result.get("error"):
                    raise FetchError(result["error"], result.get("message", "页面无法提供笔记链接。"))
                if isinstance(result, list) and result:
                    return result
                self._remaining(deadline)
                time.sleep(.1)

    def fetch_note(self, share_text: str, *, timeout: float = 10, use_cache: bool = False) -> dict:
        try:
            return self._fetch_note(share_text, timeout=timeout, use_cache=use_cache)
        except FetchError as exc:
            if exc.code in {"BRIDGE_ERROR", "BRIDGE_UNAVAILABLE", "BRIDGE_PROTOCOL_ERROR", "TIMEOUT"}:
                self._ready = False
            raise

    def _fetch_note(self, share_text: str, *, timeout: float, use_cache: bool) -> dict:
        timeout = _seconds(timeout)
        if not isinstance(use_cache, bool):
            raise FetchError("INVALID_ARGUMENT", "use_cache 必须是布尔值。")
        started = time.perf_counter()
        deadline = started + timeout
        url = extract_url(share_text)
        requested_id = extract_note_id(url)
        cache_key = requested_id or url
        with _session_lock(self.session, deadline):
            acquired = time.perf_counter()
            cached = self._cache.get(cache_key)
            if use_cache and cached and acquired - cached[0] < 300:
                note = deepcopy(cached[1])
                note.update(cache_hit=True, timing={"total_ms": round((time.perf_counter() - started) * 1000, 3), "cache_age_ms": round((acquired - cached[0]) * 1000, 3)})
                self._remaining(deadline)
                return note
            commands_before = self.bridge.command_count
            self._ensure_tab(deadline)
            prepared = time.perf_counter()
            fallback_reason = None
            if requested_id and urlsplit(url).hostname == "www.xiaohongshu.com":
                try:
                    document = self._fetch_document(url, requested_id, deadline)
                    if document is not None:
                        note, browser_metrics = document
                        finished = time.perf_counter()
                        note.update(fetch_method="webbridge_document", cache_hit=False,
                                    timing={"queue_ms": round((acquired-started)*1000,3), "prepare_ms": round((prepared-acquired)*1000,3),
                                            "document_ms": round((finished-prepared)*1000,3), "total_ms": round((finished-started)*1000,3),
                                            "bridge_commands": self.bridge.command_count-commands_before, **browser_metrics})
                        self._remember(cache_key, note, deadline)
                        return note
                    fallback_reason = "document_state_unavailable"
                except FetchError as exc:
                    if exc.code not in {"DOCUMENT_FETCH_FAILED", "PARSE_ERROR", "INCOMPLETE_NOTE"}:
                        raise
                    fallback_reason = exc.code
            previous_origin = self.bridge.evaluate("JSON.stringify(performance.timeOrigin)", timeout=self._remaining(deadline))
            prepared = time.perf_counter()
            nav = self.bridge.request("cdp", {"method": "Page.navigate", "params": {"url": url}}, timeout=self._remaining(deadline))
            if nav.get("errorText"):
                raise FetchError("NETWORK_ERROR", "浏览器无法打开目标笔记。", reason=nav["errorText"])
            navigated = time.perf_counter()
            last_error = None
            while True:
                code = _NOTE_JS.replace("__NOTE_ID__", json.dumps(requested_id)).replace("__PREVIOUS_ORIGIN__", json.dumps(previous_origin)).replace("__WAIT_MS__", str(min(500, max(1, int(self._remaining(deadline) * 1000 - 100)))))
                try:
                    result = self.bridge.evaluate(code, timeout=self._remaining(deadline))
                except FetchError as exc:
                    if exc.code == "BRIDGE_ERROR" and any(s in exc.message.lower() for s in ("context", "navigat", "destroy", "cannot find")):
                        last_error = exc
                        time.sleep(.025)
                        continue
                    self._ready = False
                    raise
                if isinstance(result, dict) and result.get("error"):
                    raise FetchError(result["error"], result.get("message", "页面无法提供笔记详情。"))
                if isinstance(result, dict) and result.get("state"):
                    final_url = validate_url(result["url"])
                    note_id = extract_note_id(final_url)
                    if not note_id or requested_id and note_id != requested_id:
                        raise FetchError("NOTE_MISMATCH", "页面与请求的笔记 ID 不一致。")
                    extracted = time.perf_counter()
                    note = parse_state(result["state"], note_id, final_url)
                    if note["is_partial"]:
                        raise FetchError("INCOMPLETE_NOTE", "笔记正文尚未完整加载。")
                    if note["type"] != "video" and not note["images"]:
                        raise FetchError("INCOMPLETE_NOTE", "图文笔记没有可用的图片地址。")
                    note.update(fetch_method="webbridge", cache_hit=False,
                                timing={"queue_ms": round((acquired-started)*1000, 3), "prepare_ms": round((prepared-acquired)*1000, 3),
                                        "navigate_ms": round((navigated-prepared)*1000, 3), "extract_ms": round((extracted-navigated)*1000, 3),
                                        "parse_ms": round((time.perf_counter()-extracted)*1000, 3), "total_ms": round((time.perf_counter()-started)*1000, 3),
                                        "bridge_commands": self.bridge.command_count - commands_before, "fallback_reason": fallback_reason},
                                completeness={"description_present": isinstance(note["description"], str), "image_count": len(note["images"]),
                                              "source_image_count": result.get("imageCount"), "images_complete": len(note["images"]) == result.get("imageCount")})
                    if note["type"] != "video" and not note["completeness"]["images_complete"]:
                        raise FetchError("INCOMPLETE_NOTE", "部分图片数据缺失，未把它记为完整抓取。")
                    self._remember(cache_key, note, deadline)
                    return note
                self._remaining(deadline)
                if last_error:
                    last_error = None

    def _remember(self, key: str, note: dict, deadline: float):
        stored = deepcopy(note)
        self._remaining(deadline)
        self._cache[key] = (time.perf_counter(), stored)
        if len(self._cache) > 500:
            self._cache.pop(next(iter(self._cache)))
        try:
            self._remaining(deadline)
        except FetchError:
            self._cache.pop(key, None)
            raise

    def _fetch_document(self, url: str, note_id: str, deadline: float):
        """Ordinary same-origin document GET; no API signing or response cache."""
        budget_ms = max(1, int(self._remaining(deadline) * 1000 - 100))
        code = _DOCUMENT_JS.replace("__URL__", json.dumps(url)).replace("__TIMEOUT_MS__", str(budget_ms))
        response = self.bridge.evaluate(code, timeout=self._remaining(deadline))
        if not isinstance(response, dict):
            raise FetchError("DOCUMENT_FETCH_FAILED", "浏览器未返回有效页面数据。")
        if response.get("fallback"):
            return None
        if response.get("error"):
            raise FetchError(response["error"], response.get("message", "读取详情页失败。"))
        final_url = validate_url(response.get("url", ""))
        final_parts = urlsplit(final_url)
        if final_parts.path.rstrip("/") == "/login":
            raise FetchError("LOGIN_REQUIRED", "小红书已跳转登录页，请在浏览器中完成登录后重试。")
        restricted_query = final_parts.path.rstrip("/") == "/404" and "-510001" in parse_qs(final_parts.query).get("errorCode", [])
        if final_parts.path.startswith("/404/sec_") or restricted_query or response.get("status") in {401, 403, 429}:
            raise FetchError("ACCESS_RESTRICTED", "小红书限制当前访问，已停止。")
        if isinstance(response.get("status"), int) and response["status"] >= 500:
            raise FetchError("NETWORK_ERROR", "小红书暂时无法返回详情页。")
        final_id = extract_note_id(final_url)
        if final_id and final_id != note_id:
            raise FetchError("NOTE_MISMATCH", "返回页面与请求的笔记 ID 不一致。")
        if response.get("status") != 200 or not final_id:
            raise FetchError("NOTE_UNAVAILABLE", "分享链接未返回可访问的笔记详情页。")
        note = parse_html(response.get("html", ""), note_id, final_url)
        complete = note.get("completeness", {})
        if note["is_partial"] or (note["type"] != "video" and (not note["images"] or not complete.get("images_complete"))):
            raise FetchError("INCOMPLETE_NOTE", "详情页没有完整正文或全部图片数据。")
        return note, {"browser_request_ms": response.get("request_ms"), "document_chars": response.get("document_chars"), "transfer_chars": len(response.get("html", ""))}

    def fetch_notes(self, urls: list[str], *, timeout: float = 10, use_cache: bool = False) -> dict:
        _seconds(timeout)
        if not isinstance(urls, list) or not 1 <= len(urls) <= 50 or not all(isinstance(u, str) for u in urls):
            raise FetchError("INVALID_ARGUMENT", "urls 必须包含 1–50 条分享链接。")
        started = time.perf_counter()
        results = []
        seen = {}
        stopped = None
        for value in urls:
            try:
                url = extract_url(value)
                key = extract_note_id(url) or url
                if stopped:
                    item = {"ok": False, "error": {"code": "BATCH_STOPPED", "message": stopped}}
                elif key in seen:
                    item = {"ok": True, "note": deepcopy(seen[key]), "duplicate": True}
                else:
                    note = self.fetch_note(url, timeout=timeout, use_cache=use_cache)
                    seen[key] = note
                    item = {"ok": True, "note": note, "duplicate": False}
            except FetchError as exc:
                item = {"ok": False, **exc.as_dict()}
                if exc.code in {"ACCESS_RESTRICTED", "LOGIN_REQUIRED", "BRIDGE_UNAVAILABLE"}:
                    stopped = exc.message
            results.append(item)
        return {"results": results, "total": len(results), "succeeded": sum(r["ok"] for r in results), "elapsed_ms": round((time.perf_counter()-started)*1000, 3)}


_DISCOVER_JS = r"""(() => {
  if (location.pathname.replace(/\/$/,'') === '/login') return JSON.stringify({error:'LOGIN_REQUIRED',message:'请在浏览器中登录小红书后再试。'});
  if (location.pathname !== '/explore' || performance.timeOrigin === __PREVIOUS_ORIGIN__) return JSON.stringify([]);
  const feeds = window.__INITIAL_STATE__?.feed?.feeds;
  const types = new Map((Array.isArray(feeds) ? feeds : []).map(f => [f.id, f.noteCard?.type]));
  const notes = new Map();
  for (const a of document.querySelectorAll('a[href]')) {
    const u = new URL(a.href); const m = u.pathname.match(/^\/explore\/([0-9a-f]{24})$/i);
    if (!m || u.hostname !== 'www.xiaohongshu.com') continue;
    if (types.get(m[1]) === 'video') continue;
    const old = notes.get(m[1]);
    if (!old || u.searchParams.has('xsec_token')) notes.set(m[1], {note_id:m[1],url:a.href,title:a.innerText || old?.title || '',type:types.get(m[1]) || 'unknown'});
  }
  return JSON.stringify(Array.from(notes.values()).slice(0,__LIMIT__));
})()"""

_DOCUMENT_JS = r"""(async () => {
  const url = __URL__;
  if (location.origin !== new URL(url).origin) return JSON.stringify({fallback:true});
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), __TIMEOUT_MS__);
  try {
    const start = performance.now();
    const response = await fetch(url, {credentials:'same-origin',cache:'no-store',signal:controller.signal});
    const html = await response.text();
    const scripts = Array.from(html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi))
      .filter(m => m[1].includes('window.__INITIAL_STATE__')).map(m => m[0]);
    return JSON.stringify({html:scripts.length ? scripts.join('') : html,url:response.url,status:response.status,
                           document_chars:html.length,request_ms:performance.now()-start});
  } catch (error) {
    return JSON.stringify({error:error.name==='AbortError'?'TIMEOUT':'DOCUMENT_FETCH_FAILED',message:'浏览器读取详情页失败：'+error.name});
  } finally {clearTimeout(timer);}
})()"""

_NOTE_JS = r"""(async () => {
  const requestedId = __NOTE_ID__;
  const previousOrigin = __PREVIOUS_ORIGIN__;
  const stop = performance.now() + __WAIT_MS__;
  do {
    if (performance.timeOrigin === previousOrigin) {
      await new Promise(resolve => setTimeout(resolve, 40)); continue;
    }
    if (location.pathname.replace(/\/$/,'') === '/login')
      return JSON.stringify({error:'LOGIN_REQUIRED',message:'小红书已跳转登录页，请在浏览器中完成登录后重试。'});
    const m = location.pathname.match(/^\/(?:explore|discovery\/item)\/([0-9a-f]{24})\/?$/i);
    const id = requestedId || m?.[1];
    if (m && m[1] === id) {
      const map = window.__INITIAL_STATE__?.note?.noteDetailMap;
      let note = map?.[id]?.note || map?.[id];
      if (note && (!note.noteId || note.noteId === id) && typeof note.desc === 'string' && Array.isArray(note.imageList) && note.imageList.length) {
        const wanted = {};
        for (const k of ['noteId','title','desc','type','user','tagList','imageList','video','interactInfo','time','lastUpdateTime','ipLocation'])
          if (note[k] !== undefined) wanted[k] = note[k];
        return JSON.stringify({url:location.href,state:{note:{noteDetailMap:{[id]:{note:wanted}}}},imageCount:note.imageList.length});
      }
    }
    const text = document.body?.innerText || '';
    if (/\/404\/sec_|安全验证|访问过于频繁|访问频率过高|网络环境存在风险|验证后继续访问|异常访问/.test(location.pathname+' '+text))
      return JSON.stringify({error:'ACCESS_RESTRICTED',message:'页面要求安全验证或限制访问，已停止。'});
    if (/笔记已删除|笔记不存在|内容已删除|页面不存在|当前笔记暂时无法浏览/.test(text))
      return JSON.stringify({error:'NOTE_UNAVAILABLE',message:'笔记不存在或当前无法浏览。'});
    if (m && /登录后查看笔记|请登录后查看|请先登录/.test(text))
      return JSON.stringify({error:'LOGIN_REQUIRED',message:'请在浏览器中登录小红书后再试。'});
    await new Promise(resolve => setTimeout(resolve, 40));
  } while (performance.now() < stop);
  return JSON.stringify({pending:true,url:location.href});
})()"""

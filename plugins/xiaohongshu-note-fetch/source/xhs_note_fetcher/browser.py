"""Optional ordinary Playwright browser with a separate local login profile."""

from contextlib import suppress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit

from .errors import FetchError
from .links import extract_note_id, validate_url
from .transport import MAX_REDIRECTS, MAX_RESPONSE_BYTES, _check_timeout, _raise_http_status, _raise_page_url, _remaining

DEFAULT_PROFILE = str(Path(__file__).resolve().parent.parent / ".xhs-browser")
WORKER_GRACE_SECONDS = 5
_STATE_READY = """expectedId => {
    const state = window.__INITIAL_STATE__;
    const map = state && state.note && state.note.noteDetailMap;
    const match = location.pathname.match(/^\\/(?:explore|discovery\\/item)\\/([0-9a-f]{24})\\/?$/i);
    const noteId = expectedId || (match && match[1]);
    if (!noteId) return false;
    return !!(map && Object.entries(map).some(([key, value]) => {
        const note = value && (value.note || value);
        return note && String(note.noteId || note.id || key).toLowerCase() === noteId.toLowerCase()
            && (typeof note.desc === 'string' || typeof note.description === 'string')
            && (note.title || note.desc || note.description || (note.imageList && note.imageList.length) || note.video);
    }));
}"""


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright, Error, TimeoutError
    except ImportError as exc:
        raise FetchError(
            "DEPENDENCY_MISSING", "浏览器模式需要 Playwright，请先运行：python -m pip install -e \".[browser]\"。",
        ) from exc
    return sync_playwright, Error, TimeoutError


def _launch(playwright, profile_dir: str, headless: bool, deadline: float):
    profile = Path(profile_dir).expanduser().resolve()
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FetchError("BROWSER_ERROR", "无法创建浏览器登录目录，请选择可写目录。") from exc
    errors = []
    for channel in ("chrome", "msedge", None):
        options = {
            "headless": headless,
            "timeout": max(1, _remaining(deadline) * 1000),
            "locale": "zh-CN",
            "service_workers": "block",
        }
        if channel:
            options["channel"] = channel
        try:
            return playwright.chromium.launch_persistent_context(str(profile), **options)
        except Exception as exc:
            # Launch errors concern local executables/profiles; no note or cookie
            # was supplied to the browser yet. Keep one line per attempted engine.
            first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            errors.append({"browser": channel or "chromium", "reason": first_line[:350]})
    raise FetchError(
        "BROWSER_ERROR",
        "无法启动 Chrome、Edge 或 Chromium。请关闭占用本项目登录目录的浏览器，或运行 python -m playwright install chromium。",
        attempts=errors,
    )


def _safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(
        ">", "\\u003e"
    ).replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


class _NavigationGuard:
    """Validate each top-level hop before the browser requests its destination.

    Playwright route.continue_ follows redirects without invoking the handler
    again. Fetch each document without redirects, and convert a validated 3xx
    into a new navigation so that every hop is checked independently.
    """

    def __init__(self, deadline: float, allow_challenge: bool = False):
        self.deadline = deadline
        self.allow_challenge = allow_challenge
        self.error = None
        self.redirects = 0

    def __call__(self, route, request):
        try:
            if not request.is_navigation_request() or request.frame.parent_frame is not None:
                route.continue_()
                return
            current = validate_url(request.url)
            if not self.allow_challenge:
                _raise_page_url(current)
            response = route.fetch(max_redirects=0, timeout=max(1, _remaining(self.deadline) * 1000))
            status = response.status
            if status in {300, 301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise FetchError("NETWORK_ERROR", "浏览器页面返回了没有目标地址的跳转。", status=status)
                target = validate_url(urljoin(current, location))
                if urlsplit(current).scheme == "https" and urlsplit(target).scheme != "https":
                    raise FetchError("INVALID_URL", "已拒绝从 HTTPS 降级到 HTTP 的页面跳转。")
                self.redirects += 1
                if self.redirects > MAX_REDIRECTS:
                    raise FetchError("NETWORK_ERROR", "浏览器页面跳转次数过多。")
                body = "<!doctype html><meta charset='utf-8'><script>location.replace(" + _safe_json(target) + ")</script>"
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
                return
            if not self.allow_challenge or status not in {401, 403, 406, 418, 429, 461, 471}:
                _raise_http_status(status)
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                raise FetchError("RESPONSE_TOO_LARGE", "页面超过 12 MB 的抓取上限。")
            route.fulfill(response=response)
        except Exception as exc:
            self.error = exc if isinstance(exc, FetchError) else FetchError(
                "NETWORK_ERROR", "浏览器无法加载小红书页面，请检查网络或重新登录。"
            )
            with suppress(Exception):
                route.abort("blockedbyclient")


def _snapshot_html(page) -> str:
    html = page.content()
    # Page HTML can contain the server's old/empty state. Prepend a fresh copy so
    # the parser sees what the hydrated page actually displays, without executing
    # JavaScript from an untrusted HTTP response in the Python process.
    state = page.evaluate("""() => {
        try {
            const state = window.__INITIAL_STATE__;
            if (!state || !state.note) return null;
            return JSON.parse(JSON.stringify({note: state.note}));
        } catch (_) { return null; }
    }""")
    if state:
        html = "<script>window.__INITIAL_STATE__=" + _safe_json(state) + ";</script>" + html
    if len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise FetchError("RESPONSE_TOO_LARGE", "浏览器页面超过 12 MB 的抓取上限。")
    return html


def _close_context(context) -> None:
    # Do not make browser shutdown wait for in-flight page request handlers.
    with suppress(Exception):
        context.unroute_all(behavior="ignoreErrors")
    with suppress(Exception):
        context.close()


def _fetch_html_in_process(
    url: str, timeout: float = 30, profile_dir: str | None = None, headless: bool = True,
) -> tuple[str, str]:
    """Read one note using a persistent normal browser; never solve challenges."""
    _check_timeout(timeout)
    url = validate_url(url)
    sync_playwright, PlaywrightError, PlaywrightTimeout = _load_playwright()
    deadline = time.monotonic() + timeout
    with sync_playwright() as playwright:
        context = _launch(playwright, profile_dir or DEFAULT_PROFILE, headless, deadline)
        try:
            guard = _NavigationGuard(deadline, allow_challenge=not headless)
            context.route("**/*", guard)
            context.set_default_timeout(max(1, _remaining(deadline) * 1000))
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=max(1, _remaining(deadline) * 1000))
            except PlaywrightError:
                if guard.error:
                    raise guard.error
                # A validated short-link redirect can replace the document while
                # goto is waiting for the previous document's DOMContentLoaded.
                if not guard.redirects:
                    raise
            try:
                wait_seconds = min(10, _remaining(deadline)) if headless else _remaining(deadline)
                page.wait_for_function(_STATE_READY, arg=extract_note_id(url), timeout=max(1, wait_seconds * 1000))
            except PlaywrightTimeout:
                # Return the actual DOM for metadata/error-page classification.
                pass
            if guard.error:
                raise guard.error
            final_url = validate_url(page.url)
            _raise_page_url(final_url)
            return _snapshot_html(page), final_url
        except FetchError:
            raise
        except PlaywrightTimeout as exc:
            raise FetchError("NETWORK_ERROR", "浏览器加载超时，请稍后重试或增加 timeout。") from exc
        except PlaywrightError as exc:
            raise FetchError("BROWSER_ERROR", "浏览器读取失败，请关闭占用登录目录的窗口并重试。") from exc
        finally:
            _close_context(context)


def _login_in_process(profile_dir: str, timeout: float = 180) -> None:
    """Open a visible browser for manual login; close it yourself when finished.

    The profile is saved locally on close or after the timeout. This operation
    does not assert that login succeeded and never prints cookie contents.
    """
    _check_timeout(timeout)
    sync_playwright, PlaywrightError, _ = _load_playwright()
    deadline = time.monotonic() + timeout
    with sync_playwright() as playwright:
        context = _launch(playwright, profile_dir, False, deadline)
        try:
            guard = _NavigationGuard(deadline, allow_challenge=True)
            context.route("**/*", guard)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded", timeout=min(30000, max(1, _remaining(deadline) * 1000)))
            while time.monotonic() < deadline:
                if not context.pages or all(item.is_closed() for item in context.pages):
                    return
                if guard.error:
                    raise guard.error
                try:
                    context.pages[0].wait_for_timeout(min(500, max(1, (deadline - time.monotonic()) * 1000)))
                except PlaywrightError:
                    if not context.pages:
                        return
                    raise
        except FetchError:
            raise
        except PlaywrightError as exc:
            if guard.error:
                raise guard.error
            raise FetchError("BROWSER_ERROR", "登录窗口未能正常打开或已中断，请重新运行 login。") from exc
        finally:
            _close_context(context)


def _terminate_worker(process) -> None:
    """Terminate only the process tree/session created for this worker."""
    if os.name == "nt":
        # A browser driver may be stuck inside native shutdown. Terminating only
        # Python would leave its driver/Chromium descendants and profile lock.
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    else:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        with suppress(OSError):
            process.kill()
    # Do not wait indefinitely if a crashed native descendant retained a pipe.
    try:
        process.communicate(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        # Windows communicate uses daemon reader threads. Closing a buffered pipe
        # still held by one of those threads can itself block indefinitely.
        return
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            with suppress(OSError):
                stream.close()


def _run_worker(payload: dict, timeout: float) -> dict:
    """Run Playwright outside the caller so native hangs have a hard boundary."""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    package_root = str(Path(__file__).resolve().parent.parent)
    environment["PYTHONPATH"] = package_root + (
        os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
    )
    options = {
        "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "text": True, "encoding": "utf-8", "env": environment,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "xhs_note_fetcher.browser", "--worker"], **options,
        )
    except OSError as exc:
        raise FetchError("BROWSER_ERROR", "无法启动独立浏览器工作进程，请检查 Python 环境。") from exc
    try:
        stdout, _stderr = process.communicate(
            input=json.dumps(payload, ensure_ascii=False, allow_nan=False),
            timeout=timeout + WORKER_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_worker(process)
        raise FetchError(
            "NETWORK_ERROR", "浏览器超过等待时限，已终止本次抓取的浏览器进程。请重新登录后重试。",
            timeout_seconds=timeout, cleanup_grace_seconds=WORKER_GRACE_SECONDS,
        ) from exc
    except BaseException:
        # Ctrl+C must not strand this invocation's browser or locked profile.
        _terminate_worker(process)
        raise
    try:
        result = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise FetchError("BROWSER_ERROR", "浏览器工作进程未返回有效结果，请检查浏览器安装后重试。") from exc
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise FetchError("BROWSER_ERROR", "浏览器工作进程返回了无效结果。")
    if result["ok"] is False:
        error = result.get("error") or {}
        if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
            raise FetchError("BROWSER_ERROR", "浏览器工作进程返回了无效错误。")
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        raise FetchError(error["code"], error["message"], **details)
    if process.returncode != 0:
        raise FetchError("BROWSER_ERROR", "浏览器工作进程意外退出，请重新运行。")
    return result


def fetch_html(
    url: str, timeout: float = 30, profile_dir: str | None = None, headless: bool = True,
) -> tuple[str, str]:
    """Read one note with a hard timeout, including native browser shutdown.

    The worker gets timeout seconds for fetching plus five seconds to exit.
    If it hangs, only this invocation's process tree is terminated; terminating
    that tree and draining its pipes have separate short bounded cleanup waits.
    """
    _check_timeout(timeout)
    url = validate_url(url)
    result = _run_worker({
        "operation": "fetch", "url": url, "timeout": timeout,
        "profile_dir": str(Path(profile_dir or DEFAULT_PROFILE).expanduser().resolve()),
        "headless": headless,
    }, timeout)
    if not isinstance(result.get("html"), str) or not isinstance(result.get("url"), str):
        raise FetchError("BROWSER_ERROR", "浏览器工作进程返回的页面无效。")
    return result["html"], validate_url(result["url"])


def login(profile_dir: str, timeout: float = 180) -> None:
    """Open a supervised visible browser for manual login and local profile save.

    Close the browser after logging in, or wait for timeout. Saving a profile
    does not by itself assert that the site's authentication succeeded.
    """
    _check_timeout(timeout)
    _run_worker({
        "operation": "login", "timeout": timeout,
        "profile_dir": str(Path(profile_dir).expanduser().resolve()),
    }, timeout)


def _worker_main() -> int:
    """Private stdin/stdout protocol; never echo URLs or secrets in diagnostics."""
    # Piped Windows Python otherwise inherits the system code page. Keep this
    # protocol UTF-8 even when the private worker is invoked without its parent.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        request_text = sys.stdin.read(1024 * 1024 + 1)
        if len(request_text) > 1024 * 1024:
            raise FetchError("INVALID_ARGUMENT", "浏览器工作请求过长。")
        request = json.loads(request_text)
        if not isinstance(request, dict):
            raise FetchError("INVALID_ARGUMENT", "浏览器工作请求无效。")
        operation = request.pop("operation", None)
        if operation == "fetch":
            html, final_url = _fetch_html_in_process(**request)
            result = {"ok": True, "html": html, "url": final_url}
        elif operation == "login":
            _login_in_process(**request)
            result = {"ok": True}
        else:
            raise FetchError("INVALID_ARGUMENT", "浏览器工作类型无效。")
    except FetchError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    except Exception:
        result = {"ok": False, "error": {"code": "BROWSER_ERROR", "message": "浏览器工作进程无法完成请求。", "details": {}}}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False))
    sys.stdout.flush()
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("This module is an internal worker. Use python -m xhs_note_fetcher.")
    raise SystemExit(_worker_main())

"""Parse note data already present in HTML or a browser's page state.

This module never evaluates JavaScript. It accepts JSON with the common
JavaScript non-finite/undefined literals replaced outside strings only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
import json
import math
import re
from urllib.parse import parse_qs, urlsplit

from .errors import FetchError


_ASSIGNMENT = re.compile(r"(?:window\s*\.\s*)?__INITIAL_STATE__\s*=\s*")
_NONFINITE_TOKEN = re.compile(r"(?:[+-]?Infinity|undefined|NaN)\b")
_EMPTY_MAP = re.compile(r"new\s+Map\s*\(\s*\[\s*\]\s*\)")
_NOTE_PATH = re.compile(r"/(?:explore|discovery/item)/([a-zA-Z0-9]+)(?:/|$)")
_ACCESS_PHRASES = (
    "请登录后查看", "登录后查看笔记", "请先登录", "安全验证", "访问过于频繁",
    "访问频率过高", "网络环境存在风险", "验证后继续访问", "完成验证后继续",
    "异常访问", "访问受限", "captcha", "access denied",
)
_UNAVAILABLE_PHRASES = (
    "笔记已删除", "笔记已被删除", "笔记不存在", "内容已删除", "笔记无法展示",
    "你访问的页面不见了", "你访问的笔记不见了", "该内容无法展示", "页面不存在",
    "笔记暂时无法展示", "内容暂时无法展示", "page not found",
)


class _Page(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self.canonical: list[str] = []
        self.visible: list[str] = []
        self.title: list[str] = []
        self._script: list[str] | None = None
        self._hidden = 0
        self._in_title = False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self._script = []
        elif tag in ("style", "noscript"):
            self._hidden += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            value = attrs.get("content")
            if key and value:
                self.meta.setdefault(key, []).append(value.strip())
        elif tag == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            if attrs.get("href"):
                self.canonical.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None
        elif tag in ("style", "noscript"):
            self._hidden = max(0, self._hidden - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif not self._hidden:
            self.visible.append(data)
            if self._in_title:
                self.title.append(data)


def _object_literal(script: str, start: int) -> str:
    if start >= len(script) or script[start] != "{":
        raise ValueError("initial state is not an object literal")
    depth = 0
    quote = None
    escaped = False
    for pos in range(start, len(script)):
        char = script[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return script[start:pos + 1]
    raise ValueError("unterminated initial-state object")


def _json_safe_literals(source: str) -> str:
    result = []
    pos = 0
    while pos < len(source):
        char = source[pos]
        if char in ('"', "'"):
            start = pos
            quote = char
            pos += 1
            while pos < len(source):
                if source[pos] == "\\":
                    pos += 2
                elif source[pos] == quote:
                    pos += 1
                    break
                else:
                    pos += 1
            result.append(source[start:pos])
        else:
            # Token boundaries prevent changes to longer identifiers. Empty Map
            # appears in some page states; recognize this one literal, never run it.
            token = _NONFINITE_TOKEN.match(source, pos)
            empty_map = _EMPTY_MAP.match(source, pos)
            if token:
                result.append("null")
                pos += len(token[0])
            elif empty_map:
                result.append("{}")
                pos += len(empty_map[0])
            elif char.isalpha() or char in "_$":
                end = pos + 1
                while end < len(source) and (source[end].isalnum() or source[end] in "_$"):
                    end += 1
                result.append(source[pos:end])
                pos = end
            else:
                result.append(char)
                pos += 1
    return "".join(result)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _first(mapping: dict, *names):
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _string(value) -> str | None:
    return value if isinstance(value, str) else None


def _url(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = unescape(value.strip())
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
        if parts.scheme in ("https", "http") and parts.hostname and not parts.username:
            return value
    except ValueError:
        pass
    return None


def _positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
        return number if number > 0 and str(number) == str(value).strip() else None
    except (ValueError, TypeError, OverflowError):
        return None


def _count(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or int(value) != value:
            return None
        return int(value)
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[,，\s]", "", value).lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(万|亿|w|k|m)?(\+)?", cleaned)
    if not match:
        return None
    try:
        number = Decimal(match[1]) * {None: 1, "万": 10000, "亿": 100000000,
                                     "w": 10000, "k": 1000, "m": 1000000}[match[2]]
        return int(number) if number == number.to_integral_value() else None
    except (InvalidOperation, ValueError):
        return None


def _timestamp(value) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and not re.fullmatch(r"\d+(?:\.\d+)?", value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # Do not invent a timezone for a string that did not contain one.
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if parsed.tzinfo else value
        except ValueError:
            return None
    try:
        number = float(value)
        if abs(number) >= 100000000000:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _images(note: dict) -> list[dict]:
    result = []
    seen = set()
    items = _first(note, "imageList", "image_list", "images")
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            item = {"url": item}
        item = _mapping(item)
        url = next((_url(item.get(key)) for key in ("urlDefault", "url", "urlPre") if _url(item.get(key))), None)
        if not url:
            infos = item.get("infoList")
            infos = [info for info in infos if isinstance(info, dict)] if isinstance(infos, list) else []
            infos.sort(key=lambda info: info.get("imageScene") != "WB_DFT")
            url = next((_url(info.get("url")) for info in infos if _url(info.get("url"))), None)
        if url and url not in seen:
            seen.add(url)
            result.append({"url": url, "width": _positive_int(item.get("width")),
                           "height": _positive_int(item.get("height"))})
    return result


def _video(note: dict) -> dict | None:
    value = note.get("video")
    if not isinstance(value, dict) and note.get("type") != "video":
        return None
    urls = []
    url_keys = {"masterUrl", "master_url", "mediaUrl", "media_url", "backupUrls", "backup_urls", "url", "urls", "urlList"}

    def visit(node, depth=0):
        if depth > 12:
            return
        if isinstance(node, dict):
            for key, child in node.items():
                if key in url_keys:
                    candidates = child if isinstance(child, list) else [child]
                    for candidate in candidates:
                        url = _url(candidate)
                        if url and url not in urls:
                            urls.append(url)
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)

    visit(value)
    return {"urls": urls}


def _tags(note: dict) -> list[dict]:
    result = []
    seen = set()
    items = _first(note, "tagList", "tag_list", "tags")
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            item = {"name": item}
        item = _mapping(item)
        tag = {"id": _string(_first(item, "id", "tagId", "tag_id")), "name": _string(item.get("name"))}
        key = (tag["id"], tag["name"])
        if any(key) and key not in seen:
            seen.add(key)
            result.append(tag)
    return result


def _select_note(state: dict, note_id: str) -> dict | None:
    section = _mapping(state.get("note"))
    maps = [section.get("noteDetailMap"), state.get("noteDetailMap")]
    for detail_map in maps:
        if not isinstance(detail_map, dict):
            continue
        entry = _mapping(detail_map.get(note_id))
        candidate = _mapping(entry.get("note")) if "note" in entry else entry
        candidate_id = _first(candidate, "noteId", "note_id", "id")
        if candidate and (candidate_id is None or str(candidate_id) == note_id):
            return candidate
        # Some frontends use a composite map key. Only accept an explicit ID match.
        for entry in detail_map.values():
            entry = _mapping(entry)
            candidate = _mapping(entry.get("note")) if "note" in entry else entry
            if str(_first(candidate, "noteId", "note_id", "id")) == note_id:
                return candidate
    for candidate in (section.get("note"), section):
        candidate = _mapping(candidate)
        if str(_first(candidate, "noteId", "note_id", "id")) == note_id:
            return candidate
    return None


def _result(note: dict, note_id: str, source_url: str, method: str) -> dict:
    user = _mapping(_first(note, "user", "author"))
    user_id = _string(_first(user, "userId", "user_id", "id"))
    raw_stats = _mapping(_first(note, "interactInfo", "interact_info", "stats"))
    aliases = {
        "likes": ("likedCount", "likeCount", "liked_count", "like_count", "likes"),
        "collects": ("collectedCount", "collectCount", "collected_count", "collect_count", "collects"),
        "comments": ("commentCount", "comment_count", "comments"),
        "shares": ("shareCount", "sharedCount", "share_count", "shares"),
    }
    counts = {key: _first(raw_stats, *names) for key, names in aliases.items()}
    # Preserve only JSON scalar raw counts; arbitrary nested objects are not stats.
    counts = {key: value if isinstance(value, (str, int, float)) and not isinstance(value, bool) and
              (not isinstance(value, float) or math.isfinite(value)) else None for key, value in counts.items()}
    description = next((note[key] for key in ("desc", "description") if isinstance(note.get(key), str)), None)
    is_partial = description is None or method == "metadata"
    warnings = []
    if description is None:
        warnings.append("页面状态未提供正文数据，当前结果不完整；正文缺失不等于笔记没有正文。")
    if any(isinstance(value, str) and re.search(r"[万亿wkm+]", value, re.I) for value in counts.values()):
        warnings.append("部分互动量为平台缩略显示值；归一化数字可能是近似值或下限，原值见 stats_raw。")
    video = _video(note)
    images = _images(note)
    source_images = _first(note, "imageList", "image_list", "images")
    source_image_count = len(source_images) if isinstance(source_images, list) else None
    if video is not None and not video["urls"]:
        warnings.append("笔记包含视频，但页面数据未提供可用的视频地址。")
    return {
        "note_id": note_id,
        "title": _string(note.get("title")),
        "description": description,
        "type": _string(note.get("type")),
        "author": {"user_id": user_id, "nickname": _string(_first(user, "nickname", "nickName")),
                   "avatar": _url(_first(user, "avatar", "avatarUrl", "avatar_url")),
                   "profile_url": f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id and re.fullmatch(r"[A-Za-z0-9_-]+", user_id) else None},
        "tags": _tags(note),
        "images": images,
        "completeness": {"description_present": description is not None,
                         "image_count": len(images), "source_image_count": source_image_count,
                         "images_complete": source_image_count is not None and len(images) == source_image_count},
        "video": video,
        "stats": {key: _count(value) for key, value in counts.items()},
        "stats_raw": counts,
        "published_at": _timestamp(_first(note, "time", "createTime", "create_time", "published_at")),
        "updated_at": _timestamp(_first(note, "lastUpdateTime", "updateTime", "update_time", "updated_at")),
        "ip_location": _string(_first(note, "ipLocation", "ip_location")),
        "source_url": source_url,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "extraction_method": method,
        "is_partial": is_partial,
        "warnings": warnings,
    }


def _parse_state(state: dict, note_id: str, source_url: str, method: str) -> dict:
    if not isinstance(state, dict):
        raise FetchError("PARSE_ERROR", "页面状态不是有效对象。", note_id=note_id)
    note = _select_note(state, note_id)
    if not note:
        raise FetchError("PARSE_ERROR", "页面状态中没有找到指定笔记的详情。", note_id=note_id)
    result = _result(note, note_id, source_url, method)
    if not (result["title"] or result["description"] or result["images"] or
            result["video"] and result["video"]["urls"]):
        raise FetchError("PARSE_ERROR", "指定笔记的页面数据为空或尚未加载。", note_id=note_id)
    return result


def parse_state(state: dict, note_id: str, source_url: str) -> dict:
    """Extract only the requested note from a browser's serialized page state."""
    return _parse_state(state, note_id, source_url, "browser_state")


def _same_note_url(value: str, note_id: str) -> bool:
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        match = _NOTE_PATH.search(parts.path)
        return (host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com")) and bool(match and match[1] == note_id)
    except ValueError:
        return False


def _restricted_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if host != "xiaohongshu.com" and not host.endswith(".xiaohongshu.com"):
            return False
        return parts.path.startswith("/404/sec_") or (
            parts.path.rstrip("/") == "/404" and
            "-510001" in parse_qs(parts.query).get("errorCode", [])
        )
    except ValueError:
        return False


def _metadata(page: _Page, note_id: str, source_url: str) -> dict | None:
    urls = page.canonical + page.meta.get("og:url", [])
    if not any(_same_note_url(url, note_id) for url in urls):
        return None
    titles = page.meta.get("og:title", [])
    if not titles:
        return None
    title = re.sub(r"\s*[-_|–—]\s*小红书\s*$", "", titles[0]).strip()
    if not title or title in {"小红书", "登录", "安全验证", "404", "发现", "你的生活指南", "小红书 - 你的生活指南"}:
        return None
    descriptions = page.meta.get("og:description") or page.meta.get("description") or []
    images = [url for value in page.meta.get("og:image", []) if (url := _url(value))]
    if not descriptions and not images:
        return None
    note = {"title": title, "desc": descriptions[0] if descriptions else None,
            "imageList": [{"url": url} for url in images]}
    result = _result(note, note_id, source_url, "metadata")
    result["warnings"].append("仅提取到页面分享元数据，正文可能截断；作者、互动量等详情未获取，不能视为完整笔记。")
    return result


def parse_html(html: str, note_id: str, source_url: str) -> dict:
    """Parse initial state, or explicitly return partial metadata when identifiable.

    Raises FetchError with ACCESS_RESTRICTED, NOTE_UNAVAILABLE, or PARSE_ERROR.
    No external requests are made and no page JavaScript is executed.
    """
    if not isinstance(html, str) or not html.strip():
        raise FetchError("PARSE_ERROR", "收到的页面内容为空。", note_id=note_id)
    try:
        page = _Page(html)
    except (ValueError, AssertionError) as exc:
        raise FetchError("PARSE_ERROR", "无法读取页面 HTML。", note_id=note_id) from exc
    partial = None
    for script in page.scripts:
        for match in _ASSIGNMENT.finditer(script):
            try:
                literal = _object_literal(script, match.end())
                state = json.loads(_json_safe_literals(literal))
                result = _parse_state(state, note_id, source_url, "initial_state")
                if not result["is_partial"]:
                    return result
                if partial is None:
                    partial = result
            except (ValueError, TypeError, RecursionError, FetchError):
                continue
    visible = " ".join(page.visible).lower()
    # Ignore phrases contained only in scripts: application bundles routinely
    # contain login/error translations even on fully accessible note pages.
    page_urls = [source_url] + page.canonical + page.meta.get("og:url", [])
    if any(_restricted_url(url) for url in page_urls):
        raise FetchError("ACCESS_RESTRICTED", "页面要求登录、安全验证或提示访问受限。", note_id=note_id)
    if partial is not None:
        return partial
    if any(phrase in visible for phrase in _ACCESS_PHRASES):
        raise FetchError("ACCESS_RESTRICTED", "页面要求登录、安全验证或提示访问受限。", note_id=note_id)
    if any(phrase in visible for phrase in _UNAVAILABLE_PHRASES):
        raise FetchError("NOTE_UNAVAILABLE", "页面提示笔记不存在、已删除或无法展示。", note_id=note_id)
    result = _metadata(page, note_id, source_url)
    if result is not None:
        return result
    raise FetchError("PARSE_ERROR", "未找到指定笔记的可用详情；页面可能未加载完成或结构已变化。", note_id=note_id)

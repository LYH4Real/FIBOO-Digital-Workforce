"""Single-note orchestration shared by Python callers and the CLI."""

import math
from pathlib import Path

from . import browser, transport
from .errors import FetchError
from .links import extract_note_id, extract_url
from .parser import parse_html

DEFAULT_PROFILE = str(Path(__file__).resolve().parent.parent / ".xhs-browser")
_FALLBACK_CODES = {"ACCESS_RESTRICTED", "PARSE_ERROR", "NETWORK_ERROR"}


def _parse(html: str, final_url: str, initial_url: str) -> dict:
    requested_id = extract_note_id(initial_url)
    final_id = extract_note_id(final_url)
    if requested_id and final_id and requested_id != final_id:
        raise FetchError("NOTE_UNAVAILABLE", "页面跳转到了另一篇笔记，未返回错误的笔记内容。")
    note_id = requested_id or final_id
    if not note_id:
        raise FetchError("ACCESS_RESTRICTED", "分享链接未到达笔记详情页，请重新复制完整分享链接，或先登录。")
    return parse_html(html, note_id, final_url)


def fetch_note(
    share_text: str, *, mode: str = "auto", timeout: float = 30,
    cookie: str | None = None, profile_dir: str | None = None,
    headless: bool = True,
) -> dict:
    """Return a normalized note, or raise FetchError with an actionable code.

    auto first requests HTML, then tries a locally installed browser on an
    access/parse/network failure or metadata-only result. No background retries.
    cookie is used only by HTTP; browser mode uses its own persistent profile.
    """
    if mode not in {"auto", "http", "browser"}:
        raise FetchError("INVALID_ARGUMENT", "mode 必须是 auto、http 或 browser。")
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise FetchError("INVALID_ARGUMENT", "timeout 必须是大于 0 的有限秒数。")
    url = extract_url(share_text)
    profile = profile_dir or DEFAULT_PROFILE
    http_error = None
    partial = None
    if mode != "browser":
        try:
            html, final_url = transport.fetch_html(url, timeout=timeout, cookie=cookie)
            result = _parse(html, final_url, url)
            if mode == "http" or (result.get("extraction_method") != "metadata" and not result.get("is_partial")):
                return result
            partial = result
        except FetchError as exc:
            if mode == "http" or exc.code not in _FALLBACK_CODES:
                raise
            http_error = exc
    try:
        html, final_url = browser.fetch_html(url, timeout=timeout, profile_dir=profile, headless=headless)
        result = _parse(html, final_url, url)
        result["fetch_method"] = "browser"
        return result
    except FetchError as exc:
        if partial is not None:
            partial.setdefault("warnings", []).append(
                f"浏览器补充抓取未完成（{exc.code}），当前内容不完整，不能作为完整笔记。"
            )
            return partial
        if http_error is not None:
            raise FetchError(
                http_error.code, http_error.message,
                browser_error={"code": exc.code, "message": exc.message},
                hint="可先安装浏览器依赖并运行 login，再使用 --mode browser；请保留完整分享链接。",
            ) from exc
        raise

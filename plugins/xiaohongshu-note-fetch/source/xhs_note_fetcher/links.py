"""Parse share text while retaining the complete, signed note URL."""

import re
from urllib.parse import urlsplit, urlunsplit

from .errors import FetchError

ALLOWED_HOSTS = frozenset({
    "xhslink.com", "www.xhslink.com", "xiaohongshu.com", "www.xiaohongshu.com",
})
SHORT_LINK_HOSTS = frozenset({"xhslink.com", "www.xhslink.com"})
NOTE_HOSTS = ALLOWED_HOSTS - SHORT_LINK_HOSTS
_NOTE_PATH = re.compile(r"^/(?:explore|discovery/item)/([0-9a-fA-F]{24})/?$")
_URL_IN_TEXT = re.compile(
    r"(?:https?://[^\s<>\"'，。；！？、（）【】《》]+|"
    r"(?<![\w.@/-])(?:www\.)?(?:xhslink\.com|xiaohongshu\.com)/[^\s<>\"'，。；！？、（）【】《》]+)",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ".,;!?)）]}〉>。；，！？、\u200b"


def validate_url(url: str) -> str:
    """Validate an HTTP(S) page URL and normalize only scheme/host/default port.

    Paths and query parameters are not decoded or reordered. This validator is
    also used for intermediate redirects; extract_url enforces note paths.
    """
    if not isinstance(url, str) or not url.strip():
        raise FetchError("INVALID_URL", "请输入小红书分享链接或包含链接的分享文案。")
    candidate = url.strip()
    if len(candidate) > 32768 or "\\" in candidate or any(ord(c) < 33 or ord(c) == 127 for c in candidate):
        raise FetchError("INVALID_URL", "链接包含无效字符，或链接过长。")
    try:
        parsed = urlsplit(candidate)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port
        if (
            scheme not in {"http", "https"}
            or host not in ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443 if scheme == "https" else 80}
            or parsed.netloc.endswith(":")
        ):
            raise ValueError("Unsupported origin")
    except (ValueError, UnicodeError) as exc:
        raise FetchError("INVALID_URL", "仅支持 xhslink.com 和 xiaohongshu.com 的普通 HTTP(S) 链接。") from exc
    return urlunsplit((scheme, host, parsed.path or "/", parsed.query, parsed.fragment))


def extract_note_id(url: str) -> str | None:
    """Return a 24-hex note ID from a validated note path, if present."""
    checked = validate_url(url)
    parsed = urlsplit(checked)
    match = _NOTE_PATH.fullmatch(parsed.path) if parsed.hostname in NOTE_HOSTS else None
    return match.group(1).lower() if match else None


def extract_url(text: str) -> str:
    """Find the first supported note URL in a URL or copied share message."""
    if not isinstance(text, str) or not text.strip():
        raise FetchError("INVALID_URL", "请输入小红书分享链接或包含链接的分享文案。")
    for match in _URL_IN_TEXT.finditer(text):
        candidate = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if not candidate.lower().startswith(("https://", "http://")):
            candidate = "https://" + candidate
        try:
            candidate = validate_url(candidate)
        except FetchError:
            continue
        parsed = urlsplit(candidate)
        if extract_note_id(candidate) or (parsed.hostname in SHORT_LINK_HOSTS and parsed.path.strip("/")):
            return candidate
    raise FetchError(
        "INVALID_URL",
        "未找到有效笔记链接。请复制完整 xhslink.com 分享链接，或 /explore/、/discovery/item/ 笔记详情链接。",
    )

"""Small standard-library HTTP transport with explicit redirect boundaries."""

import gzip
from http.client import HTTPException
import io
import math
import re
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import FetchError
from .links import NOTE_HOSTS, validate_url

MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_REDIRECTS = 8
_REDIRECT_CODES = {300, 301, 302, 303, 307, 308}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FetchError("NETWORK_ERROR", "抓取超时，请稍后重试或增加 timeout。")
    return remaining


def _check_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise FetchError("INVALID_ARGUMENT", "timeout 必须是大于 0 的有限秒数。")


def _bounded_read(response, deadline: float) -> bytes:
    length = response.headers.get("Content-Length")
    expected_length = None
    if length:
        try:
            expected_length = int(length)
            if expected_length > MAX_RESPONSE_BYTES:
                raise FetchError("RESPONSE_TOO_LARGE", "页面超过 12 MB 的抓取上限。")
        except ValueError:
            pass
    pieces = []
    total = 0
    # HTTPResponse.read1 returns after a single buffered/socket read, allowing
    # deadline checks even when a server slowly trickles its response body.
    reader = getattr(response, "read1", response.read)
    while True:
        remaining = _remaining(deadline)
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(remaining)
        piece = reader(min(65536, MAX_RESPONSE_BYTES + 1 - total))
        _remaining(deadline)
        if not piece:
            break
        pieces.append(piece)
        total += len(piece)
        if total > MAX_RESPONSE_BYTES:
            raise FetchError("RESPONSE_TOO_LARGE", "页面超过 12 MB 的抓取上限。")
    if expected_length is not None and expected_length >= 0 and total < expected_length:
        raise FetchError("NETWORK_ERROR", "服务器中断了页面传输，请稍后重试。")
    return b"".join(pieces)


def _decode_body(body: bytes, headers) -> str:
    encoding = headers.get("Content-Encoding", "").strip().lower()
    try:
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as source:
                body = source.read(MAX_RESPONSE_BYTES + 1)
        elif encoding == "deflate":
            decoder = zlib.decompressobj()
            body = decoder.decompress(body, MAX_RESPONSE_BYTES + 1)
            if not decoder.eof and len(body) <= MAX_RESPONSE_BYTES:
                raise FetchError("NETWORK_ERROR", "服务器返回了不完整的压缩页面。")
        elif encoding not in {"", "identity"}:
            raise FetchError("NETWORK_ERROR", "服务器返回了不支持的页面压缩格式。")
    except (OSError, EOFError, zlib.error) as exc:
        raise FetchError("NETWORK_ERROR", "服务器返回的压缩页面无法解码。") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise FetchError("RESPONSE_TOO_LARGE", "解压后的页面超过 12 MB 的抓取上限。")
    content_type = headers.get("Content-Type", "")
    match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", content_type, re.IGNORECASE)
    charset = match.group(1) if match else "utf-8-sig"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8-sig", errors="replace")


def _request_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((
        parsed.scheme, parsed.netloc,
        quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
        quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~[]"), "",
    ))


def _raise_http_status(status: int) -> None:
    if status in {401, 403, 406, 418, 429, 461, 471}:
        raise FetchError("ACCESS_RESTRICTED", "小红书要求登录或访问验证；请在浏览器中正常登录后重试。", status=status)
    if status in {404, 410}:
        raise FetchError("NOTE_UNAVAILABLE", "笔记不存在、已删除或当前不可见。", status=status)
    if status >= 400:
        raise FetchError("NETWORK_ERROR", "小红书服务器未能返回页面。", status=status)


def _raise_page_url(url: str) -> None:
    """Some access challenges return HTTP 200 at an error-page URL."""
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if parsed.path.startswith("/404/sec_") or (
        parsed.path.rstrip("/") == "/404" and "-510001" in query.get("errorCode", [])
    ):
        raise FetchError("ACCESS_RESTRICTED", "小红书将请求转到了访问验证页面，请在浏览器中正常登录或验证后重试。")


def fetch_html(url: str, timeout: float = 30, cookie: str | None = None) -> tuple[str, str]:
    """Fetch one HTML page and its final validated URL, with an overall deadline.

    The supplied cookie is sent only to HTTPS Xiaohongshu note hosts. Redirect
    requests are rebuilt without inheriting credentials from a previous host.
    """
    _check_timeout(timeout)
    current = validate_url(url)
    if cookie is not None and (not isinstance(cookie, str) or any(ord(c) < 32 or ord(c) == 127 for c in cookie)):
        raise FetchError("INVALID_ARGUMENT", "Cookie 必须是单行请求头内容。")
    if cookie is not None:
        try:
            cookie.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise FetchError("INVALID_ARGUMENT", "Cookie 中有无效字符，请复制原始 Cookie 请求头。") from exc
    deadline = time.monotonic() + timeout
    opener = build_opener(_NoRedirect())
    seen = set()
    for hop in range(MAX_REDIRECTS + 1):
        current = validate_url(current)
        _raise_page_url(current)
        request_url = _request_url(current)
        if request_url in seen:
            raise FetchError("NETWORK_ERROR", "分享链接发生循环跳转，请重新复制分享链接。")
        seen.add(request_url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept-Encoding": "identity",
        }
        parsed = urlsplit(current)
        if cookie and parsed.scheme == "https" and parsed.hostname in NOTE_HOSTS:
            headers["Cookie"] = cookie
        response = None
        try:
            request = Request(request_url, headers=headers, method="GET")
            try:
                response = opener.open(request, timeout=_remaining(deadline))
            except HTTPError as exc:
                response = exc
            status = response.getcode() or 200
            if status in _REDIRECT_CODES:
                location = response.headers.get("Location")
                if not location:
                    raise FetchError("NETWORK_ERROR", "分享链接返回了没有目标地址的跳转。", status=status)
                if hop >= MAX_REDIRECTS:
                    raise FetchError("NETWORK_ERROR", "分享链接跳转次数过多。")
                target = validate_url(urljoin(current, location))
                if parsed.scheme == "https" and urlsplit(target).scheme != "https":
                    raise FetchError("INVALID_URL", "已拒绝从 HTTPS 降级到 HTTP 的分享链接跳转。")
                current = target
                continue
            _raise_http_status(status)
            body = _bounded_read(response, deadline)
            html = _decode_body(body, response.headers)
            _remaining(deadline)
            return html, current
        except FetchError:
            raise
        except (URLError, OSError, HTTPException, ValueError) as exc:
            # Never include request headers, tokens or raw cookie values in errors.
            raise FetchError("NETWORK_ERROR", "无法连接小红书或读取页面，请检查网络后重试。") from exc
        finally:
            if response is not None:
                response.close()
    raise FetchError("NETWORK_ERROR", "分享链接跳转次数过多。")

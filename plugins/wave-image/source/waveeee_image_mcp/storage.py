from __future__ import annotations

import base64
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable
from . import __version__

MAX_IMAGE_BYTES = 64 * 1024 * 1024


def image_extension(raw: bytes) -> str:
    """Identify the returned container, never the requested format or URL suffix.

    These are header/container checks, not a visual or full pixel decode review.
    """
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("图像内容为空或不是字节数据")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("单张图片超过 64 MiB 保存上限")
    if (len(raw) >= 45 and raw.startswith(b"\x89PNG\r\n\x1a\n")
            and raw[8:16] == b"\x00\x00\x00\rIHDR"
            and int.from_bytes(raw[16:20], "big") > 0
            and int.from_bytes(raw[20:24], "big") > 0
            and b"\x00\x00\x00\x00IEND\xaeB`\x82" in raw[33:]):
        return ".png"
    if len(raw) >= 12 and raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"):
        return ".jpg"
    if (len(raw) >= 20 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
            and raw[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
            and 20 <= int.from_bytes(raw[4:8], "little") + 8 <= len(raw)):
        return ".webp"
    if (len(raw) >= 14 and raw[:6] in (b"GIF87a", b"GIF89a")
            and int.from_bytes(raw[6:8], "little") > 0
            and int.from_bytes(raw[8:10], "little") > 0 and raw.endswith(b";")):
        return ".gif"
    raise ValueError("返回内容不是可识别的 PNG/JPEG/WebP/GIF 图片，可能是错误页面或不完整数据")


def _check_image_url(url: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("图片 URL 必须是有效的 HTTP 或 HTTPS 地址")


class _ImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_filename(value: str, fallback: str = "image") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", str(value)).strip("._")
    if cleaned.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        cleaned = "_" + cleaned
    return cleaned or fallback


class ImageStorage:
    def __init__(self, root: Path, downloader: Callable[[str], bytes] | None = None):
        self.root = Path(root).expanduser().resolve()
        self.downloader = downloader or self._download

    def persist(self, response: dict, stem: str) -> list[Path]:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list) or not data:
            raise ValueError("图像响应缺少 data 数组")
        self.root.mkdir(parents=True, exist_ok=True)
        base = safe_filename(stem)
        paths: list[Path] = []
        try:
            for index, item in enumerate(data, start=1):
                if not isinstance(item, dict):
                    raise ValueError("图像响应 data 项格式不正确")
                raw, extension = self._content(item)
                suffix = f"-{index}" if len(data) > 1 else ""
                collision = 1
                while True:
                    extra = f"-{collision}" if collision > 1 else ""
                    path = self.root / f"{base}{suffix}{extra}{extension}"
                    try:
                        handle = path.open("xb")
                        break
                    except FileExistsError:
                        collision += 1
                paths.append(path)
                with handle:
                    handle.write(raw)
        except Exception:
            # Only files exclusively created by this call may be rolled back.
            for path in paths:
                path.unlink(missing_ok=True)
            raise
        return paths

    def _content(self, item: dict) -> tuple[bytes, str]:
        encoded = item.get("b64_json")
        if encoded:
            if not isinstance(encoded, str):
                raise ValueError("图像 b64_json 必须是 Base64 字符串")
            if len(encoded) > MAX_IMAGE_BYTES * 2:
                raise ValueError("图像 Base64 内容超过保存上限")
            encoded = encoded.strip()
            if encoded.lower().startswith("data:"):
                header, sep, encoded = encoded.partition(",")
                if not sep or not header.lower().startswith("data:image/") or not header.lower().endswith(";base64"):
                    raise ValueError("图像 Base64 data URI 格式不正确")
            encoded = re.sub(r"\s+", "", encoded)
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError("图像 Base64 解码失败") from exc
            return raw, image_extension(raw)
        url = item.get("url")
        if url:
            if not isinstance(url, str):
                raise ValueError("图像 url 必须是字符串")
            _check_image_url(url)
            raw = self.downloader(url)
            return raw, image_extension(raw)
        raise ValueError("图像响应没有 b64_json 或 url")

    @staticmethod
    def _download(url: str) -> bytes:
        _check_image_url(url)
        # Image hosts may differ from the API host; never send the API key here.
        request = urllib.request.Request(url, headers={"User-Agent": f"waveeee-image-mcp/{__version__}", "Accept": "image/*"})
        try:
            opener = urllib.request.build_opener(_ImageRedirectHandler())
            with opener.open(request, timeout=120) as response:
                raw = response.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"图片下载失败（HTTP {exc.code}），可保存原响应后重试下载") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError("图片下载失败或超时，可保存原响应后重试下载") from exc
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("单张图片超过 64 MiB 保存上限")
        return raw

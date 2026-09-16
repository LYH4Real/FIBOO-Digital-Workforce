from __future__ import annotations

import json
import re
import mimetypes
import os
import secrets
import urllib.error
import urllib.request
from typing import Any, Callable

from . import __version__
from .config import Settings, api_key_error

# GPT Image 2 accepts arbitrary dimensions subject to these service limits.
MAX_SIDE_EXCLUSIVE = 3840
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
SIDE_MULTIPLE = 16
MAX_ASPECT_RATIO = 3
SIZE_PATTERN = re.compile(r"^(\d+)x(\d+)$")
SIZE_EXAMPLES = ("1024x1024", "1248x1664", "1664x1248", "2048x2048")
ALLOWED_FORMATS = {"auto", "url", "b64_json"}
Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]
EditTransport = Callable[[str, dict[str, str], dict[str, str], list[str], float], dict[str, Any]]


class WaveeeeError(RuntimeError):
    """A safe, user-facing Waveeee error."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def validate_size(size: str) -> None:
    if not isinstance(size, str):
        raise ValueError("size 必须是形如 WIDTHxHEIGHT 的字符串")
    match = SIZE_PATTERN.fullmatch(size.strip())
    if not match:
        raise ValueError("size 必须是形如 WIDTHxHEIGHT 的字符串，例如 1440x1920")
    width, height = (int(value) for value in match.groups())
    if width <= 0 or height <= 0:
        raise ValueError("size 的宽和高必须大于 0")
    if width >= MAX_SIDE_EXCLUSIVE or height >= MAX_SIDE_EXCLUSIVE:
        raise ValueError("size 的单边必须严格小于 3840px")
    if width % SIDE_MULTIPLE or height % SIDE_MULTIPLE:
        raise ValueError("size 的宽、高必须是 16 的倍数")
    pixels = width * height
    if pixels < MIN_PIXELS or pixels > MAX_PIXELS:
        raise ValueError("size 的总像素必须在 655360 到 8294400 之间")
    aspect_ratio = max(width, height) / min(width, height)
    if aspect_ratio > MAX_ASPECT_RATIO:
        raise ValueError("size 的长边与短边比例不能超过 3:1")


def validate_generation(prompt: str, size: str, n: int, response_format: str) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt 不能为空")
    validate_size(size)
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 4:
        raise ValueError("n 必须是 1 到 4 之间的整数")
    if not isinstance(response_format, str) or response_format not in ALLOWED_FORMATS:
        raise ValueError("response_format 必须是 auto、url 或 b64_json")


def build_payload(
    prompt: str,
    model: str = Settings.model,
    size: str = "1024x1024",
    n: int = 1,
    response_format: str = "auto",
) -> dict[str, Any]:
    validate_generation(prompt, size, n, response_format)
    payload = {
        "model": model,
        "prompt": prompt.strip(),
        "size": size,
        "n": n,
    }
    if response_format != "auto":
        payload["response_format"] = response_format
    return payload


def _urllib_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:1000]
        retryable = exc.code == 429 or exc.code >= 500
        raise WaveeeeError(f"Waveeee 请求失败（HTTP {exc.code}）：{raw}", retryable=retryable) from exc
    except urllib.error.URLError as exc:
        raise WaveeeeError(f"无法连接 Waveeee：{exc.reason}", retryable=True) from exc
    if status < 200 or status >= 300:
        raise WaveeeeError(f"Waveeee 请求失败（HTTP {status}）：{raw[:1000]}", retryable=status >= 500)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WaveeeeError("Waveeee 返回的不是合法 JSON") from exc


def _urllib_edit_transport(
    url: str,
    headers: dict[str, str],
    fields: dict[str, str],
    image_paths: list[str],
    timeout: float,
) -> dict[str, Any]:
    boundary = "----waveeee-" + secrets.token_hex(16)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    for path in image_paths:
        file_path = os.fspath(path)
        filename = os.path.basename(file_path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(file_path, "rb") as handle:
            content = handle.read()
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    request_headers = dict(headers)
    request_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:1000]
        raise WaveeeeError(f"Waveeee 请求失败（HTTP {exc.code}）：{raw}", retryable=exc.code == 429 or exc.code >= 500) from exc
    except urllib.error.URLError as exc:
        raise WaveeeeError(f"无法连接 Waveeee：{exc.reason}", retryable=True) from exc
    if status < 200 or status >= 300:
        raise WaveeeeError(f"Waveeee 请求失败（HTTP {status}）：{raw[:1000]}", retryable=status >= 500)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WaveeeeError("Waveeee 返回的不是合法 JSON") from exc


class WaveeeeClient:
    def __init__(self, settings: Settings, transport: Transport | None = None, edit_transport: EditTransport | None = None):
        self.settings = settings
        self.transport = transport or _urllib_transport
        self.edit_transport = edit_transport or _urllib_edit_transport

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
        response_format: str = "auto",
    ) -> dict[str, Any]:
        error = api_key_error(self.settings.api_key)
        if error:
            raise ValueError(error)
        payload = build_payload(prompt, model or self.settings.model, size, n, response_format)
        url = f"{self.settings.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"waveeee-image-mcp/{__version__}",
        }
        try:
            result = self.transport(url, headers, payload, self.settings.timeout_seconds)
        except Exception as exc:
            if isinstance(exc, WaveeeeError):
                raise WaveeeeError(_redact(str(exc), self.settings.api_key), retryable=exc.retryable) from exc
            raise WaveeeeError(_redact(str(exc), self.settings.api_key), retryable=True) from exc
        if not isinstance(result, dict) or not isinstance(result.get("data"), list) or not result["data"]:
            raise WaveeeeError("Waveeee 返回格式不正确，缺少非空 data 数组")
        return result

    def edit(
        self,
        prompt: str,
        *,
        image_paths: list[str],
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
        response_format: str = "auto",
    ) -> dict[str, Any]:
        error = api_key_error(self.settings.api_key)
        if error:
            raise ValueError(error)
        validate_generation(prompt, size, n, response_format)
        if not isinstance(image_paths, list) or not 1 <= len(image_paths) <= 10:
            raise ValueError("image_paths 必须是 1 到 10 个本地图片路径的数组")
        normalized = [os.fspath(path) for path in image_paths]
        for path in normalized:
            if not os.path.isfile(path):
                raise ValueError(f"参考图片不存在或不是文件：{path}")
        fields = {
            "model": model or self.settings.model,
            "prompt": prompt.strip(),
            "size": size,
            "n": str(n),
        }
        if response_format != "auto":
            fields["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Accept": "application/json",
            "User-Agent": f"waveeee-image-mcp/{__version__}",
        }
        url = f"{self.settings.base_url}/images/edits"
        try:
            result = self.edit_transport(url, headers, fields, normalized, self.settings.timeout_seconds)
        except Exception as exc:
            if isinstance(exc, WaveeeeError):
                raise WaveeeeError(_redact(str(exc), self.settings.api_key), retryable=exc.retryable) from exc
            raise WaveeeeError(_redact(str(exc), self.settings.api_key), retryable=True) from exc
        if not isinstance(result, dict) or not isinstance(result.get("data"), list) or not result["data"]:
            raise WaveeeeError("Waveeee 返回格式不正确，缺少非空 data 数组")
        return result


def _redact(message: str, secret: str) -> str:
    return message.replace(secret, "***") if secret else message

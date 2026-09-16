from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def api_key_error(value: str) -> str | None:
    """Return a safe local format diagnostic; never include the credential."""
    if not isinstance(value, str) or not value:
        return "缺少 WAVEEEE_API_KEY，请配置真实的 Waveeee API Key"
    if value.lower().startswith("bearer "):
        return "WAVEEEE_API_KEY 只填写密钥本身，不要添加 Bearer 前缀"
    if not value.isascii():
        return "WAVEEEE_API_KEY 含中文或非 ASCII 字符，请将示例占位文字替换为真实 API Key；这不是提示词或图片路径的编码问题"
    if any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in value):
        return "WAVEEEE_API_KEY 含空格、换行或控制字符，请填写完整的单行 API Key"
    return None


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str = "https://api.waveeee.com/v1"
    model: str = "gpt-image-2.5"
    output_dir: Path = Path("generated_images")
    timeout_seconds: float = 120.0
    max_concurrency: int = 4
    max_retries: int = 2

    @classmethod
    def from_env(cls, *, require_api_key: bool = True) -> "Settings":
        api_key = os.getenv("WAVEEEE_API_KEY", "").strip()
        if require_api_key:
            error = api_key_error(api_key)
            if error:
                raise ValueError(error)
        output = os.getenv("WAVEEEE_OUTPUT_DIR", "generated_images").strip() or "generated_images"
        timeout = _positive_float(os.getenv("WAVEEEE_TIMEOUT_SECONDS", "120"), "WAVEEEE_TIMEOUT_SECONDS")
        concurrency = _bounded_int(os.getenv("WAVEEEE_MAX_CONCURRENCY", "4"), "WAVEEEE_MAX_CONCURRENCY", 1, 16)
        retries = _bounded_int(os.getenv("WAVEEEE_MAX_RETRIES", "2"), "WAVEEEE_MAX_RETRIES", 0, 5)
        return cls(
            api_key=api_key,
            base_url=(os.getenv("WAVEEEE_BASE_URL", cls.base_url).strip().rstrip("/")),
            model=(os.getenv("WAVEEEE_MODEL", cls.model).strip() or cls.model),
            output_dir=Path(output).expanduser(),
            timeout_seconds=timeout,
            max_concurrency=concurrency,
            max_retries=retries,
        )


def _positive_float(value: str, name: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if result <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return result


def _bounded_int(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return result

#!/usr/bin/env python3
"""Download one ordinary DingTalk file through DWS and verify its bytes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAX_BYTES = 64 * 1024 * 1024
ONLINE_EXTENSIONS = {"adoc", "axls", "able", "appt", "mind", "whiteboard"}


class DownloadError(Exception):
    def __init__(self, code: str, message: str, **details: object):
        super().__init__(message)
        self.code = code
        self.details = details


def _dingtalk_url(value: str) -> str:
    if not isinstance(value, str):
        raise DownloadError("invalid_url", "--url 必须是 HTTPS 钉钉链接。")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        valid = (
            parsed.scheme == "https"
            and (host == "dingtalk.com" or host.endswith(".dingtalk.com"))
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        )
    except ValueError:
        valid = False
    if not valid:
        raise DownloadError("invalid_url", "--url 必须是 HTTPS 钉钉链接。")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _run_json(command: list[str], cwd: Path, operation: str) -> dict:
    try:
        completed = subprocess.run(
            command, cwd=str(cwd), shell=False, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="strict", timeout=300,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise DownloadError("dws_failed", f"DWS {operation} 未完成；请检查 CLI、网络或认证。") from exc
    if completed.returncode != 0:
        raise DownloadError("dws_failed", f"DWS {operation} 返回失败；原始输出未透传。")
    try:
        data = json.loads(completed.stdout.lstrip("\ufeff"))
    except (ValueError, AttributeError) as exc:
        raise DownloadError("invalid_response", f"DWS {operation} 未返回有效 JSON。") from exc
    if not isinstance(data, dict):
        raise DownloadError("invalid_response", f"DWS {operation} 响应不是对象。")
    for item in (data, data.get("result")):
        if isinstance(item, dict) and (
            item.get("success") is False or item.get("ok") is False
            or item.get("error") or item.get("outcome") in ("failed", "failure", "partial_success", "unknown")
        ):
            raise DownloadError("dws_failed", f"DWS {operation} 报告未成功。")
    return data


def _expected_size(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise DownloadError("invalid_metadata", "文件大小元数据无效。")
    size = int(str(value))
    if not 0 < size <= MAX_BYTES:
        raise DownloadError("size_limit", "只支持大于 0 且不超过 64 MiB 的文件。")
    return size


def _expected_md5(value: object) -> bytes | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise DownloadError("invalid_metadata", "MD5 元数据无效。")
    try:
        digest = bytes.fromhex(value) if re.fullmatch(r"[0-9a-fA-F]{32}", value) else base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise DownloadError("invalid_metadata", "MD5 元数据不是有效的十六进制或 Base64。") from exc
    if len(digest) != 16:
        raise DownloadError("invalid_metadata", "MD5 元数据长度无效。")
    return digest


def _filename(value: object, extension: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "document")).strip(" .")
    stem = Path(name).stem[:120].strip(" .") or "document"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", stem):
        stem = "_" + stem
    return f"{stem}.{extension}" if extension else stem


def download_document(url: str, profile: str, output_dir: str | Path | None = None, dws: str | None = None) -> dict:
    source_url = _dingtalk_url(url)
    if not profile or not profile.strip() or "," in profile:
        raise DownloadError("invalid_profile", "必须提供本轮已确认的单个 DWS profile。")
    output = Path(output_dir) if output_dir is not None else Path.cwd()
    if not output.is_absolute():
        raise DownloadError("invalid_output_dir", "--output-dir 必须是绝对路径。")
    output = output.resolve()
    executable = shutil.which(dws or os.environ.get("DWS_EXECUTABLE") or "dws")
    if not executable:
        raise DownloadError("dws_not_found", "未找到 dws，请通过 --dws 指定可执行文件。")
    output.mkdir(parents=True, exist_ok=True)
    profile_args = ["--profile", profile, "--format", "json"]
    info = _run_json([executable, "drive", "info", "--node", url, *profile_args], output, "info")
    metadata = info.get("result")
    if info.get("success") is not True or not isinstance(metadata, dict):
        raise DownloadError("invalid_metadata", "DWS info 缺少成功的 result 对象。")
    node_type = str(metadata.get("type") or "UNKNOWN").upper()
    extension = str(metadata.get("extension") or "").lower().lstrip(".")
    if node_type != "FILE" or extension in ONLINE_EXTENSIONS:
        route = "drive +list" if node_type in {"FOLDER", "DIRECTORY"} else "对应的文档 skill"
        raise DownloadError("unsupported_type", "仅下载普通文件，请按节点类型重新路由。", node_type=node_type, extension=extension, suggested_route=route)
    if extension and not re.fullmatch(r"[a-z0-9]{1,16}", extension):
        raise DownloadError("invalid_metadata", "文件扩展名元数据无效。")
    node_id, space_id = metadata.get("fileId"), metadata.get("spaceId")
    if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", node_id):
        raise DownloadError("invalid_metadata", "DWS info 缺少有效 fileId。")
    if not isinstance(space_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", space_id):
        raise DownloadError("invalid_metadata", "DWS info 缺少有效 spaceId。")
    expected_size = _expected_size(metadata.get("fileSize"))
    expected_md5 = _expected_md5(metadata.get("md5"))
    doc_url = _dingtalk_url(metadata.get("docUrl") or source_url)
    modified_at = next((metadata[key] for key in ("modifyTime", "modifiedTime", "lastModifiedTime", "modifiedAt", "updateTime") if isinstance(metadata.get(key), (str, int)) and not isinstance(metadata.get(key), bool)), None)
    name = _filename(metadata.get("name"), extension)
    staging = output / ("dingtalk-" + uuid.uuid4().hex)
    staging.mkdir()
    target = staging / name
    try:
        relative_target = target.relative_to(output).as_posix()
        _run_json([executable, "drive", "+download", "--node", node_id, "--space-id", space_id, "--output", relative_target, *profile_args], output, "download")
        if target.is_symlink() or not target.is_file() or target.resolve().parent != staging:
            raise DownloadError("missing_file", "下载没有产生预期的普通文件。")
        actual_size = target.stat().st_size
        if not 0 < actual_size <= MAX_BYTES:
            raise DownloadError("size_limit", "实际文件必须大于 0 且不超过 64 MiB。")
        if expected_size is not None and actual_size != expected_size:
            raise DownloadError("size_mismatch", "实际文件大小与钉钉元数据不一致。")
        sha256, md5 = hashlib.sha256(), hashlib.md5()
        read_size = 0
        with target.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                read_size += len(block)
                if read_size > MAX_BYTES:
                    raise DownloadError("size_limit", "读取文件超过 64 MiB。")
                sha256.update(block)
                md5.update(block)
        if read_size != actual_size:
            raise DownloadError("size_mismatch", "文件在校验过程中发生变化。")
        if expected_md5 is not None and md5.digest() != expected_md5:
            raise DownloadError("md5_mismatch", "实际文件 MD5 与钉钉元数据不一致。")
        return {
            "success": True,
            "file": {"absolute_path": str(target), "name": name, "extension": extension, "size": actual_size, "sha256": sha256.hexdigest()},
            "source": {"node": node_id, "docUrl": doc_url, "modified_at": modified_at, "retrieved_at": datetime.now(timezone.utc).isoformat()},
            "verification": {"size": "matched" if expected_size is not None else "unavailable", "md5": "matched" if expected_md5 is not None else "unavailable"},
        }
    except BaseException:
        # Only remove the unique directory created by this invocation, never an existing user file.
        if staging.parent == output and staging.name.startswith("dingtalk-") and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--dws")
    args = parser.parse_args(argv)
    try:
        result = download_document(args.url, args.profile, args.output_dir, args.dws)
    except DownloadError as exc:
        print(json.dumps({"success": False, "error": {"code": exc.code, "message": str(exc), **exc.details}}, ensure_ascii=False))
        return 1
    except OSError:
        print(json.dumps({"success": False, "error": {"code": "local_io_error", "message": "本地文件读写失败。"}}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

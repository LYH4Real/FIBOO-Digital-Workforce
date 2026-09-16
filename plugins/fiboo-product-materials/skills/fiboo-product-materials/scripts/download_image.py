"""Download a selected product attachment from a fresh DWS record."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from product_materials import DWS, MaterialsError, ProductMaterials

MAX_BYTES = 64 * 1024 * 1024


def check_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not any(host.endswith(suffix) for suffix in (".aliyuncs.com", ".alicdn.com"))):
        raise MaterialsError("unsupported_storage", "附件存储域名不受此版本支持；未下载。")
    return url


class StorageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def image_extension(header):
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"
    raise MaterialsError("invalid_image", "附件内容不是支持的 PNG/JPEG/GIF/WebP 图片。")


def save_attachment(attachment, output_dir, opener=None):
    expected = attachment.get("size")
    if isinstance(expected, int) and (expected <= 0 or expected > MAX_BYTES):
        raise MaterialsError("size_limit", "附件大小无效或超过 64 MiB。")
    url = attachment.get("url")
    if not isinstance(url, str):
        raise MaterialsError("missing_download_url", "该附件未返回可用下载地址。")
    check_url(url)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = root / ("product-image-" + uuid.uuid4().hex)
    directory.mkdir()
    partial = directory / "image.part"
    try:
        request = Request(url, headers={"Accept": "image/*"})
        client = opener or build_opener(StorageRedirect())
        digest, count, header = hashlib.sha256(), 0, b""
        with client.open(request, timeout=30) as response, partial.open("xb") as f:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise MaterialsError("size_limit", "下载图片超过 64 MiB。")
            while chunk := response.read(256 * 1024):
                count += len(chunk)
                if count > MAX_BYTES:
                    raise MaterialsError("size_limit", "下载图片超过 64 MiB。")
                if len(header) < 16:
                    header += chunk[:16 - len(header)]
                digest.update(chunk)
                f.write(chunk)
        if not count or isinstance(expected, int) and count != expected:
            raise MaterialsError("size_mismatch", "图片实际字节数与附件记录不一致。")
        destination = directory / ("image" + image_extension(header))
        partial.rename(destination)
        return {"absolute_path": str(destination), "size": count, "sha256": digest.hexdigest(),
                "resourceId": attachment.get("resourceId"), "source_name": attachment.get("filename"),
                "image_type_checked": True, "size_checked": isinstance(expected, int)}
    except BaseException:
        if partial.exists():
            partial.unlink()
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
        raise


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--scope", choices=("view", "table"), default="view")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dws", default=os.environ.get("DWS_EXECUTABLE", "dws"))
    args = parser.parse_args()
    try:
        service = ProductMaterials(DWS(args.dws), profile=args.profile, scope=args.scope)
        raw = service.get_raw(args.record_id)
        attachments = raw.get("cells", {}).get(service.names.get("产品图"), [])
        if not isinstance(attachments, list) or not 0 <= args.index < len(attachments):
            raise MaterialsError("attachment_missing", "该产品没有所选序号的产品图附件。")
        result = save_attachment(attachments[args.index], args.output_dir)
        result.update({"status": "success", "recordId": args.record_id, "scope": args.scope,
                       "retrieved_at": datetime.now(timezone.utc).isoformat()})
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except MaterialsError as exc:
        print(json.dumps({"status": "error", "error": exc.as_dict()}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": {"category": type(exc).__name__,
                          "message": "图片下载失败，未返回成功文件；临时下载地址已隐藏。"}}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())

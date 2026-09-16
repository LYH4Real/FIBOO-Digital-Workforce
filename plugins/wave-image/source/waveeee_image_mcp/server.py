from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .batch import BatchRunner
from .edit_batch import EditBatchRunner
from .client import ALLOWED_FORMATS, SIZE_EXAMPLES, WaveeeeClient
from .config import Settings, api_key_error
from .storage import ImageStorage, safe_filename

PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = __version__


class MCPServer:
    def __init__(self, settings: Settings | None = None, *, client: WaveeeeClient | None = None):
        self.config_error: str | None = None
        if settings is None:
            try:
                settings = Settings.from_env(require_api_key=False)
            except ValueError as exc:
                self.config_error = str(exc)
                settings = Settings(api_key="")
        self.settings = settings
        self.client = client

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "initialize":
            return self._result(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "waveeee-image-mcp", "version": SERVER_VERSION},
                "instructions": "生成或编辑图片后自动保存到本地；已有 API 响应使用 save_image_response 保存，无需重新生图。按实际返回的 url 或 b64_json 处理图片。",
            })
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": self.tool_definitions()})
        if method == "tools/call":
            params = request.get("params") or {}
            try:
                result = self.call_tool(str(params.get("name", "")), params.get("arguments") or {})
                return self._result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "structuredContent": result})
            except Exception as exc:
                return self._result(request_id, {"isError": True, "content": [{"type": "text", "text": str(exc)}]})
        if request_id is None:
            return None
        return self._error(request_id, -32601, f"未知方法：{method}")

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "server_info":
            return self.server_info()
        if name == "save_image_response":
            return self.save_image_response(args)
        if name == "generate_image":
            return self.generate_image(args)
        if name == "edit_image":
            return self.edit_image(args)
        if name == "generate_batch_images":
            return self.generate_batch_images(args)
        if name == "edit_batch_images":
            return self.edit_batch_images(args)
        raise ValueError(f"未知工具：{name}")

    def server_info(self) -> dict[str, Any]:
        configuration_error = self.config_error or api_key_error(self.settings.api_key)
        return {
            "name": "waveeee-image-mcp",
            "version": SERVER_VERSION,
            "base_url": self.settings.base_url,
            "model": self.settings.model,
            "output_dir": str(self.settings.output_dir.resolve()),
            "api_key_configured": configuration_error is None,
            "configuration_error": configuration_error,
            "size_constraints": {
                "examples": list(SIZE_EXAMPLES),
                "max_side_exclusive": 3840,
                "side_multiple": 16,
                "min_pixels": 655360,
                "max_pixels": 8294400,
                "max_aspect_ratio": 3,
            },
            "allowed_response_formats": sorted(ALLOWED_FORMATS),
            "default_response_format": "auto",
            "response_handling": "auto 不发送 response_format；保存始终按实际 data[].url 或 data[].b64_json 处理。",
            "max_concurrency": self.settings.max_concurrency,
            "max_retries": self.settings.max_retries,
        }

    def generate_image(self, args: dict[str, Any]) -> dict[str, Any]:
        client = self._client()
        prompt = args.get("prompt", "")
        response_format = args.get("response_format", "auto")
        response = client.generate(
            prompt,
            model=args.get("model"),
            size=args.get("size", "1024x1024"),
            n=args.get("n", 1),
            response_format=response_format,
        )
        root = self._output_dir(args.get("output_subdir", ""))
        filename = Path(str(args.get("filename", "image"))).stem or "image"
        paths = ImageStorage(root).persist(response, safe_filename(filename))
        return {
            "success": True,
            "model": args.get("model") or self.settings.model,
            "prompt": str(prompt).strip(),
            "size": args.get("size", "1024x1024"),
            "images": [str(path) for path in paths],
            "created": response.get("created"),
        }

    def generate_batch_images(self, args: dict[str, Any]) -> dict[str, Any]:
        client = self._client()
        tasks = args.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("tasks 必须是非空数组")
        if len(tasks) > 100:
            raise ValueError("单批次最多支持 100 个任务")
        root = self._output_dir(args.get("output_subdir", ""))
        runner = BatchRunner(client, root)
        results = runner.run(
            tasks,
            concurrency=args.get("concurrency", self.settings.max_concurrency),
            retries=args.get("retries", self.settings.max_retries),
        )
        return {
            "success": all(item["success"] for item in results),
            "total": len(results),
            "succeeded": sum(1 for item in results if item["success"]),
            "failed": sum(1 for item in results if not item["success"]),
            "manifest": str(root / "manifest.jsonl"),
            "results": results,
        }

    def edit_image(self, args: dict[str, Any]) -> dict[str, Any]:
        client = self._client()
        image_paths = args.get("image_paths", args.get("images", args.get("image", [])))
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        response = client.edit(
            args.get("prompt", ""),
            image_paths=image_paths,
            model=args.get("model"),
            size=args.get("size", "1024x1024"),
            n=args.get("n", 1),
            response_format=args.get("response_format", "auto"),
        )
        root = self._output_dir(args.get("output_subdir", ""))
        filename = Path(str(args.get("filename", "edited-image"))).stem or "edited-image"
        paths = ImageStorage(root).persist(response, safe_filename(filename))
        return {
            "success": True,
            "model": args.get("model") or self.settings.model,
            "prompt": str(args.get("prompt", "")).strip(),
            "reference_images": [str(path) for path in image_paths],
            "size": args.get("size", "1024x1024"),
            "images": [str(path) for path in paths],
            "created": response.get("created"),
        }

    def edit_batch_images(self, args: dict[str, Any]) -> dict[str, Any]:
        tasks = args.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("tasks 必须是非空数组")
        if len(tasks) > 100:
            raise ValueError("单批次最多支持 100 个任务")
        root = self._output_dir(args.get("output_subdir", ""))
        results = EditBatchRunner(self._client(), root).run(tasks, concurrency=args.get("concurrency", self.settings.max_concurrency), retries=args.get("retries", self.settings.max_retries))
        return {"success": all(item["success"] for item in results), "total": len(results), "succeeded": sum(item["success"] for item in results), "failed": sum(not item["success"] for item in results), "manifest": str(root / "manifest.jsonl"), "results": results}

    def _client(self) -> WaveeeeClient:
        if self.config_error:
            raise ValueError(self.config_error)
        error = api_key_error(self.settings.api_key)
        if error:
            raise ValueError(error)
        if self.client is None:
            self.client = WaveeeeClient(self.settings)
        return self.client

    def save_image_response(self, args: dict[str, Any]) -> dict[str, Any]:
        """Persist a completed API response without constructing a generation client."""
        has_response = "response" in args
        has_path = "response_json_path" in args
        if has_response == has_path:
            raise ValueError("response 与 response_json_path 必须且只能提供一个")
        if has_path:
            value = args["response_json_path"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError("response_json_path 必须是本地 JSON 文件的绝对路径")
            path = Path(value)
            if not path.is_absolute():
                raise ValueError("response_json_path 必须是本地 JSON 文件的绝对路径")
            if not path.is_file():
                raise ValueError("response_json_path 不存在或不是文件")
            try:
                response = json.loads(path.read_text(encoding="utf-8-sig"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("response_json_path 必须包含合法的 UTF-8 JSON") from exc
        else:
            response = args["response"]
        if not isinstance(response, dict):
            raise ValueError("response 必须是包含 data 数组的 JSON 对象")
        root = self._output_dir(args.get("output_subdir", ""))
        filename = Path(str(args.get("filename", "saved-image"))).stem or "saved-image"
        paths = ImageStorage(root).persist(response, safe_filename(filename))
        result = {"success": True, "images": [str(path) for path in paths], "created": response.get("created")}
        for key in ("model", "usage"):
            if key in response:
                result[key] = response[key]
        return result

    def _output_dir(self, subdir: Any) -> Path:
        root = self.settings.output_dir.expanduser().resolve()
        candidate = (root / str(subdir or "")).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("output_subdir 不能跳出配置的输出目录")
        return candidate

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def tool_definitions(self) -> list[dict[str, Any]]:
        def generation_properties() -> dict[str, Any]:
            return {
                "prompt": {"type": "string"},
                "model": {"type": "string", "default": self.settings.model,
                          "description": "省略时采用 WAVEEEE_MODEL 运行时配置；显式值保持不变。"},
                "size": {"type": "string", "pattern": "^[0-9]+x[0-9]+$", "default": "1024x1024",
                         "description": "WIDTHxHEIGHT；宽高均为 16 的倍数，单边 <3840px，总像素 655360–8294400，长宽比不超过 3:1"},
                "n": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
                "response_format": {"type": "string", "enum": sorted(ALLOWED_FORMATS), "default": "auto",
                                    "description": "auto 省略 API 请求中的此字段；url/b64_json 显式发送。保存时始终按实际返回的 url 或 b64_json 自动处理。"},
            }

        def reference_property() -> dict[str, Any]:
            return {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string"},
                    "description": "本地参考图片路径；以 multipart 的 image 字段逐张上传。"}

        definitions = []
        for name, editing, batching in (
            ("generate_image", False, False), ("edit_image", True, False),
            ("generate_batch_images", False, True), ("edit_batch_images", True, True),
        ):
            properties = generation_properties()
            required = ["prompt"]
            if editing:
                properties["image_paths"] = reference_property()
                required.append("image_paths")
            if batching:
                properties["id"] = {"type": "string"}
                properties = {
                    "tasks": {"type": "array", "minItems": 1, "maxItems": 100,
                              "items": {"type": "object", "properties": properties, "required": required}},
                    "concurrency": {"type": "integer", "minimum": 1, "maximum": 16, "default": self.settings.max_concurrency},
                    "retries": {"type": "integer", "minimum": 0, "maximum": 5, "default": self.settings.max_retries},
                }
                required = ["tasks"]
            else:
                properties["filename"] = {"type": "string"}
            properties["output_subdir"] = {"type": "string"}
            operation = "参考图编辑" if editing else "生成"
            description = f"使用 Waveeee {operation}图片并保存到本地；按实际返回的 url/b64_json 自动处理。"
            if batching:
                description += "并发执行各任务并写入 manifest.jsonl。"
            definitions.append({"name": name, "description": description,
                                "inputSchema": {"type": "object", "properties": properties, "required": required}})
        definitions.extend([
            {
                "name": "save_image_response",
                "description": "保存已有 API 响应中的图片，无需 API Key、不重新生图。互斥提供 response 对象或本地 JSON 文件绝对路径；自动下载 data[].url 或解码 data[].b64_json，返回本地图片路径。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "response": {"type": "object", "description": "已有 API JSON 响应，含非空 data 数组。"},
                        "response_json_path": {"type": "string", "description": "已有 UTF-8 JSON 响应文件的本地绝对路径。"},
                        "filename": {"type": "string"},
                        "output_subdir": {"type": "string"},
                    },
                    "oneOf": [
                        {"required": ["response"], "not": {"required": ["response_json_path"]}},
                        {"required": ["response_json_path"], "not": {"required": ["response"]}},
                    ],
                },
            },
            {"name": "server_info", "description": "查看服务版本、模型、输出目录和运行时能力。",
             "inputSchema": {"type": "object", "properties": {}}},
        ])
        return definitions


def handle_message(line: str, server: MCPServer) -> str | None:
    try:
        request = json.loads(line)
        response = server.handle(request)
        return json.dumps(response, ensure_ascii=False) if response is not None else None
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"无效 JSON-RPC 请求：{exc}"}}, ensure_ascii=False)


def run_stdio(
    server: MCPServer | None = None,
    *,
    stdin: Any | None = None,
    stdout: Any | None = None,
) -> None:
    """Run the MCP JSON-RPC loop with UTF-8 at the process boundary.

    MCP stdio is a byte protocol.  Using ``sys.stdin``/``sys.stdout`` directly
    on Windows makes Python inherit the active console code page (often cp936),
    which corrupts Chinese prompts and tool descriptions.  Reading/writing the
    underlying buffers and explicitly encoding UTF-8 keeps the protocol
    independent of the host locale.  ``stdin``/``stdout`` parameters are
    injectable for tests and embedders.
    """
    active = server or MCPServer()
    input_stream = stdin if stdin is not None else getattr(sys.stdin, "buffer", sys.stdin)
    output_stream = stdout if stdout is not None else getattr(sys.stdout, "buffer", sys.stdout)

    for raw_line in input_stream:
        if isinstance(raw_line, bytes):
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                error = json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"输入必须是 UTF-8：{exc}"}},
                    ensure_ascii=False,
                )
                _write_stdio(output_stream, error)
                continue
        else:
            line = str(raw_line)
        line = line.strip()
        if not line:
            continue
        response = handle_message(line, active)
        if response:
            _write_stdio(output_stream, response)


def _write_stdio(stream: Any, response: str) -> None:
    payload = (response + "\n").encode("utf-8")
    try:
        stream.write(payload)
    except TypeError:
        # Text streams are useful for embedding/tests; force UTF-8 where
        # possible rather than inheriting the Windows locale encoding.
        try:
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")
        except (AttributeError, ValueError):
            pass
        stream.write(payload.decode("utf-8"))
    stream.flush()

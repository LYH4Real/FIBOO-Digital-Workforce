"""Frozen Windows entry point and local installation diagnostics.

Normal invocation leaves stdout exclusively to the stdio MCP server. Diagnostic
modes use a child of this same executable, so they check the distributed server.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys


EXPECTED_TOOLS = {
    "xhs_status", "xhs_discover_notes", "xhs_fetch_note", "xhs_fetch_notes"
}
DIAGNOSTIC_FLAGS = {"--setup", "--check", "--self-test"}


def _child_settings():
    from mcp import StdioServerParameters

    child_env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"PYTHONHOME", "PYTHONPATH"}
    }
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    # A new diagnostic server owns its own frozen runtime initialization.
    child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    args = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve())]
    return StdioServerParameters(command=sys.executable, args=args, env=child_env)


def _payload(result) -> dict:
    payload = result.structuredContent
    texts = [item.text for item in result.content if item.type == "text"]
    if not isinstance(payload, dict) or len(texts) != 1:
        raise RuntimeError("MCP result format mismatch")
    if json.loads(texts[0]) != payload:
        raise RuntimeError("MCP text and structured payload differ")
    return payload


async def _diagnose(*, check_bridge: bool) -> dict | None:
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    # Discard server diagnostics; never print browser data or request contents.
    with open(os.devnull, "w", encoding="utf-8") as server_stderr:
        with anyio.fail_after(30):
            async with stdio_client(_child_settings(), errlog=server_stderr) as (reader, writer):
                async with ClientSession(
                    reader, writer, read_timeout_seconds=timedelta(seconds=10)
                ) as session:
                    initialized = await session.initialize()
                    if initialized.serverInfo.name != "xhs-note-fetcher":
                        raise RuntimeError("Unexpected MCP server")
                    listed = await session.list_tools()
                    names = [tool.name for tool in listed.tools]
                    if len(names) != 4 or set(names) != EXPECTED_TOOLS:
                        raise RuntimeError("Unexpected MCP tool list")
                    invalid = await session.call_tool(
                        "xhs_fetch_note", {"share_text": "中文自检：这不是笔记链接"}
                    )
                    payload = _payload(invalid)
                    if not invalid.isError or payload.get("error", {}).get("code") != "INVALID_URL":
                        raise RuntimeError("Invalid URL test failed")
                    print("[PASS] MCP 协议自检通过：初始化、4 个工具、中文无效链接处理。", flush=True)
                    if not check_bridge:
                        return None
                    try:
                        response = await session.call_tool("xhs_status", {})
                        status = _payload(response)
                        if response.isError:
                            return {"running": False, "extension_connected": False}
                        # Session identifiers and other future fields stay private.
                        return {
                            key: status.get(key)
                            for key in ("running", "extension_connected", "version", "extension_version")
                        }
                    except Exception:
                        return {"running": False, "extension_connected": False}


def _write_config() -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("--setup 请使用分享包中的 runtime\\xhs-note-mcp.exe。")
    executable = Path(sys.executable).resolve()
    config_path = executable.parent.parent / "workbuddy-mcp.json"
    config = {
        "mcpServers": {
            "xhs-note-fetcher": {
                "command": str(executable),
                "args": [],
                "env": {"PYTHONUTF8": "1"},
            }
        }
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def _diagnostic_main(mode: str) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    config_path = None
    if mode == "--setup":
        if not getattr(sys, "frozen", False):
            print("安装配置需使用分享包中的 runtime\\xhs-note-mcp.exe；源码可运行 --self-test。")
            return 1
        try:
            config_path = _write_config()
        except OSError:
            print("配置文件写入失败。请把完整分享包解压到有写入权限的目录，再运行 install.cmd。")
            return 1
    curl_missing = mode == "--check" and shutil.which("curl.exe") is None
    try:
        status = asyncio.run(_diagnose(check_bridge=(mode == "--check" and not curl_missing)))
    except Exception as exc:
        print(f"[FAIL] MCP 协议自检失败（{type(exc).__name__}）。")
        print("请重新完整解压分享包，确认 runtime 及其中的 _internal 文件夹齐全，再重试。")
        return 1
    if config_path is not None:
        print(f"[PASS] 已生成 WorkBuddy 配置：{config_path}")
        print("请按 README 的步骤将该配置加入 WorkBuddy。此操作不会修改已有 WorkBuddy 配置。")
        print("移动解压目录后，请重新运行 install.cmd 并更新 WorkBuddy 中的路径。")
    if mode == "--check":
        if curl_missing:
            print("[未就绪] MCP 程序正常，但系统找不到 curl.exe。")
            print("请确认 Windows 自带 curl 组件可用，且 Windows\\System32 在 PATH 中。")
            print("如使用旧版或精简版系统，请联系同事或 IT 恢复系统 curl 后，再运行 check.cmd。")
            return 2
        assert status is not None
        print(json.dumps(status, ensure_ascii=False, indent=2))
        if status.get("running") is not True or status.get("extension_connected") is not True:
            print("[未就绪] MCP 程序正常，Kimi 浏览器连接尚未就绪。")
            print("请按 README 安装并启动 Kimi 浏览器扩展及本地连接服务，打开浏览器并确认扩展已连接。")
            print("随后在该浏览器登录小红书，再运行 check.cmd。此检查不会自动打开网页。")
            return 2
        print("[PASS] Kimi 本地服务和浏览器扩展已连接。抓取前请在浏览器中登录小红书。")
    return 0


def main() -> int:
    args = sys.argv[1:]
    special = [arg for arg in args if arg in DIAGNOSTIC_FLAGS]
    if special:
        if len(args) != 1:
            print("--setup、--check、--self-test 须单独使用。", file=sys.stderr)
            return 1
        return _diagnostic_main(special[0])
    from xhs_note_fetcher.mcp_server import main as server_main

    return server_main(args)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())

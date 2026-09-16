"""Stdio MCP tools for the persistent Kimi WebBridge fetcher.

Install the optional ``mcp`` extra before running this module. The ordinary
``xhs-note`` CLI intentionally has no dependency on the MCP SDK.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Annotated, Any

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult, TextContent, ToolAnnotations
    from pydantic import Field
except ModuleNotFoundError as exc:
    if exc.name == "mcp":
        raise SystemExit('MCP support is not installed. Run: python -m pip install -e ".[mcp]"') from None
    raise

from .errors import FetchError


LOGGER = logging.getLogger(__name__)
Timeout = Annotated[float, Field(gt=0, le=120, description="每篇详情读取超时秒数，默认 10。")]
ShareText = Annotated[str, Field(min_length=1, description="完整小红书分享链接或含链接的分享文案，保留 xsec_token。")]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


def _result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    """Keep JSON text and structured output equivalent, including Chinese text."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")))],
        structuredContent=payload,
        isError=is_error,
    )


async def _invoke(function, *args, **kwargs) -> CallToolResult:
    try:
        payload = await asyncio.to_thread(function, *args, **kwargs)
    except FetchError as exc:
        return _result(exc.as_dict(), is_error=True)
    except Exception as exc:
        # Do not reflect bridge commands, browser data, or tokens in an unexpected
        # exception back to the MCP client or process logs.
        LOGGER.error("Unexpected WebBridge failure (%s)", type(exc).__name__)
        return _result({"error": {"code": "INTERNAL_ERROR", "message": "读取失败；请检查 WebBridge 状态后重试。", "details": {"exception_type": type(exc).__name__}}}, is_error=True)
    if isinstance(payload, list):
        payload = {"notes": payload, "count": len(payload)}
    # A partially successful batch is usable. Every item retains its own status.
    all_failed = bool(payload.get("total", 0)) and payload.get("succeeded") == 0
    return _result(payload, is_error=bool(payload.get("error")) or all_failed)


def create_server(fetcher=None, *, session: str | None = None) -> FastMCP:
    """Build one server with one reusable fetcher; injection enables offline tests."""
    if fetcher is None:
        from .webbridge import WebBridgeFetcher

        fetcher = WebBridgeFetcher(session=session)

    server = FastMCP(
        "xhs-note-fetcher",
        instructions=(
            "通过用户已连接的 Kimi 浏览器扩展读取小红书。先检查 xhs_status，"
            "从页面发现完整链接后批量读取；保留分享链接参数。图文详情包括正文、"
            "图片 URL 和尺寸，不包括图片下载、OCR 或评论翻页。性能测试设置 use_cache=false。"
        ),
        log_level="WARNING",
    )

    @server.tool(annotations=READ_ONLY)
    async def xhs_status() -> CallToolResult:
        """检查本地 Kimi WebBridge 的连接和浏览器状态。"""
        return await _invoke(fetcher.status)

    @server.tool(annotations=READ_ONLY)
    async def xhs_discover_notes(
        limit: Annotated[int, Field(ge=1, le=50, description="最多返回的笔记链接数量。")]=20,
    ) -> CallToolResult:
        """打开小红书首页并发现笔记分享链接；排除明确的视频，返回 notes 和 count。"""
        return await _invoke(fetcher.discover_notes, limit=limit)

    @server.tool(annotations=READ_ONLY)
    async def xhs_fetch_note(
        share_text: ShareText,
        timeout: Timeout = 10,
        use_cache: Annotated[bool, Field(description="是否复用本进程已获取的详情；测速保持 false。")]=False,
    ) -> CallToolResult:
        """读取一篇小红书图文详情：标题、正文、作者、互动数、图片 URL 和尺寸。

        图文指页面正文和图片信息，不下载图片、不做 OCR、不抓取评论翻页。
        保留完整分享链接和签名参数；访问失败返回结构化 error，isError=true。
        """
        return await _invoke(fetcher.fetch_note, share_text, timeout=timeout, use_cache=use_cache)

    @server.tool(annotations=READ_ONLY)
    async def xhs_fetch_notes(
        urls: Annotated[list[ShareText], Field(min_length=1, max_length=50, description="1–50 条完整分享链接或分享文案。")],
        timeout: Timeout = 10,
        use_cache: Annotated[bool, Field(description="是否复用详情缓存；测速保持 false。")]=False,
    ) -> CallToolResult:
        """批量读取图文详情，复用浏览器会话以减少 Agent 往返开销。

        每篇返回正文、图片 URL/尺寸及成功或错误状态；不含下载、OCR、评论翻页。
        返回 results、total、succeeded、elapsed_ms；全部失败时 isError=true。
        部分成功时 isError=false，必须同时检查每个 results 项目的 ok 和 error。
        """
        return await _invoke(fetcher.fetch_notes, urls, timeout=timeout, use_cache=use_cache)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xhs-note-mcp", description="Xiaohongshu detail MCP server over stdio (Kimi WebBridge).")
    parser.add_argument("--session", default=None, help="Kimi WebBridge session; defaults to XHS_WEBBRIDGE_SESSION or a new session.")
    args = parser.parse_args(argv)
    # The SDK owns stdout for JSON-RPC; logging is sent to stderr.
    create_server(session=args.session).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

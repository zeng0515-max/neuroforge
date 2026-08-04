"""把 ToolRegistry 暴露为标准 MCP Server（stdio 传输）。

可通过 `python -m app.tools.mcp_server` 启动，
供 Claude Desktop / Cursor 等外部 MCP 客户端连接并调用项目内置工具。

注意：stdout 是 MCP 通信通道，所有日志必须输出到 stderr。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from app.tools.tool_registry import tool_registry

logger = logging.getLogger(__name__)

# MCP Server 名称与版本（对外展示给客户端）
SERVER_NAME = "multi-agent-code-system"
SERVER_VERSION = "0.1.0"


def _to_text_content(result: Any) -> types.TextContent:
    """把工具执行结果序列化为 TextContent。

    - dict/list → json.dumps 序列化
    - str       → 原样使用
    - 其他      → str() 转字符串
    """
    if isinstance(result, str):
        text = result
    elif isinstance(result, (dict, list)):
        text = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        text = str(result)
    return types.TextContent(type="text", text=text)


def create_server() -> Server:
    """创建并配置 MCP Server 实例，注册 list_tools / call_tool 处理器。"""
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        # 遍历 tool_registry 中所有工具，转换为 MCP Tool 格式。
        # to_openai_tools() 返回项的 function.parameters 已是 JSON Schema，可直接作为 inputSchema。
        tools: list[types.Tool] = []
        for item in tool_registry.to_openai_tools():
            fn = item.get("function", {})
            tools.append(
                types.Tool(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    inputSchema=fn.get("parameters", {"type": "object", "properties": {}}),
                )
            )
        logger.info("列出工具：%d 个", len(tools))
        return tools

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict
    ) -> list[types.TextContent] | types.CallToolResult:
        # 调用 tool_registry 执行工具；异常时返回 isError=True 的错误结果。
        logger.info("调用工具：%s，参数：%s", name, arguments)
        try:
            result = tool_registry.call(name, arguments or {})
            return [_to_text_content(result)]
        except Exception as e:
            logger.exception("调用工具 %s 失败：%s", name, e)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"工具执行失败：{e}")],
                isError=True,
            )

    return server


async def main() -> None:
    """通过 stdio 传输启动 MCP Server。"""
    # 日志输出到 stderr，避免污染 stdout（stdout 是 MCP 通信通道）
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

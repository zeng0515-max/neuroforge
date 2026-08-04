"""MCP Client：连接外部 MCP Server（stdio 传输），自动发现工具并桥接进 ToolRegistry。

通过 stdio 启动外部 MCP Server 子进程，发现其暴露的工具，并把每个工具注册为
ToolRegistry 中的同步工具（桥接名格式：``mcp__{server_name}__{tool_name}``），
让 Agent 可以像调用内置工具一样调用外部 MCP 工具。

异步/同步边界说明
----------------
- ToolRegistry 的 handler 是同步函数，但 MCP 调用是异步的。
- ``connect`` 在调用方的 event loop 中创建 session 并捕获该 loop；session 的所有
  后续操作（call_tool / 关闭 context manager）都必须在同一个 loop 上执行
  （BaseSession 的 receive_loop 与请求/响应流绑定到创建时的 loop）。
- 桥接函数是同步的：当从同步线程（如 ``run_in_executor`` 中的 Agent）调用时，
  通过 ``asyncio.run_coroutine_threadsafe`` 把协程投递到 session 所属 loop 并阻塞
  等待结果；当没有运行中的 loop 时回退到 ``asyncio.run``。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.tools.tool_registry import ToolParamSpec, ToolRegistry, ToolSpec, tool_registry

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP 客户端：管理多个外部 MCP Server 连接，并把它们的工具桥接进 ToolRegistry。"""

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self._registry: ToolRegistry = registry or tool_registry
        self._sessions: Dict[str, ClientSession] = {}  # server_name -> session
        self._tools_map: Dict[str, Tuple[str, str]] = {}  # bridged_name -> (server_name, original_name)
        self._contexts: Dict[str, Tuple[Any, Any]] = {}  # server_name -> (stdio_cm, session_cm)
        self._loops: Dict[str, asyncio.AbstractEventLoop] = {}  # server_name -> session 所属 loop
        self._loop_threads: Dict[str, threading.Thread] = {}  # server_name -> connect 时的线程

    # ------------------------------------------------------------------ #
    #  连接
    # ------------------------------------------------------------------ #
    async def connect(
        self,
        server_name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """启动并连接一个外部 MCP Server，发现其工具并桥接进 registry。

        返回已桥接的工具名列表。
        """
        if server_name in self._sessions:
            raise ValueError(f"MCP server 已连接: {server_name}")

        # 捕获当前运行中的 event loop；session 将绑定到此 loop
        loop = asyncio.get_running_loop()
        params = StdioServerParameters(command=command, args=args or [], env=env)

        logger.info(
            "正在连接 MCP server '%s'：%s %s",
            server_name, command, " ".join(args or []),
        )

        # 手动进入 async context manager，以便在 connect 返回后保持 session 存活
        stdio_cm = stdio_client(params)
        read_stream, write_stream = await stdio_cm.__aenter__()
        session_cm = ClientSession(read_stream, write_stream)
        session = await session_cm.__aenter__()
        try:
            await session.initialize()
            result = await session.list_tools()
        except Exception:
            # 初始化/列举失败：清理已打开的 context manager
            await self._safe_exit(session_cm)
            await self._safe_exit(stdio_cm)
            raise

        # 保存连接状态
        self._sessions[server_name] = session
        self._contexts[server_name] = (stdio_cm, session_cm)
        self._loops[server_name] = loop
        self._loop_threads[server_name] = threading.current_thread()

        # 桥接每个外部工具
        bridged_names: List[str] = []
        try:
            for tool in result.tools:
                bridged_name = f"mcp__{server_name}__{tool.name}"
                self._make_bridge(
                    bridged_name=bridged_name,
                    input_schema=tool.inputSchema,
                    description=tool.description,
                    server_name=server_name,
                    original_name=tool.name,
                )
                bridged_names.append(bridged_name)
        except Exception:
            # 桥接失败：回滚已注册工具 + 关闭连接
            for bn in bridged_names:
                self._tools_map.pop(bn, None)
                self._registry._tools.pop(bn, None)
            self._sessions.pop(server_name, None)
            self._contexts.pop(server_name, None)
            self._loops.pop(server_name, None)
            self._loop_threads.pop(server_name, None)
            await self._safe_exit(session_cm)
            await self._safe_exit(stdio_cm)
            raise

        logger.info(
            "MCP server '%s' 已连接，桥接 %d 个工具：%s",
            server_name, len(result.tools), bridged_names,
        )
        return bridged_names

    # ------------------------------------------------------------------ #
    #  调用
    # ------------------------------------------------------------------ #
    async def call_external(
        self, bridged_name: str, kwargs: Optional[Dict[str, Any]] = None
    ) -> str:
        """异步调用一个已桥接的 MCP 工具。

        自动路由到 session 所属的 event loop 上执行，支持跨 loop 调用。
        """
        if bridged_name not in self._tools_map:
            raise KeyError(f"未知的 MCP 桥接工具: {bridged_name}")
        server_name = self._tools_map[bridged_name][0]
        return await self._run_on_loop(
            self._loops.get(server_name),
            self._call_external_impl(bridged_name, kwargs or {}),
        )

    async def _call_external_impl(
        self, bridged_name: str, kwargs: Dict[str, Any]
    ) -> str:
        """实际调用逻辑：必须在 session 所属 loop 上执行。"""
        if bridged_name not in self._tools_map:
            raise KeyError(f"未知的 MCP 桥接工具: {bridged_name}")
        server_name, original_name = self._tools_map[bridged_name]
        session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"MCP server 未连接: {server_name}")

        call_result = await session.call_tool(original_name, kwargs)

        # 从 content 提取文本（TextContent.text 拼接；其它类型转字符串兜底）
        text_parts: List[str] = []
        for item in call_result.content:
            text = getattr(item, "text", None)
            if text is not None:
                text_parts.append(text)
            else:
                text_parts.append(str(item))
        result_text = "\n".join(text_parts)

        if call_result.isError:
            raise ValueError(f"MCP 工具 {bridged_name} 返回错误: {result_text}")
        return result_text

    # ------------------------------------------------------------------ #
    #  断开
    # ------------------------------------------------------------------ #
    async def disconnect(self, server_name: str) -> None:
        """关闭指定 server 的 session 与 stdio 进程，并移除其桥接工具。"""
        if server_name not in self._contexts:
            return
        loop = self._loops.get(server_name)
        stdio_cm, session_cm = self._contexts.pop(server_name, (None, None))

        # 移除该 server 的所有桥接工具
        to_remove = [
            bn for bn, (sn, _) in list(self._tools_map.items()) if sn == server_name
        ]
        for bn in to_remove:
            self._tools_map.pop(bn, None)
            self._registry._tools.pop(bn, None)

        self._sessions.pop(server_name, None)
        self._loops.pop(server_name, None)
        self._loop_threads.pop(server_name, None)

        async def _close() -> None:
            await self._safe_exit(session_cm)
            await self._safe_exit(stdio_cm)

        # 在 session 所属 loop 上关闭 context manager
        await self._run_on_loop(loop, _close())
        logger.info("MCP server '%s' 已断开", server_name)

    async def disconnect_all(self) -> None:
        """关闭所有已连接的 MCP server。"""
        for server_name in list(self._contexts.keys()):
            await self.disconnect(server_name)

    # ------------------------------------------------------------------ #
    #  桥接函数构造
    # ------------------------------------------------------------------ #
    def _make_bridge(
        self,
        *,
        bridged_name: str,
        input_schema: Dict[str, Any],
        description: Optional[str],
        server_name: str,
        original_name: str,
    ) -> None:
        """为一个 MCP 工具创建同步桥接函数并注册到 registry。"""
        def bridge(**kwargs: Any) -> str:
            return self._call_sync(bridged_name, kwargs)

        bridge.__name__ = bridged_name
        bridge.__doc__ = description or f"MCP bridged tool: {bridged_name}"

        # 先以 **kwargs 注册（不会自动推导参数），随后手动修正 spec 与 pydantic_model
        self._registry.register(name=bridged_name, description=description)(bridge)

        entry = self._registry._tools.get(bridged_name)
        if entry is not None:
            params = self._build_params_from_schema(input_schema)
            entry.spec = ToolSpec(
                name=bridged_name,
                description=description or bridged_name,
                params=params,
            )
            # 跳过 pydantic 校验，原样转发参数给 MCP server
            # （避免本地 schema 与 MCP server 真实 schema 不一致导致误拦截）
            entry.pydantic_model = None

        self._tools_map[bridged_name] = (server_name, original_name)

    def _call_sync(self, bridged_name: str, kwargs: Dict[str, Any]) -> str:
        """同步桥接入口：把异步调用投递到 session 所属 loop 并阻塞等待。

        - session 所属 loop 正在运行（典型：Agent 在 run_in_executor 线程中调用）
          → 用 ``asyncio.run_coroutine_threadsafe`` 投递并 ``.result()`` 阻塞等待。
        - 没有运行中的 loop → 用 ``asyncio.run`` 兜底。
        """
        if bridged_name not in self._tools_map:
            raise KeyError(f"未知的 MCP 桥接工具: {bridged_name}")
        server_name = self._tools_map[bridged_name][0]
        loop = self._loops.get(server_name)

        if loop is not None and loop.is_running():
            # 防止在 session loop 自身线程内同步等待 → 死锁
            if threading.current_thread() is self._loop_threads.get(server_name):
                raise RuntimeError(
                    f"不能在 MCP server '{server_name}' 的 event loop 线程内同步调用桥接工具 "
                    f"'{bridged_name}'；请改用 await mcp_client.call_external(...)。"
                )
            future = asyncio.run_coroutine_threadsafe(
                self._call_external_impl(bridged_name, kwargs), loop
            )
            return future.result(timeout=None)

        # 没有运行中的 loop：用 asyncio.run 兜底（若 session 已失效会抛出异常）
        return asyncio.run(self._call_external_impl(bridged_name, kwargs))

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_params_from_schema(input_schema: Any) -> List[ToolParamSpec]:
        """从 MCP 工具的 inputSchema（JSON Schema）构建参数列表。"""
        params: List[ToolParamSpec] = []
        if not isinstance(input_schema, dict):
            return params
        props = input_schema.get("properties") or {}
        required = set(input_schema.get("required") or [])
        for pname, pschema in props.items():
            if not isinstance(pschema, dict):
                pschema = {}
            params.append(
                ToolParamSpec(
                    name=pname,
                    type=MCPClient._json_type(pschema.get("type", "string")),
                    required=pname in required,
                    description=pschema.get("description", "") or "",
                    default=pschema.get("default"),
                )
            )
        return params

    @staticmethod
    def _json_type(t: Any) -> str:
        """把 JSON Schema type 映射为 ToolParamSpec.type。"""
        # MCP/JSON Schema 的 type 可能是字符串，也可能是列表（如 ["string", "null"]）
        if isinstance(t, list):
            t = t[0] if t else "string"
        mapping = {
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "array": "array",
            "object": "object",
            "null": "string",
        }
        return mapping.get(t, "string")

    async def _run_on_loop(
        self, loop: Optional[asyncio.AbstractEventLoop], coro: Any
    ) -> Any:
        """在指定 loop 上运行协程；支持跨 loop 调用。

        - 当前已在该 loop 上 → 直接 await。
        - 该 loop 正在运行但当前不在该 loop → ``run_coroutine_threadsafe`` + ``wrap_future``。
        - 该 loop 未运行或为 None → 直接 await（兜底）。
        """
        if loop is None:
            return await coro
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            return await coro
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return await asyncio.wrap_future(future)
        return await coro

    @staticmethod
    async def _safe_exit(cm: Any) -> None:
        """安全退出一个 async context manager，吞掉异常并记日志。"""
        if cm is None:
            return
        try:
            await cm.__aexit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭 MCP context manager 时出错: %s", e)


__all__ = ["MCPClient"]

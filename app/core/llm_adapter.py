from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
import openai
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.outputs import ChatResult
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None  # type: ignore

from app.core.config import settings
from app.core.llm_providers import _resolve_provider

try:
    from app.core.observability import metrics_collector, alert_manager
except ImportError:
    metrics_collector = None  # type: ignore[assignment]
    alert_manager = None  # type: ignore[assignment]

try:
    from app.core.guardrails import output_guardrail
except ImportError:
    output_guardrail = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Bug P2 修复：对上游 LLM 厂商瞬时故障（连接/超时/限流）做重试退避
# openai SDK 的这些异常是瞬时性的，重试通常能恢复
_RETRYABLE: Tuple[type, ...] = (
    openai.APIError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    httpx.TransportError,
    httpx.TimeoutException,
)


@dataclass
class LLMUsage:
    """LLM 调用用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "LLMUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


@dataclass
class ChatResponse:
    """统一的聊天响应结构。"""

    content: str
    raw: Any = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    model: str = ""


def _build_llm(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> ChatOpenAI:
    if ChatOpenAI is None:
        raise ImportError("langchain-openai is not installed")
    # httpx.Timeout 设置 connect/read 超时防止无限挂起
    # connect=15s: DNS+TCP+SSL 握手 / read=90s: 首字节响应 / write=15s: 请求体发送
    _llm_timeout = httpx.Timeout(connect=15.0, read=90.0, write=15.0, pool=10.0)
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=_llm_timeout,
        max_retries=1,
        # DashScope qwen3 系列默认开启 thinking，长任务会长时间无响应
        # 关闭 thinking 以获得确定性、低延迟的输出
        extra_body={"enable_thinking": False},
    )


class MultiModelRouter:
    """多模型统一路由：按任务类型自动选择模型。

    路由规则：
    - code_gen / test_gen → code_model（代码专用模型，更低温度更高 max_tokens）
    - review / chat → main_model（主模型，通用对话）
    - 支持 fallback：主模型失败时自动降级到备用模型

    规则配置格式："task_type:model_role" 逗号分隔，
    model_role 为 "code"（用 code_model）或 "main"（用 main_model）。
    """

    def __init__(self) -> None:
        self._rules: Dict[str, bool] = {}  # task_type -> use_code_model
        self._parse_rules()

    def _parse_rules(self) -> None:
        """解析路由规则字符串。"""
        rules_str = settings.model_routing.rules
        for rule in rules_str.split(","):
            rule = rule.strip()
            if not rule:
                continue
            parts = rule.split(":")
            if len(parts) != 2:
                logger.warning("无效的路由规则：%s", rule)
                continue
            task_type, model_role = parts[0].strip(), parts[1].strip()
            self._rules[task_type] = model_role == "code"

    def resolve(self, task_type: str, fallback_model: Optional[str] = None) -> Dict[str, Any]:
        """根据任务类型解析模型选择。

        Args:
            task_type: 任务类型（code_gen / review / test_gen / chat）
            fallback_model: 可选，指定备用模型名

        Returns:
            {"use_code_model": bool, "model": Optional[str]}
        """
        use_code_model = self._rules.get(task_type, False)
        result: Dict[str, Any] = {
            "use_code_model": use_code_model,
            "model": fallback_model,
            "fallback_available": settings.model_routing.fallback_enabled,
        }
        return result

    def get_route_info(self) -> Dict[str, str]:
        """返回当前路由规则信息（用于展示）。"""
        return {task: "code" if use_code else "main" for task, use_code in self._rules.items()}


def _record_llm_metrics(
    model_name: str,
    usage: LLMUsage,
    start_time: float,
    agent: str = "",
) -> None:
    """记录 LLM 调用指标到 MetricsCollector。"""
    if metrics_collector is not None:
        duration_ms = (time.monotonic() - start_time) * 1000
        metrics_collector.record_llm_call(
            model=model_name,
            tokens=usage.total_tokens,
            duration_ms=duration_ms,
            agent=agent,
        )


class LLMAdapter:
    """
    模型调用抽象层（核心亮点）：
    - 统一主模型 / 代码模型两套入口
    - 支持同步/异步、流式/非流式、工具调用
    - 自动累计 token 用量
    - 当 API Key 缺失时降级为 DryRun，便于本地开发
    - 支持 MultiModelRouter 按任务类型自动路由
    - 支持语义缓存（SemanticCache）
    """

    def __init__(self) -> None:
        self.usage = LLMUsage()
        self._main: Optional[ChatOpenAI] = None
        self._code: Optional[ChatOpenAI] = None
        self.dry_run: bool = False
        self.init_error: str = ""
        # 当前主模型 / 代码模型名称（运行时可切换）
        self._current_main_model: str = settings.llm.model
        self._current_code_model: str = (
            settings.code_llm.model or settings.llm.model
        )
        self._router = MultiModelRouter()
        self._cache = None
        self._init_cache()
        self._build_clients()

    def _init_cache(self) -> None:
        """初始化语义缓存。"""
        try:
            from app.core.semantic_cache import semantic_cache
            self._cache = semantic_cache
        except Exception as e:
            logger.debug("语义缓存不可用（不影响主流程）：%s", e)
            self._cache = None

    @staticmethod
    def _is_real_key(value: Optional[str]) -> bool:
        if not value:
            return False
        v = value.strip().lower()
        blacklist = {
            "",
            "your_api_key_here",
            "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
            "none",
            "null",
            "placeholder",
            "xxx",
        }
        return v not in blacklist and len(value.strip()) >= 8

    def _build_clients(self) -> None:
        if not self._is_real_key(settings.llm.api_key):
            self.dry_run = True
            logger.warning("LLM_API_KEY 为空或占位符，进入 DryRun 模式，不会真正调用模型。")
            return
        try:
            self._main = _build_llm(
                base_url=settings.llm.base_url,
                api_key=settings.llm.api_key,
                model=self._current_main_model,
                temperature=settings.llm.temperature,
                max_tokens=settings.llm.max_tokens,
            )
            self._main_base_url = settings.llm.base_url
            self._main_api_key = settings.llm.api_key
            self._code = _build_llm(
                base_url=settings.code_llm.base_url,
                api_key=settings.code_llm.api_key,
                model=self._current_code_model,
                temperature=settings.code_llm.temperature,
                max_tokens=settings.code_llm.max_tokens,
            )
            self._code_base_url = settings.code_llm.base_url or settings.llm.base_url
            self._code_api_key = settings.code_llm.api_key or settings.llm.api_key
            self.dry_run = False
        except Exception as e:
            logger.error("初始化 LLM 客户端失败：%s", e)
            self._main = None
            self._code = None
            self.dry_run = True
            self.init_error = f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------ #
    #  模型管理：列表 / 切换
    # ------------------------------------------------------------------ #
    def reload_config(self) -> None:
        """热重载：重新读取 settings 并重建 LLM 客户端（页面内配置 API 后无需重启）。

        调用前需先更新 settings.llm / settings.code_llm 及 os.environ，
        此处仅重建客户端并同步 dry_run 状态。
        """
        self._current_main_model = settings.llm.model
        self._current_code_model = settings.code_llm.model or settings.llm.model
        self._build_clients()

    @property
    def current_model(self) -> str:
        """当前主模型名称（前端展示用）。"""
        return self._current_main_model

    @property
    def current_code_model(self) -> str:
        return self._current_code_model

    @property
    def base_url(self) -> str:
        return settings.llm.base_url

    @staticmethod
    def available_models() -> List[str]:
        """预设模型清单（基于 OpenAI 兼容协议常用模型 + 当前默认）。"""
        return [
            "qwen3.7-max",
            "qwen-max",
            "qwen-plus",
            "qwen-turbo",
            "qwen-coder-plus",
            "deepseek-v3",
            "deepseek-r1",
            "doubao-seed-1-6-flash-250828",
            "doubao-seed-1-6-250615",
            "gpt-4o-mini",
            "gpt-4o",
        ]

    def switch_model(self, model: str, *, for_code: bool = False) -> Dict[str, str]:
        """运行时切换模型，不重启服务。"""
        if not model or not model.strip():
            raise ValueError("模型名称不能为空")
        model = model.strip()
        if self.dry_run:
            if for_code:
                self._current_code_model = model
            else:
                self._current_main_model = model
            logger.info("[DryRun] 切换模型为：%s (for_code=%s)", model, for_code)
            return {"status": "ok", "model": model, "mode": "dry-run"}

        try:
            if for_code:
                base_url, api_key, provider_name, key_url, is_fallback = _resolve_provider(model)
                self._code = _build_llm(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    temperature=settings.code_llm.temperature,
                    max_tokens=settings.code_llm.max_tokens,
                )
                self._code_base_url = base_url
                self._code_api_key = api_key
                self._current_code_model = model
            else:
                base_url, api_key, provider_name, key_url, is_fallback = _resolve_provider(model)
                self._main = _build_llm(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    temperature=settings.llm.temperature,
                    max_tokens=settings.llm.max_tokens,
                )
                self._main_base_url = base_url
                self._main_api_key = api_key
                self._current_main_model = model
            logger.info("切换模型为：%s (for_code=%s, base_url=%s, fallback=%s)", model, for_code, base_url, is_fallback)
            return {
                "status": "ok",
                "model": model,
                "for_code": str(for_code).lower(),
                "provider": provider_name,
                "api_key_url": key_url,
                "api_key_fallback": is_fallback,
            }
        except Exception as e:
            logger.error("切换模型失败：%s", e)
            raise RuntimeError(f"切换失败：{e}")

    # ------------------------------------------------------------------ #
    #  基础消息构造
    # ------------------------------------------------------------------ #
    @staticmethod
    def to_messages(
        *,
        system: Optional[str] = None,
        user: Optional[str] = None,
        history: Optional[List[BaseMessage]] = None,
    ) -> List[BaseMessage]:
        messages: List[BaseMessage] = []
        if system:
            messages.append(SystemMessage(content=system))
        if history:
            messages.extend(history)
        if user:
            messages.append(HumanMessage(content=user))
        return messages

    # ------------------------------------------------------------------ #
    #  Bug S3：per-task 模型解析
    # ------------------------------------------------------------------ #
    def _resolve_client(
        self,
        *,
        use_code_model: bool,
        model: Optional[str],
    ):
        """根据 use_code_model 与 model 参数解析出实际使用的 client。

        当 model 参数非空且非 DryRun 时，构建一个临时 client 用本任务指定模型，
        避免全局 switch_model 在并发任务间串扰；解析失败则回退到默认 client。
        """
        if model and not self.dry_run:
            try:
                base_url, api_key, _provider, _key_url, _is_fallback = _resolve_provider(model)
                if use_code_model:
                    temperature = settings.code_llm.temperature
                    max_tokens = settings.code_llm.max_tokens
                else:
                    temperature = settings.llm.temperature
                    max_tokens = settings.llm.max_tokens
                return _build_llm(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                logger.warning("per-task 模型 %s 解析失败，回退到默认 client：%s", model, e)
        return self._code if use_code_model else self._main

    def _used_model_name(self, *, use_code_model: bool, model: Optional[str]) -> str:
        """返回本次调用实际使用的模型名（用于 ChatResponse.model 字段）。"""
        if model:
            return model
        return self._current_code_model if use_code_model else self._current_main_model

    # ------------------------------------------------------------------ #
    #  Bug P2：带重试退避的内部调用方法
    #  对上游 LLM 厂商瞬时故障（APIError / 连接 / 超时 / 限流）做 3 次指数退避重试，
    #  reraise=True 确保最后一次失败抛出原始异常便于调用方处理。
    #  DryRun 模式不会进入这些方法（chat / achat 会提前返回 _dry_run）。
    # ------------------------------------------------------------------ #
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    def _invoke_sync(self, client: Any, messages: List[BaseMessage]) -> BaseMessage:
        """同步调用 LLM，硬超时 120s（线程池兜底，防止 httpx timeout 失效导致无限挂起）。"""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.invoke, messages)
            try:
                return future.result(timeout=120)
            except concurrent.futures.TimeoutError:
                logger.error("LLM 调用超时（120s），强制中断")
                raise TimeoutError("LLM 调用超时（120s），DashScope API 无响应") from None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    async def _ainvoke_async(self, client: Any, messages: List[BaseMessage]) -> BaseMessage:
        """异步调用 LLM，带重试。"""
        return await client.ainvoke(messages)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    async def _astream_open(
        self, client: Any, messages: List[BaseMessage]
    ) -> Tuple[Any, Optional[Any]]:
        """打开流式连接并获取首个 chunk，失败可重试。

        返回 (async_iterator, first_chunk)；first_chunk 为 None 表示空流。
        """
        aiter = client.astream(messages)
        try:
            first = await aiter.__anext__()
        except StopAsyncIteration:
            return aiter, None
        return aiter, first

    @property
    def router(self) -> MultiModelRouter:
        """获取多模型路由器。"""
        return self._router

    # ------------------------------------------------------------------ #
    #  语义缓存辅助
    # ------------------------------------------------------------------ #
    def _check_cache(self, messages: List[BaseMessage]) -> Optional[str]:
        """检查语义缓存，返回缓存内容或 None。"""
        if self._cache is None or self.dry_run:
            return None
        # 提取最后一条 HumanMessage 作为查询键
        query_text = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                query_text = str(m.content)[:500]
                break
        if not query_text:
            return None
        try:
            cached = self._cache.lookup(query_text, model=self._current_main_model)
            return cached
        except Exception as e:
            logger.debug("语义缓存检查失败：%s", e)
            return None

    def _store_cache(self, messages: List[BaseMessage], response: str) -> None:
        """将请求-响应存入语义缓存。"""
        if self._cache is None or not response or self.dry_run:
            return
        query_text = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                query_text = str(m.content)[:500]
                break
        if not query_text:
            return
        try:
            self._cache.store(query_text, response, model=self._current_main_model)
        except Exception as e:
            logger.debug("语义缓存存储失败：%s", e)

    # ------------------------------------------------------------------ #
    #  输出护栏检查
    # ------------------------------------------------------------------ #
    def _check_output_guardrail(self, response: ChatResponse) -> ChatResponse:
        """对 LLM 输出做安全检测和脱敏。

        检测 API Key 泄露、文件路径泄露、代码执行指令等。
        对 CRITICAL 级别问题自动脱敏并触发告警。
        """
        if output_guardrail is None or not response.content:
            return response

        result = output_guardrail.check(response.content)
        if result.passed:
            return response

        # 按严重程度分类统计
        critical_count = sum(1 for i in result.issues if i.get("severity") == "CRITICAL")
        if critical_count > 0:
            logger.warning(
                "LLM 输出护栏检测到 %d 个致命问题，%d 个警告",
                critical_count,
                len(result.issues) - critical_count,
            )
            # 触发告警
            if alert_manager is not None:
                alert_manager.alert(
                    rule_name="output_guardrail",
                    level="CRITICAL",
                    message=f"LLM 输出中发现 {critical_count} 个敏感内容: "
                            f"{', '.join(i['type'] for i in result.issues[:3])}",
                    details={"issues": result.issues, "model": response.model},
                )

        # 如果有脱敏版本则使用
        content = result.sanitized_text if result.sanitized else response.content
        return ChatResponse(
            content=content,
            raw=response.raw,
            tool_calls=response.tool_calls,
            usage=response.usage,
            model=response.model,
        )

    # ------------------------------------------------------------------ #
    #  同步调用
    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: List[BaseMessage],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        use_code_model: bool = False,
        model: Optional[str] = None,
        task_type: str = "chat",
    ) -> ChatResponse:
        # 语义缓存检查
        if not tools and not model:
            cached = self._check_cache(messages)
            if cached is not None:
                logger.info("语义缓存命中，跳过 LLM 调用")
                return ChatResponse(
                    content=cached,
                    model="cache",
                    usage=LLMUsage(),
                )

        # 路由解析
        route = self._router.resolve(task_type, fallback_model=model)
        use_code = route["use_code_model"] if not use_code_model else use_code_model

        client = self._resolve_client(use_code_model=use_code, model=model)
        if client is None:
            return self._dry_run(messages)

        _start = time.monotonic()
        model_name = self._used_model_name(use_code_model=use_code, model=model)

        try:
            if tools:
                client = client.bind_tools(tools)
            result: BaseMessage = self._invoke_sync(client, messages)
            usage = self._extract_usage(result)
            self.usage.add(usage)
            tool_calls = getattr(result, "tool_calls", None) or []

            # 指标埋点
            _record_llm_metrics(model_name, usage, _start)

            response = ChatResponse(
                content=str(result.content) if result.content else "",
                raw=result,
                tool_calls=list(tool_calls),
                usage=usage,
                model=model_name,
            )
            # 存入缓存
            if not tools:
                self._store_cache(messages, response.content)
            # 输出护栏检查
            response = self._check_output_guardrail(response)
            return response
        except Exception as e:
            logger.exception("LLM 同步调用失败: %s", e)
            # fallback：如果当前用的是 code_model 且路由允许 fallback，尝试用 main_model
            if use_code and route["fallback_available"] and not model:
                logger.warning("code_model 调用失败，尝试 fallback 到 main_model：%s", e)
                return self.chat(
                    messages, tools=tools,
                    use_code_model=False, model=model,
                    task_type=task_type,
                )
            # 所有重试/fallback 均已耗尽 → 降级为 DryRun 模式返回占位内容
            logger.error("LLM 所有调用路径均失败，降级为 DryRun 响应: %s", e)
            return self._dry_run(messages)

    # ------------------------------------------------------------------ #
    #  异步调用
    # ------------------------------------------------------------------ #
    async def achat(
        self,
        messages: List[BaseMessage],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        use_code_model: bool = False,
        model: Optional[str] = None,
        task_type: str = "chat",
    ) -> ChatResponse:
        # 语义缓存检查
        if not tools and not model:
            cached = self._check_cache(messages)
            if cached is not None:
                logger.info("语义缓存命中，跳过 LLM 调用")
                return ChatResponse(
                    content=cached,
                    model="cache",
                    usage=LLMUsage(),
                )

        # 路由解析
        route = self._router.resolve(task_type, fallback_model=model)
        use_code = route["use_code_model"] if not use_code_model else use_code_model

        client = self._resolve_client(use_code_model=use_code, model=model)
        if client is None:
            return self._dry_run(messages)

        _start = time.monotonic()
        model_name = self._used_model_name(use_code_model=use_code, model=model)

        try:
            if tools:
                client = client.bind_tools(tools)
            result: BaseMessage = await self._ainvoke_async(client, messages)
            usage = self._extract_usage(result)
            self.usage.add(usage)
            tool_calls = getattr(result, "tool_calls", None) or []

            # 指标埋点
            _record_llm_metrics(model_name, usage, _start)

            response = ChatResponse(
                content=str(result.content) if result.content else "",
                raw=result,
                tool_calls=list(tool_calls),
                usage=usage,
                model=model_name,
            )
            # 存入缓存
            if not tools:
                self._store_cache(messages, response.content)
            # 输出护栏检查
            response = self._check_output_guardrail(response)
            return response
        except Exception as e:
            logger.exception("LLM 异步调用失败: %s", e)
            # fallback：如果当前用的是 code_model 且路由允许 fallback，尝试用 main_model
            if use_code and route["fallback_available"] and not model:
                logger.warning("code_model 调用失败，尝试 fallback 到 main_model：%s", e)
                return await self.achat(
                    messages, tools=tools,
                    use_code_model=False, model=model,
                    task_type=task_type,
                )
            # 所有重试/fallback 均已耗尽 → 降级为 DryRun
            logger.error("LLM 异步调用所有路径均失败，降级为 DryRun: %s", e)
            return self._dry_run(messages)

    # ------------------------------------------------------------------ #
    #  流式
    # ------------------------------------------------------------------ #
    async def astream(
        self,
        messages: List[BaseMessage],
        *,
        use_code_model: bool = False,
    ) -> AsyncIterator[str]:
        client = self._code if use_code_model else self._main
        if client is None:
            yield "[DryRun] 未配置 API Key，流式调用跳过。"
            return
        try:
            # Bug P2：流式首字节重试——连接建立 + 首 chunk 失败时自动重试
            aiter, first_chunk = await self._astream_open(client, messages)
            if first_chunk is not None:
                content = getattr(first_chunk, "content", "") or ""
                if content:
                    yield content
            async for chunk in aiter:
                content = getattr(chunk, "content", "") or ""
                if content:
                    yield content
        except Exception as e:
            logger.exception("LLM 流式调用失败: %s", e)
            raise

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_usage(msg: BaseMessage) -> LLMUsage:
        usage_meta = getattr(msg, "usage_metadata", None) or {}
        if not usage_meta:
            return LLMUsage()
        try:
            return LLMUsage(
                prompt_tokens=int(usage_meta.get("input_tokens", 0) or usage_meta.get("prompt_tokens", 0)),
                completion_tokens=int(usage_meta.get("output_tokens", 0) or usage_meta.get("completion_tokens", 0)),
                total_tokens=int(usage_meta.get("total_tokens", 0)),
            )
        except Exception:
            return LLMUsage()

    # ------------------------------------------------------------------ #
    #  方案B：结构化输出（Pydantic 校验）
    # ------------------------------------------------------------------ #
    def chat_structured(
        self,
        messages: List[BaseMessage],
        response_model: type,
        *,
        use_code_model: bool = False,
        model: Optional[str] = None,
    ):
        """结构化输出：让 LLM 返回 JSON 并用 Pydantic 模型校验。

        实现策略：
        1. 优先用 OpenAI response_format=json_object（DashScope/DeepSeek/OpenAI 都支持）
           - 在 system prompt 末尾追加 JSON Schema 提示，引导模型输出符合结构
        2. 收到响应后用 response_model.model_validate_json 校验
        3. 校验失败时抛 ValueError，由调用方决定是否回退到正则解析

        参数：
            messages: 消息列表
            response_model: Pydantic 模型类（用于校验输出）
            use_code_model: 是否用代码模型
            model: per-task 指定模型（Bug S3），非空时覆盖默认 client

        返回：(parsed_model, ChatResponse)
            parsed_model: response_model 的实例
            ChatResponse: 原始响应（含 content/usage 等，便于记账）

        失败时：
            - DryRun 模式返回 None（调用方需自行 fallback）
            - JSON 解析或 Pydantic 校验失败抛 ValueError
        """
        from pydantic import BaseModel, ValidationError

        if not (isinstance(response_model, type) and issubclass(response_model, BaseModel)):
            raise ValueError("response_model 必须是 Pydantic BaseModel 子类")

        client = self._resolve_client(use_code_model=use_code_model, model=model)
        if client is None:
            # DryRun 模式无法真正结构化输出，返回 None 让调用方 fallback
            return None, self._dry_run(messages)

        # 在 system message 末尾追加 JSON Schema 提示
        schema = response_model.model_json_schema()
        schema_hint = (
            "\n\n**重要：必须输出严格符合以下 JSON Schema 的 JSON 对象，不要包含任何其他文字、"
            "不要用 markdown 代码块包裹：**\n```json\n"
            + json.dumps(schema, ensure_ascii=False, indent=2)
            + "\n```"
        )
        augmented_messages: List[BaseMessage] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                augmented_messages.append(SystemMessage(content=str(m.content) + schema_hint))
            else:
                augmented_messages.append(m)
        # 如果没有 system message，补一个
        if not any(isinstance(m, SystemMessage) for m in augmented_messages):
            augmented_messages.insert(0, SystemMessage(content=schema_hint.strip()))

        try:
            # 用 bind(response_format={"type": "json_object"}) 强制 JSON 输出
            # 这是 OpenAI / DashScope / DeepSeek 都兼容的最稳方式
            json_client = client.bind(response_format={"type": "json_object"})
            result: BaseMessage = json_client.invoke(augmented_messages)
            usage = self._extract_usage(result)
            self.usage.add(usage)
            content = str(result.content) if result.content else ""
            chat_resp = ChatResponse(
                content=content,
                raw=result,
                usage=usage,
                model=self._used_model_name(use_code_model=use_code_model, model=model),
            )
            # 解析 + Pydantic 校验
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                raise ValueError(f"LLM 输出不是合法 JSON：{e}\n内容前200：{content[:200]}") from e
            try:
                parsed = response_model.model_validate(data)
            except ValidationError as e:
                raise ValueError(
                    f"LLM 输出不符合 {response_model.__name__} schema：{e}\n内容前200：{content[:200]}"
                ) from e
            # 输出护栏检查
            chat_resp = self._check_output_guardrail(chat_resp)
            return parsed, chat_resp
        except ValueError:
            # 校验失败直接抛给调用方
            raise
        except Exception as e:
            logger.exception("chat_structured 调用失败：%s", e)
            raise

    def _dry_run(self, messages: List[BaseMessage]) -> ChatResponse:
        preview = " | ".join(
            f"{type(m).__name__}:{str(m.content)[:40]}"
            for m in messages[-3:]
        )
        content = f"[DryRun] No-Op LLM response. last_msgs={preview}"
        return ChatResponse(
            content=content,
            model="dry-run",
        )

    def reset_usage(self) -> None:
        self.usage = LLMUsage()


# 全局单例，全局共享用量统计
llm = LLMAdapter()

__all__ = [
    "llm",
    "LLMAdapter",
    "ChatResponse",
    "LLMUsage",
    "MultiModelRouter",
]

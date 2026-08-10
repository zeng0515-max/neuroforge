from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

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
from app.core.llm_providers import _PROVIDER_REGISTRY, _resolve_provider

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
    _llm_timeout = httpx.Timeout(connect=15.0, read=90.0, write=15.0, pool=10.0)
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=_llm_timeout,
        max_retries=1,
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
        self._rules: Dict[str, bool] = {}
        self._parse_rules()

    def _parse_rules(self) -> None:
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
        use_code_model = self._rules.get(task_type, False)
        result: Dict[str, Any] = {
            "use_code_model": use_code_model,
            "model": fallback_model,
            "fallback_available": settings.model_routing.fallback_enabled,
        }
        return result

    def get_route_info(self) -> Dict[str, str]:
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


class _TokenAccumulator:
    """将流式 token 累积为完整的 chunk（句子/段落/代码块）。

    缓冲 token 直到检测到 chunk 边界（句末标点、代码块闭合、或达到最大长度），
    然后产出完整 chunk 并可选回调。
    """

    _SENTENCE_END = re.compile(r'[。！？.!?。！？]\s*$')

    def __init__(self, callback: Optional[Callable[[str], None]] = None) -> None:
        self._buffer: List[str] = []
        self._callback = callback

    def push(self, token: str) -> Optional[str]:
        """推入一个 token；若检测到 chunk 边界则返回完整 chunk，否则返回 None。"""
        self._buffer.append(token)
        text = "".join(self._buffer)
        if self._is_chunk_complete(text):
            chunk = text
            self._buffer.clear()
            if self._callback:
                try:
                    self._callback(chunk)
                except Exception:
                    pass
            return chunk
        return None

    def flush(self) -> Optional[str]:
        """将剩余缓冲作为最终 chunk 输出。"""
        if self._buffer:
            chunk = "".join(self._buffer)
            self._buffer.clear()
            if self._callback:
                try:
                    self._callback(chunk)
                except Exception:
                    pass
            return chunk
        return None

    def reset(self) -> None:
        """清空缓冲但不产出。"""
        self._buffer.clear()

    @classmethod
    def _is_chunk_complete(cls, text: str) -> bool:
        if text.startswith('```'):
            if text.count('```') >= 2 and len(text) > 10:
                return True
            if len(text) >= 512:
                return True
            return False
        if cls._SENTENCE_END.search(text):
            return True
        if len(text) >= 256:
            return True
        return False


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
        self.quota_exhausted: bool = False
        self.quota_detail: str = ""
        self._quota_state_file = Path(__file__).resolve().parent.parent / "runtime" / ".quota_state.json"
        self._current_main_model: str = settings.llm.model
        self._current_code_model: str = (
            settings.code_llm.model or settings.llm.model
        )
        self._router = MultiModelRouter()
        self._cache = None
        self._init_cache()
        self._restore_quota_state()
        self._build_clients()

    def _save_quota_state(self) -> None:
        try:
            self._quota_state_file.parent.mkdir(parents=True, exist_ok=True)
            self._quota_state_file.write_text(json.dumps({
                "quota_exhausted": self.quota_exhausted,
                "quota_detail": self.quota_detail,
                "saved_at": time.time(),
            }))
        except Exception:
            pass

    def _restore_quota_state(self) -> None:
        try:
            if self._quota_state_file.exists():
                data = json.loads(self._quota_state_file.read_text())
                self.quota_exhausted = data.get("quota_exhausted", False)
                self.quota_detail = data.get("quota_detail", "")
        except Exception:
            pass

    def _clear_quota_state(self) -> None:
        self.quota_exhausted = False
        self.quota_detail = ""
        try:
            if self._quota_state_file.exists():
                self._quota_state_file.unlink()
        except Exception:
            pass

    def _init_cache(self) -> None:
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
            self._restore_quota_state()
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
            self._restore_quota_state()
        except Exception as e:
            logger.error("初始化 LLM 客户端失败：%s", e)
            self._main = None
            self._code = None
            self.dry_run = True
            self.init_error = f"{type(e).__name__}: {e}"
            self._restore_quota_state()

    # ------------------------------------------------------------------ #
    #  模型管理：列表 / 切换
    # ------------------------------------------------------------------ #
    def reload_config(self) -> None:
        self._current_main_model = settings.llm.model
        self._current_code_model = settings.code_llm.model or settings.llm.model
        self._build_clients()
        self._clear_quota_state()

    @property
    def current_model(self) -> str:
        return self._current_main_model

    @property
    def current_code_model(self) -> str:
        return self._current_code_model

    @property
    def base_url(self) -> str:
        return settings.llm.base_url

    @staticmethod
    def available_models() -> List[str]:
        """Registered models plus whatever is currently configured."""
        models = set(_PROVIDER_REGISTRY.keys())
        for configured in (settings.llm.model, settings.code_llm.model or settings.llm.model):
            if configured:
                models.add(configured)
        return sorted(models)

    def switch_model(self, model: str, *, for_code: bool = False) -> Dict[str, str]:
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
        if model:
            return model
        return self._current_code_model if use_code_model else self._current_main_model

    # ------------------------------------------------------------------ #
    #  Bug P2：带重试退避的内部调用方法
    # ------------------------------------------------------------------ #
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    def _invoke_sync(self, client: Any, messages: List[BaseMessage]) -> BaseMessage:
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
        aiter = client.astream(messages)
        try:
            first = await aiter.__anext__()
        except StopAsyncIteration:
            return aiter, None
        return aiter, first

    @property
    def router(self) -> MultiModelRouter:
        return self._router

    # ------------------------------------------------------------------ #
    #  语义缓存辅助
    # ------------------------------------------------------------------ #
    def _check_cache(self, messages: List[BaseMessage]) -> Optional[str]:
        if self._cache is None or self.dry_run:
            return None
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
        if output_guardrail is None or not response.content:
            return response

        result = output_guardrail.check(response.content)
        if result.passed:
            return response

        critical_count = sum(1 for i in result.issues if i.get("severity") == "CRITICAL")
        if critical_count > 0:
            logger.warning(
                "LLM 输出护栏检测到 %d 个致命问题，%d 个警告",
                critical_count,
                len(result.issues) - critical_count,
            )
            if alert_manager is not None:
                alert_manager.alert(
                    rule_name="output_guardrail",
                    level="CRITICAL",
                    message=f"LLM 输出中发现 {critical_count} 个敏感内容: "
                            f"{', '.join(i['type'] for i in result.issues[:3])}",
                    details={"issues": result.issues, "model": response.model},
                )

        content = result.sanitized_text if result.sanitized else response.content
        return ChatResponse(
            content=content,
            raw=response.raw,
            tool_calls=response.tool_calls,
            usage=response.usage,
            model=response.model,
        )

    # ------------------------------------------------------------------ #
    #  Token 用量追踪（stage 级别）
    # ------------------------------------------------------------------ #
    def _track_token_usage(
        self,
        stage: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
    ) -> None:
        """在 stage 级别记录 token 用量到 metrics_collector。"""
        if metrics_collector is not None:
            total = prompt_tokens + completion_tokens
            metrics_collector.record_llm_call_with_stage(
                stage=stage,
                model=model,
                tokens=total,
                duration_ms=duration_ms,
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
        stage: str = "default",
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

            # 指标埋点（兼容旧版 + stage 级别追踪）
            duration_ms = (time.monotonic() - _start) * 1000
            _record_llm_metrics(model_name, usage, _start)
            self._track_token_usage(
                stage=stage,
                model=model_name,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                duration_ms=duration_ms,
            )

            response = ChatResponse(
                content=str(result.content) if result.content else "",
                raw=result,
                tool_calls=list(tool_calls),
                usage=usage,
                model=model_name,
            )
            if not tools:
                self._store_cache(messages, response.content)
            response = self._check_output_guardrail(response)
            return response
        except Exception as e:
            logger.exception("LLM 同步调用失败: %s", e)
            self._emit_quota_error(e)
            if use_code and route["fallback_available"] and not model:
                logger.warning("code_model 调用失败，尝试 fallback 到 main_model：%s", e)
                return self.chat(
                    messages, tools=tools,
                    use_code_model=False, model=model,
                    task_type=task_type, stage=stage,
                )
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
        stage: str = "default",
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

            # 指标埋点（兼容旧版 + stage 级别追踪）
            duration_ms = (time.monotonic() - _start) * 1000
            _record_llm_metrics(model_name, usage, _start)
            self._track_token_usage(
                stage=stage,
                model=model_name,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                duration_ms=duration_ms,
            )

            response = ChatResponse(
                content=str(result.content) if result.content else "",
                raw=result,
                tool_calls=list(tool_calls),
                usage=usage,
                model=model_name,
            )
            if not tools:
                self._store_cache(messages, response.content)
            response = self._check_output_guardrail(response)
            return response
        except Exception as e:
            logger.exception("LLM 异步调用失败: %s", e)
            self._emit_quota_error(e)
            if use_code and route["fallback_available"] and not model:
                logger.warning("code_model 调用失败，尝试 fallback 到 main_model：%s", e)
                return await self.achat(
                    messages, tools=tools,
                    use_code_model=False, model=model,
                    task_type=task_type, stage=stage,
                )
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
        stream_mode: str = "token",
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> AsyncIterator[str]:
        """流式调用，支持 token/chunk 两种模式和 DryRun。

        Args:
            messages: 消息列表
            use_code_model: 是否使用代码模型
            stream_mode: "token" 逐 token 产出（默认，兼容旧行为）；
                         "chunk" 使用 _TokenAccumulator 累积为完整 chunk
            on_chunk: 可选回调，每个完整 chunk 产出后调用（仅 chunk 模式有效）
        """
        client = self._code if use_code_model else self._main
        if client is None:
            async for piece in self._dry_run_stream(messages):
                yield piece
            return

        try:
            aiter, first_chunk = await self._astream_open(client, messages)
            accumulator = _TokenAccumulator(callback=on_chunk) if stream_mode == "chunk" else None

            if first_chunk is not None:
                content = getattr(first_chunk, "content", "") or ""
                if content:
                    if accumulator is not None:
                        piece = accumulator.push(content)
                        if piece is not None:
                            yield piece
                    else:
                        yield content

            async for chunk in aiter:
                content = getattr(chunk, "content", "") or ""
                if content:
                    if accumulator is not None:
                        piece = accumulator.push(content)
                        if piece is not None:
                            yield piece
                    else:
                        yield content

            if accumulator is not None:
                remaining = accumulator.flush()
                if remaining is not None:
                    yield remaining
        except Exception as e:
            logger.exception("LLM 流式调用失败: %s", e)
            raise

    # ------------------------------------------------------------------ #
    #  带累积器的流式（高级接口）
    # ------------------------------------------------------------------ #
    async def astream_with_accumulator(
        self,
        messages: List[BaseMessage],
        *,
        use_code_model: bool = False,
        stream_mode: str = "chunk",
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> AsyncIterator[str]:
        """使用 _TokenAccumulator 的流式接口，产出完整 chunk 并回调。

        等价于 astream(..., stream_mode="chunk")，提供更明确的语义入口。
        当 stream_mode="token" 时退化为逐 token 产出（不累积）。

        Args:
            messages: 消息列表
            use_code_model: 是否使用代码模型
            stream_mode: "chunk"（默认，累积为完整 chunk）或 "token"（逐 token）
            on_chunk: 每个完整 chunk 的同步回调

        Yields:
            完整的文本 chunk（句子/段落/代码块）
        """
        async for piece in self.astream(
            messages,
            use_code_model=use_code_model,
            stream_mode=stream_mode,
            on_chunk=on_chunk,
        ):
            yield piece

    # ------------------------------------------------------------------ #
    #  DryRun 流式输出
    # ------------------------------------------------------------------ #
    async def _dry_run_stream(
        self, messages: List[BaseMessage]
    ) -> AsyncIterator[str]:
        """DryRun 模式的流式占位输出。

        将 _dry_run 生成的占位内容作为单个 chunk 产出，
        模拟一次性流式响应，使前端无需特殊处理即可展示。
        """
        response = self._dry_run(messages)
        content = response.content
        if not content:
            return
        yield content

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
            return None, self._dry_run(messages)

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
        if not any(isinstance(m, SystemMessage) for m in augmented_messages):
            augmented_messages.insert(0, SystemMessage(content=schema_hint.strip()))

        try:
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
            chat_resp = self._check_output_guardrail(chat_resp)
            return parsed, chat_resp
        except ValueError:
            raise
        except Exception as e:
            logger.exception("chat_structured 调用失败：%s", e)
            raise

    def _emit_quota_error(self, exc: Exception) -> None:
        try:
            is_quota_error = (
                isinstance(exc, openai.PermissionDeniedError)
                and ("Free quota exhausted" in str(exc) or "AllocationQuota" in str(exc))
            )
            if is_quota_error:
                self.quota_exhausted = True
                self.quota_detail = str(exc)[:300]
                self._save_quota_state()
                logger.error("API Key 免费额度已用尽：%s", exc)
                try:
                    from app.core.events import EventBus, EventType
                    event_bus = EventBus()
                    event_bus.emit(
                        EventType.NODE_FAILED,
                        data={
                            "node": "llm",
                            "node_name": "LLM 调用",
                            "error_type": "quota_exhausted",
                            "message": "API Key 免费额度已用完，请充值或更换 Key 后重试",
                            "detail": str(exc)[:300],
                        },
                        task_id="",
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _dry_run(self, messages: List[BaseMessage]) -> ChatResponse:
        """DryRun 模式：根据消息内容生成有意义的占位代码/回复。"""
        user_content = ""
        for m in reversed(messages):
            if isinstance(m, (HumanMessage, SystemMessage)):
                content = str(m.content)
                if content and len(content) > 5:
                    user_content = content
                    break

        code_keywords = ["代码", "实现", "python", "def ", "class ", "函数", "需求",
                         "FILE", "哈希表", "排序", "算法", "线程", "安全", "web",
                         "server", "API", "HTTP", "链表", "树", "图", "队列", "栈",
                         "排序", "二分", "递归", "迭代", "装饰器", "并发", "异步",
                         "async", "await", "数据库", "缓存", "ORM", "路由", "中间件"]
        is_code_request = any(kw.lower() in user_content.lower() for kw in code_keywords)

        if is_code_request:
            file_path = "main.py"
            path_match = re.search(r"###\s*FILE\s*[:：]\s*([^\s]+)", user_content)
            if path_match:
                file_path = path_match.group(1).strip()

            safe_summary = user_content[:80].replace("\n", " ").replace("\"", "'")
            content = f'''### FILE: {file_path}
"""
[DryRun] 占位代码 — API Key 未配置或额度已用尽。
以下为模板代码，请配置有效 API Key 后重新执行以获得真实代码生成。
"""

# TODO: 实现功能
# 原始需求: {safe_summary}


def placeholder():
    """占位函数"""
    print("Hello from NeuroForge DryRun mode!")
    print("请在 Dashboard 配置有效的 API Key 以获得真实代码生成。")


if __name__ == "__main__":
    placeholder()
'''
        else:
            safe_summary = user_content[:100] if user_content else "无内容"
            content = f"[DryRun] 当前处于 DryRun 模式（未配置有效 API Key 或额度已用尽）。\n请访问 Dashboard 配置页面配置 API Key。\n\n原始请求摘要：{safe_summary}"

        return ChatResponse(
            content=content,
            model="dry-run",
            usage=LLMUsage(),
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
    "_TokenAccumulator",
]

"""轻量级分布式追踪模块。

OpenTelemetry 作为可选依赖（try-import），未安装时降级为自研
trace_id + contextvars 方案，保证同一请求链路上的日志可串联。

设计原则：
- 节约依赖：不强制安装 opentelemetry-sdk，用 try-import 降级
- 零侵入：装饰器 + 上下文管理器模式，现有代码改动最小
- trace_id 注入日志：让每条日志都带 trace_id，便于全链路追踪
"""

import contextvars
import functools
import logging
import time
import uuid

# 尝试导入 OpenTelemetry，未安装时降级为自研实现
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 自研降级实现：基于 contextvars 的 trace_id 传播
# ---------------------------------------------------------------------------

# 全局 trace_id 上下文变量
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)


def _generate_trace_id() -> str:
    """生成 16 位 hex trace_id"""
    return uuid.uuid4().hex[:16]


def get_trace_id() -> str:
    """获取当前上下文的 trace_id，不存在则自动生成并写入"""
    tid = _trace_id_var.get()
    if not tid:
        tid = _generate_trace_id()
        _trace_id_var.set(tid)
    return tid


def set_trace_id(tid: str) -> None:
    """设置当前上下文的 trace_id"""
    _trace_id_var.set(tid)


# ---------------------------------------------------------------------------
# Span 上下文管理器
# ---------------------------------------------------------------------------


class _OTelSpanWrapper:
    """包装 OTel span，进入时注入 trace_id 到 contextvars。

    使用 OTel 原生的 ``start_as_current_span`` 上下文管理器激活 span，
    兼容 OTel SDK 1.x/2.x，避免 ``trace.use_span`` 在不同版本下的差异。
    """

    def __init__(self, tracer, name: str, attrs: dict):
        self._tracer = tracer
        self._name = name
        self._attrs = attrs
        self._cm = None
        self._span = None

    def __enter__(self):
        self._cm = self._tracer.start_as_current_span(
            self._name, attributes=self._attrs
        )
        self._span = self._cm.__enter__()
        # 注入 trace_id 到 contextvars，便于日志串联
        try:
            ctx = self._span.get_span_context()
            tid = format(ctx.trace_id, "032x")[:16]
            set_trace_id(tid)
        except Exception:
            # 获取 span context 失败不影响 span 本身的生命周期
            pass
        return self

    def __exit__(self, *exc):
        # exc = (exc_type, exc_value, exc_tb)；record_exception 需要异常实例
        if exc[1]:
            # 异常时记录异常并设置 ERROR 状态
            self._span.record_exception(exc[1])
            self._span.set_status(
                trace.Status(trace.StatusCode.ERROR, str(exc[1]))
            )
        return self._cm.__exit__(*exc)

    def set_attribute(self, key, value):
        if self._span:
            self._span.set_attribute(key, value)

    def record_exception(self, e):
        if self._span:
            self._span.record_exception(e)


class FallbackSpanContext:
    """自研降级 span 上下文管理器（无 OTel 时使用）"""

    def __init__(self, name: str, attrs: dict):
        self._name = name
        self._attrs = dict(attrs) if attrs else {}
        self._start_time = None

    def __enter__(self):
        self._start_time = time.perf_counter()
        logger.debug("span 开始: %s, attrs=%s", self._name, self._attrs)
        return self

    def __exit__(self, *exc):
        duration = time.perf_counter() - self._start_time
        if exc[0]:
            logger.warning(
                "span 失败: %s, duration=%.3fs, error=%s",
                self._name,
                duration,
                exc[0],
            )
        else:
            logger.debug("span 结束: %s, duration=%.3fs", self._name, duration)

    def set_attribute(self, key, value):
        self._attrs[key] = value

    def record_exception(self, e):
        self._attrs["error"] = str(e)


# ---------------------------------------------------------------------------
# TracingManager
# ---------------------------------------------------------------------------


class TracingManager:
    """追踪管理器：OTel 优先，自研降级"""

    def __init__(self, service_name: str = "multi-agent-code-system"):
        self._service_name = service_name
        self._enabled = False
        self._tracer = None

        if _OTEL_AVAILABLE:
            try:
                provider = TracerProvider(
                    resource=Resource.create({"service.name": service_name})
                )
                # ConsoleSpanExporter 输出到 stderr，不需要额外服务
                provider.add_span_processor(
                    BatchSpanProcessor(ConsoleSpanExporter())
                )
                trace.set_tracer_provider(provider)
                self._tracer = trace.get_tracer(service_name)
                self._enabled = True
                logger.info("OpenTelemetry tracing 已启用")
            except Exception as e:
                logger.warning("OTel 初始化失败，降级到自研 tracing: %s", e)
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start_span(self, name: str, **attributes):
        """开始一个 span，返回 Span 上下文管理器"""
        if self._enabled and self._tracer:
            return _OTelSpanWrapper(self._tracer, name, attributes)
        return FallbackSpanContext(name, attributes)

    def trace_span(self, name: str, **attrs):
        """装饰器：自动为函数创建 span"""

        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                with self.start_span(name, **attrs) as span:
                    try:
                        result = func(*args, **kwargs)
                        span.set_attribute("status", "OK")
                        return result
                    except Exception as e:
                        span.record_exception(e)
                        raise

            return wrapper

        return decorator


# ---------------------------------------------------------------------------
# 日志 trace_id 注入
# ---------------------------------------------------------------------------


class TraceIdLogFilter(logging.Filter):
    """日志过滤器：在每条 LogRecord 上注入 trace_id"""

    def filter(self, record):
        record.trace_id = get_trace_id()
        return True


# ---------------------------------------------------------------------------
# 全局单例 + 初始化函数
# ---------------------------------------------------------------------------

tracer = TracingManager()


def setup_tracing(
    service_name: str = "multi-agent-code-system", enabled: bool = True
):
    """初始化 tracing（在 app lifespan 中调用）"""
    global tracer
    if not enabled:
        return
    # 先注入日志 filter，再创建 TracingManager，确保后续 OTel 初始化
    # 日志也带 trace_id，避免含 %(trace_id)s 的 handler 格式化 KeyError。
    # 注意：filter 必须加到根 logger 的 handler 上，否则子 logger
    # 透传上来的记录不会经过根 logger 的 filter（callHandlers 不重跑
    # 父 logger 的 filter），会导致 %(trace_id)s 格式化 KeyError。
    root_logger = logging.getLogger()
    log_filter = TraceIdLogFilter()
    root_logger.addFilter(log_filter)
    for handler in root_logger.handlers:
        handler.addFilter(log_filter)
    tracer = TracingManager(service_name=service_name)


def trace_span(name: str, **attrs):
    """模块级便利装饰器：委托给全局 tracer 创建 span"""
    return tracer.trace_span(name, **attrs)


__all__ = [
    "tracer",
    "setup_tracing",
    "get_trace_id",
    "set_trace_id",
    "trace_span",
]

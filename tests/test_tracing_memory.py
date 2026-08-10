"""tracing 与长期记忆模块单元测试。

覆盖：
- app.core.tracing：trace_id 生成/设置/隔离、span 上下文管理器、装饰器、日志 filter
- app.core.memory：经验保存/检索/统计、空值容错、limit 截断
- app.core.config：TracingConfig / MemoryConfig 默认值与 settings 聚合

测试约束：
- 全部本地可运行，不依赖网络
- memory 测试使用临时 DB + mock RAG，避免污染真实数据
"""
from __future__ import annotations

import contextvars
import logging
from unittest.mock import MagicMock

import pytest

from app.core.config import MemoryConfig, TracingConfig, settings
from app.core.memory import MemoryManager
from app.core.tracing import (
    TraceIdLogFilter,
    TracingManager,
    get_trace_id,
    set_trace_id,
    setup_tracing,
    tracer,
)


# ================================================================== #
#  tracing: trace_id 基本行为
# ================================================================== #


def test_get_trace_id_generates_id():
    """首次调用 get_trace_id() 应自动生成非空 16 位 hex 字符串。"""
    # 先清空当前上下文 trace_id，模拟"首次调用"
    set_trace_id("")
    tid = get_trace_id()
    assert tid, "trace_id 不应为空"
    assert len(tid) == 16, "trace_id 应为 16 位"
    # 确认是合法 hex
    int(tid, 16)


def test_set_and_get_trace_id():
    """set_trace_id 设置后 get_trace_id 应返回相同值。"""
    set_trace_id("test123")
    assert get_trace_id() == "test123"


def test_trace_id_isolation():
    """不同 contextvar 上下文中 trace_id 应互相隔离。

    在 copy_context 的副本中修改 trace_id 不应泄漏回原上下文。
    """
    set_trace_id("outer-id")
    ctx = contextvars.copy_context()

    def run_in_inner():
        set_trace_id("inner-id")
        return get_trace_id()

    inner_tid = ctx.run(run_in_inner)
    assert inner_tid == "inner-id"
    # 内部上下文修改不应泄漏到外部
    assert get_trace_id() == "outer-id"


# ================================================================== #
#  tracing: span 上下文管理器
# ================================================================== #


def test_tracer_start_span():
    """start_span 返回的 span 应可作上下文管理器且具备 set_attribute / record_exception。"""
    with tracer.start_span("test_span") as span:
        assert span is not None
        assert hasattr(span, "set_attribute")
        assert hasattr(span, "record_exception")


def test_tracer_span_set_attribute():
    """在 span 内调用 set_attribute 不应抛异常。"""
    with tracer.start_span("test_attr") as span:
        span.set_attribute("key", "value")
        span.set_attribute("count", 42)


def test_tracer_span_with_exception():
    """span 内抛异常时异常应继续传播，不被吞掉。"""
    with pytest.raises(RuntimeError, match="span error"):
        with tracer.start_span("test_exception"):
            raise RuntimeError("span error")


# ================================================================== #
#  tracing: 装饰器
# ================================================================== #


def test_tracer_trace_span_decorator():
    """trace_span 装饰器对正常函数应返回原结果且不抛异常。"""
    @tracer.trace_span("test_func_ok")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


def test_tracer_trace_span_decorator_exception():
    """被装饰函数抛异常时异常应正确传播，不被装饰器吞掉。"""
    @tracer.trace_span("test_func_bad")
    def boom():
        raise ValueError("decorated boom")

    with pytest.raises(ValueError, match="decorated boom"):
        boom()


# ================================================================== #
#  tracing: 初始化与日志 filter
# ================================================================== #


def test_setup_tracing_disabled():
    """setup_tracing(enabled=False) 不应启用 OTel。"""
    setup_tracing(enabled=False)
    # 能执行到这里即说明未抛异常


def test_tracing_manager_disabled_by_default():
    """TracingManager 默认不应启用 OTel，避免测试输出 ConsoleSpanExporter JSON。"""
    assert TracingManager().enabled is False


def test_trace_id_log_filter():
    """TraceIdLogFilter 应在 LogRecord 上注入 trace_id 属性。"""
    set_trace_id("log-filter-id")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    flt = TraceIdLogFilter()
    assert flt.filter(record) is True
    assert record.trace_id == "log-filter-id"


# ================================================================== #
#  config 默认值
# ================================================================== #


def test_tracing_config_defaults():
    """TracingConfig 默认 enabled=False, service_name=multi-agent-code-system。"""
    cfg = TracingConfig()
    assert cfg.enabled is False
    assert cfg.service_name == "multi-agent-code-system"


def test_memory_config_defaults():
    """MemoryConfig 默认 enabled=True, recall_on_gen=True。"""
    cfg = MemoryConfig()
    assert cfg.enabled is True
    assert cfg.recall_on_gen is True


def test_settings_has_tracing_and_memory():
    """settings 应聚合 tracing 与 memory 配置。"""
    assert settings.tracing is not None
    assert settings.memory is not None
    assert isinstance(settings.tracing, TracingConfig)
    assert isinstance(settings.memory, MemoryConfig)


# ================================================================== #
#  memory: 临时 MemoryManager fixture
# ================================================================== #


@pytest.fixture()
def tmp_memory(tmp_path):
    """构造指向临时 DB 的 MemoryManager，并 mock 掉 RAG，避免污染真实数据。

    只测 SQLite 通道；RAG 通道被替换为返回空结果的 mock。
    tmp_path 由 pytest 自动创建并在测试后清理，无需手动删除 DB 文件。
    """
    db_path = str(tmp_path / "test_memory.db")
    mm = MemoryManager(db_path=db_path)
    # 替换 RAG：add_texts 无副作用，query 返回空列表
    mock_rag = MagicMock()
    mock_rag.add_texts.return_value = None
    mock_rag.query.return_value = []
    mm._rag = mock_rag
    return mm


def test_memory_save_and_recall(tmp_memory):
    """保存一条经验后，用关键词检索应能找到。"""
    mm = tmp_memory
    eid = mm.save_experience(
        task_id="task-1",
        user_request="实现用户登录功能",
        review_verdict="approved",
        review_score=90,
    )
    assert isinstance(eid, int) and eid > 0

    results = mm.recall_experience("登录")
    assert len(results) >= 1
    # SQLite 通道返回的结果应包含刚保存的记录
    sqlite_hits = [r for r in results if r.get("source") == "sqlite"]
    assert sqlite_hits, "应至少有一条来自 SQLite 的检索结果"
    hit = sqlite_hits[0]
    assert hit["task_id"] == "task-1"
    assert "登录" in hit["user_request"]


def test_memory_recall_no_match(tmp_memory):
    """检索不存在的关键词应返回空列表。"""
    mm = tmp_memory
    mm.save_experience(
        task_id="task-2",
        user_request="实现用户登录功能",
    )
    results = mm.recall_experience("完全不存在的关键词xyz")
    assert results == []


def test_memory_get_stats(tmp_memory):
    """保存经验后 get_stats 应返回正确统计。"""
    mm = tmp_memory
    mm.save_experience(
        task_id="t-a",
        user_request="需求A",
        review_verdict="approved",
        review_score=80,
    )
    mm.save_experience(
        task_id="t-b",
        user_request="需求B",
        review_verdict="rejected",
        review_score=50,
    )
    stats = mm.get_stats()
    assert stats["total_experiences"] == 2
    assert stats["approved_count"] == 1
    # approval_rate = 1/2 = 0.5
    assert stats["approval_rate"] == 0.5
    # 平均分（仅 score>0 的记录）：(80+50)/2 = 65
    assert stats["avg_review_score"] == 65.0


def test_memory_save_with_empty_values(tmp_memory):
    """保存空值不应抛异常。"""
    mm = tmp_memory
    eid = mm.save_experience(
        task_id="",
        user_request="",
        code_files=None,
        error_message="",
    )
    assert isinstance(eid, int) and eid > 0


def test_memory_recall_limit(tmp_memory):
    """保存多条经验后 recall_experience(limit=2) 最多返回 2 条。"""
    mm = tmp_memory
    # 保存 4 条都含"测试"关键词的经验，分数递增
    for i in range(4):
        mm.save_experience(
            task_id=f"t-{i}",
            user_request=f"测试需求编号{i}",
            review_score=60 + i,
        )
    results = mm.recall_experience("测试", limit=2)
    assert len(results) == 2

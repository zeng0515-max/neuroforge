"""统一编排集成测试。

覆盖:
- UnifiedOrchestrator.run() 完整流程
- UnifiedOrchestrator.stream_run() 流式输出
- 新旧架构切换（legacy_mode）
- UnifiedResult / StreamEvent / SystemStatus 数据类
- analyze() / decompose() / list_agents() 便捷方法
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List

import pytest

from app.unified_orchestrator import (
    StreamEvent,
    SystemStatus,
    UnifiedOrchestrator,
    UnifiedResult,
)


# ====================================================================== #
#  UnifiedResult / StreamEvent / SystemStatus —— 数据类
# ====================================================================== #


class TestDataClasses:
    """数据类基础属性。"""

    def test_unified_result_defaults(self):
        """UnifiedResult 默认值。"""
        result = UnifiedResult()
        assert result.success is False
        assert result.artifacts == {}
        assert result.execution_plan is None
        assert result.agent_outputs == {}
        assert result.execution_time == 0.0

    def test_unified_result_to_dict(self):
        """to_dict 序列化。"""
        result = UnifiedResult(success=True, execution_time=12.5)
        d = result.to_dict()
        assert d["success"] is True
        assert d["execution_time"] == 12.5

    def test_unified_result_to_dict_with_plan(self):
        """有执行计划时的序列化。"""
        from app.graph.orchestrator import ExecutionPlan
        plan = ExecutionPlan(execution_order=[["a"], ["b"]])
        result = UnifiedResult(
            success=True,
            execution_plan=plan,
            execution_time=5.0,
        )
        d = result.to_dict()
        assert d["execution_plan"] is not None
        assert d["execution_plan"]["total_tasks"] == 2

    def test_stream_event_to_dict(self):
        """StreamEvent 序列化。"""
        evt = StreamEvent(
            event_type="task_started",
            data={"task_id": "t1"},
            progress=30,
        )
        d = evt.to_dict()
        assert d["event_type"] == "task_started"
        assert d["progress"] == 30
        assert "timestamp" in d

    def test_stream_event_default_timestamp(self):
        """StreamEvent 应有默认时间戳。"""
        evt = StreamEvent(event_type="test")
        assert evt.timestamp is not None

    def test_system_status_to_dict(self):
        """SystemStatus 序列化。"""
        status = SystemStatus(
            engine="new",
            pipeline_phase="done",
            uptime_seconds=120.5,
        )
        d = status.to_dict()
        assert d["engine"] == "new"
        assert d["pipeline_phase"] == "done"
        assert d["uptime_seconds"] == 120.5


# ====================================================================== #
#  UnifiedOrchestrator.run() —— 完整流程
# ====================================================================== #


class TestUnifiedOrchestratorRun:
    """run() 一站式同步执行。"""

    @pytest.fixture
    def uo(self):
        """创建独立的 UnifiedOrchestrator 实例。"""
        return UnifiedOrchestrator()

    def test_run_simple_request(self, uo):
        """简单请求应成功执行。"""
        result = uo.run("实现一个简单的加法函数")
        assert isinstance(result, UnifiedResult)
        assert result.success is True
        assert result.execution_time > 0

    def test_run_with_options(self, uo):
        """带 options 执行。"""
        result = uo.run(
            "实现一个登录模块",
            options={"enable_memory": False},
        )
        assert result.success is True

    def test_run_with_preferred_strategy(self, uo):
        """指定执行策略。"""
        result = uo.run(
            "实现一个登录模块",
            options={"preferred_strategy": "sequential"},
        )
        assert result.success is True

    def test_run_produces_artifacts(self, uo):
        """执行结果应包含产物。"""
        result = uo.run("实现一个加法函数")
        assert isinstance(result.artifacts, dict)

    def test_run_produces_memory_summary(self, uo):
        """执行结果应包含记忆摘要。"""
        result = uo.run("实现一个加法函数")
        assert isinstance(result.memory_summary, dict)


# ====================================================================== #
#  UnifiedOrchestrator.stream_run() —— 流式输出
# ====================================================================== #


class TestUnifiedOrchestratorStream:
    """stream_run() 流式执行。"""

    @pytest.fixture
    def uo(self):
        return UnifiedOrchestrator()

    def test_stream_run_produces_events(self, uo):
        """stream_run 应产出 StreamEvent 序列。"""
        events = list(uo.stream_run("实现一个加法函数"))
        assert len(events) >= 3
        assert all(isinstance(e, StreamEvent) for e in events)

    def test_stream_run_starts_with_started(self, uo):
        """首个事件应为 execution_started。"""
        events = list(uo.stream_run("测试"))
        assert events[0].event_type == "execution_started"
        assert events[0].progress == 0

    def test_stream_run_ends_with_completed(self, uo):
        """末尾事件应为 execution_completed。"""
        events = list(uo.stream_run("测试"))
        assert events[-1].event_type == "execution_completed"
        assert events[-1].progress == 100

    def test_stream_run_has_requirement_events(self, uo):
        """应包含需求分析相关事件。"""
        events = list(uo.stream_run("实现一个登录模块"))
        event_types = {e.event_type for e in events}
        assert "requirement_analysis_started" in event_types
        assert "requirement_analysis_completed" in event_types

    def test_stream_run_has_decomposition_events(self, uo):
        """应包含任务拆解事件。"""
        events = list(uo.stream_run("实现一个登录模块"))
        event_types = {e.event_type for e in events}
        assert "task_decomposition_started" in event_types
        assert "task_decomposition_completed" in event_types

    def test_stream_run_progress_monotonic(self, uo):
        """progress 应单调递增。"""
        events = list(uo.stream_run("实现一个登录模块"))
        progresses = [e.progress for e in events]
        for i in range(1, len(progresses)):
            assert progresses[i] >= progresses[i - 1]

    def test_stream_run_final_success(self, uo):
        """最终事件的 success 字段。"""
        events = list(uo.stream_run("实现一个加法函数"))
        last = events[-1]
        assert last.data["success"] is True


# ====================================================================== #
#  新旧架构切换
# ====================================================================== #


class TestLegacyMode:
    """legacy_mode 切换测试。"""

    @pytest.fixture
    def uo(self):
        return UnifiedOrchestrator()

    def test_legacy_mode_run(self, uo):
        """legacy_mode 执行旧 pipeline。"""
        result = uo.run(
            "实现一个加法函数",
            options={"legacy_mode": True},
        )
        assert isinstance(result, UnifiedResult)
        assert result.execution_plan is None
        assert result.execution_time > 0

    def test_legacy_mode_stream(self, uo):
        """legacy_mode 流式执行。"""
        events = list(uo.stream_run(
            "实现一个加法函数",
            options={"legacy_mode": True},
        ))
        assert len(events) >= 1
        assert events[0].event_type == "execution_started"

    def test_new_mode_is_default(self, uo):
        """默认应走新引擎。"""
        result = uo.run("实现一个加法函数")
        assert result.execution_plan is not None

    def test_legacy_mode_failure_handling(self, uo):
        """legacy 模式异常处理。"""
        result = uo.run(
            "实现一个功能",
            options={
                "legacy_mode": True,
                "extra_args": {"_force_error": True},
            },
        )
        assert isinstance(result, UnifiedResult)


# ====================================================================== #
#  便捷方法
# ====================================================================== #


class TestConvenienceMethods:
    """analyze() / decompose() / list_agents() 等。"""

    @pytest.fixture
    def uo(self):
        return UnifiedOrchestrator()

    def test_analyze(self, uo):
        """analyze() 应返回 RequirementAnalysis。"""
        from app.graph.task_decomposer import RequirementAnalysis
        analysis = uo.analyze("实现一个登录模块")
        assert isinstance(analysis, RequirementAnalysis)
        assert analysis.goal == "实现一个登录模块"

    def test_decompose(self, uo):
        """decompose() 应返回 TaskTree。"""
        from app.graph.task_decomposer import TaskTree
        tree = uo.decompose("实现一个登录模块")
        assert isinstance(tree, TaskTree)
        assert tree.total_tasks >= 1

    def test_list_agents(self, uo):
        """list_agents() 应返回已注册 Agent。"""
        agents = uo.list_agents()
        assert len(agents) >= 3
        names = {a.name for a in agents}
        assert "architect" in names or any("architect" in n for n in names)

    def test_get_system_status(self, uo):
        """get_system_status() 应返回 SystemStatus。"""
        status = uo.get_system_status()
        assert isinstance(status, SystemStatus)
        assert status.engine == "new"
        assert len(status.agents) >= 3

    def test_context_manager(self, uo):
        """UnifiedOrchestrator 支持 with 语句。"""
        with UnifiedOrchestrator() as orch:
            result = orch.run("测试")
            assert result.success is True


# ====================================================================== #
#  边界场景
# ====================================================================== #


class TestEdgeCases:
    """边界场景测试。"""

    @pytest.fixture
    def uo(self):
        return UnifiedOrchestrator()

    def test_empty_request(self, uo):
        """空字符串请求不应崩溃。"""
        result = uo.run("")
        assert isinstance(result, UnifiedResult)

    def test_very_long_request(self, uo):
        """超长请求不应崩溃。"""
        long_req = "实现功能 " * 100
        result = uo.run(long_req)
        assert isinstance(result, UnifiedResult)

    def test_stream_empty_request(self, uo):
        """空请求流式执行。"""
        events = list(uo.stream_run(""))
        assert len(events) >= 2

    def test_multiple_runs(self, uo):
        """多次连续 run 应互不影响。"""
        r1 = uo.run("实现一个加法函数")
        r2 = uo.run("实现一个减法函数")
        assert r1.success is True
        assert r2.success is True

    def test_run_with_extra_handlers(self, uo):
        """通过 options.extra_handlers 注入自定义 handler。"""
        def _handler(ctx):
            return {"custom": True}

        result = uo.run(
            "测试",
            options={"extra_handlers": {"requirement": _handler}},
        )
        assert result.success is True

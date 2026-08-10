"""编排引擎单元测试。

覆盖:
- TaskGraph 拓扑排序（Kahn 算法、环检测、并行分组）
- Orchestrator 任务拆解（标准流水线、关键词触发的额外任务）
- 并行执行计划生成与执行
- 失败恢复策略（RETRY / REASSIGN / SKIP / ABORT）
- 与旧 pipeline 的兼容性（PipelinePhase / PipelineStatus 对齐）
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.graph.orchestrator import (
    AgentTask,
    CyclicDependencyError,
    ExecutionPlan,
    Orchestrator,
    OrchestratorResult,
    OrchestratorState,
    RecoveryStrategy,
    TaskGraph,
    TaskStatus,
)
from app.graph.state.enums import PipelinePhase
from app.graph.state.models import PipelineStatus


# ====================================================================== #
#  TaskGraph —— 拓扑排序
# ====================================================================== #


class TestTaskGraphTopology:
    """TaskGraph 拓扑排序与环检测。"""

    def test_add_single_task(self):
        """单任务图应正确注册。"""
        g = TaskGraph()
        task = AgentTask(task_id="a", description="A")
        g.add_task("a", task)
        assert g.task_count == 1
        assert g.get_task("a") is task

    def test_add_duplicate_raises(self):
        """重复任务 ID 应抛 ValueError。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        with pytest.raises(ValueError, match="已存在"):
            g.add_task("a", AgentTask(task_id="a"))

    def test_add_task_with_missing_dependency(self):
        """依赖不存在的任务应抛 ValueError。"""
        g = TaskGraph()
        with pytest.raises(ValueError, match="不存在"):
            g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))

    def test_linear_chain_execution_order(self):
        """线性链路: a→b→c 拓扑排序应为 [a, b, c]。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", dependencies=["b"]))
        order = g.get_execution_order()
        assert order == ["a", "b", "c"]

    def test_parallel_tasks_execution_order(self):
        """并行任务: a→(b,c)→d 拓扑排序合法。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", dependencies=["a"]))
        g.add_task("d", AgentTask(task_id="d", dependencies=["b", "c"]))
        order = g.get_execution_order()
        assert order[0] == "a"
        assert order[-1] == "d"
        assert set(order[1:3]) == {"b", "c"}

    def test_detect_cycle_raises(self):
        """循环依赖应抛 CyclicDependencyError。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g._adj["b"].add("a")
        g._in_degree["a"] += 1
        with pytest.raises(CyclicDependencyError):
            g.add_task("c", AgentTask(task_id="c", dependencies=["b"]))

    def test_detect_cycle_on_existing_graph(self):
        """在已有图上形成环应抛 CyclicDependencyError。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", dependencies=["b"]))
        g._adj["c"].add("a")
        g._in_degree["a"] += 1
        with pytest.raises(CyclicDependencyError):
            g.add_task("d", AgentTask(task_id="d", dependencies=["c"]))

    def test_parallel_groups(self):
        """并行分组: a→(b,c)→d 应分为 3 组。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", dependencies=["a"]))
        g.add_task("d", AgentTask(task_id="d", dependencies=["b", "c"]))
        groups = g.get_parallel_groups()
        assert len(groups) == 3
        assert groups[0] == ["a"]
        assert set(groups[1]) == {"b", "c"}
        assert groups[2] == ["d"]

    def test_ready_tasks(self):
        """就绪任务查询: 仅返回所有依赖已完成的任务。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", dependencies=["b"]))
        ready = g.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "a"

        ready_after = g.get_ready_tasks(completed={"a"})
        assert len(ready_after) == 1
        assert ready_after[0].task_id == "b"

    def test_ready_tasks_respects_failed_deps(self):
        """依赖失败的任务不应就绪。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        ready = g.get_ready_tasks(completed=set(), failed={"a"})
        assert len(ready) == 0

    def test_critical_path(self):
        """关键路径应为最长依赖链。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", dependencies=["b"]))
        g.add_task("d", AgentTask(task_id="d", dependencies=["a"]))
        path = g.compute_critical_path()
        assert len(path) == 3
        assert path == ["a", "b", "c"]

    def test_remove_task(self):
        """移除任务应清理依赖引用。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        removed = g.remove_task("a")
        assert removed.task_id == "a"
        assert g.task_count == 1
        assert "a" not in g.get_task("b").dependencies

    def test_remove_nonexistent_raises(self):
        """移除不存在的任务应抛 KeyError。"""
        g = TaskGraph()
        with pytest.raises(KeyError):
            g.remove_task("nonexistent")


# ====================================================================== #
#  Orchestrator —— 任务拆解
# ====================================================================== #


class TestOrchestratorDecompose:
    """Orchestrator 任务拆解。"""

    def test_standard_pipeline(self):
        """标准需求应生成 requirement→code_gen→code_review→test_gen 四步。"""
        orch = Orchestrator()
        tasks = orch.decompose_task("实现一个用户登录模块")
        task_ids = [t.task_id for t in tasks]
        assert "requirement" in task_ids
        assert "code_gen" in task_ids
        assert "code_review" in task_ids
        assert "test_gen" in task_ids
        assert len(tasks) == 4

    def test_refactor_keyword_adds_analysis(self):
        """包含'重构'关键词应插入深度分析任务。"""
        orch = Orchestrator()
        tasks = orch.decompose_task("重构现有代码架构")
        task_ids = [t.task_id for t in tasks]
        assert any(tid.startswith("analysis_") for tid in task_ids)
        assert len(tasks) >= 5

    def test_api_keyword_adds_design(self):
        """包含'API'关键词应插入 API 设计任务。"""
        orch = Orchestrator()
        tasks = orch.decompose_task("设计一个 REST API 接口服务")
        task_ids = [t.task_id for t in tasks]
        assert any(tid.startswith("api_design_") for tid in task_ids)
        assert len(tasks) >= 5

    def test_decompose_sets_dependencies(self):
        """任务拆解应正确设置依赖关系。"""
        orch = Orchestrator()
        tasks = orch.decompose_task("实现一个登录模块")
        task_map = {t.task_id: t for t in tasks}
        assert "requirement" in task_map["code_gen"].dependencies
        assert "code_gen" in task_map["code_review"].dependencies
        assert "code_review" in task_map["test_gen"].dependencies

    def test_decompose_sets_metadata_phase(self):
        """每个任务的 metadata 应包含 phase 信息。"""
        orch = Orchestrator()
        tasks = orch.decompose_task("实现一个登录模块")
        for t in tasks:
            assert "phase" in t.metadata


# ====================================================================== #
#  Orchestrator —— Agent 分配
# ====================================================================== #


class TestOrchestratorAgentAssignment:
    """智能体分配测试。"""

    def test_default_assignment(self):
        """默认映射: requirement→requirement_agent 等。"""
        orch = Orchestrator()
        task = AgentTask(task_id="t1", agent_type="code_gen")
        agent = orch.assign_agent(task)
        assert agent == "code_gen_agent"
        assert task.assigned_agent == "code_gen_agent"

    def test_custom_assignment(self):
        """自定义 agent 映射应生效。"""
        orch = Orchestrator()
        task = AgentTask(task_id="t1", agent_type="code_gen")
        agents = {"code_gen": "custom_coder"}
        agent = orch.assign_agent(task, available_agents=agents)
        assert agent == "custom_coder"

    def test_unknown_type_fallback(self):
        """未知 agent_type 应回退到 default_agent。"""
        orch = Orchestrator()
        task = AgentTask(task_id="t1", agent_type="unknown_type")
        agent = orch.assign_agent(task)
        assert agent == "default_agent"


# ====================================================================== #
#  Orchestrator —— 执行计划生成
# ====================================================================== #


class TestOrchestratorSchedule:
    """执行计划生成。"""

    def test_schedule_produces_valid_plan(self):
        """从 TaskGraph 生成的计划应包含所有任务。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a", agent_type="requirement"))
        g.add_task("b", AgentTask(task_id="b", agent_type="code_gen", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", agent_type="review", dependencies=["b"]))
        g.add_task("d", AgentTask(task_id="d", agent_type="test_gen", dependencies=["c"]))

        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        assert plan.total_tasks == 4
        assert len(plan.execution_order) == 4
        assert plan.max_parallelism == 1
        assert len(plan.critical_path) == 4

    def test_fallback_strategies_set(self):
        """review/test_gen/code_gen 任务应使用 RETRY 策略。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a", agent_type="requirement"))
        g.add_task("b", AgentTask(task_id="b", agent_type="code_gen", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", agent_type="review", dependencies=["b"]))

        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        assert plan.fallback_strategies["a"] == RecoveryStrategy.SKIP
        assert plan.fallback_strategies["b"] == RecoveryStrategy.RETRY
        assert plan.fallback_strategies["c"] == RecoveryStrategy.RETRY

    def test_estimated_duration(self):
        """估算耗时应与关键路径长度正相关。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a"))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"]))
        g.add_task("c", AgentTask(task_id="c", dependencies=["b"]))
        g.add_task("d", AgentTask(task_id="d"))

        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        assert plan.estimated_duration > 0
        assert plan.total_tasks == 4


# ====================================================================== #
#  Orchestrator —— 计划执行
# ====================================================================== #


class TestOrchestratorExecute:
    """计划执行与并行执行。"""

    def test_execute_with_handlers_success(self):
        """带 handler 的任务应成功执行。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a", handler=lambda ctx: {"ok": True}))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"], handler=lambda ctx: {"ok": True}))

        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        result = orch.execute_plan(plan, task_graph=g)
        assert result.success is True
        assert result.completed_count == 2
        assert result.failed_count == 0

    def test_execute_without_handler_uses_default(self):
        """无 handler 的任务应走默认执行路径。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a", agent_type="code_gen"))
        g.add_task("b", AgentTask(task_id="b", agent_type="review", dependencies=["a"]))

        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        result = orch.execute_plan(plan, task_graph=g)
        assert result.success is True
        assert result.completed_count == 2

    def test_execute_parallel_tasks(self):
        """并行任务应在同一组内执行。"""
        g = TaskGraph()
        g.add_task("a", AgentTask(task_id="a", handler=lambda ctx: {"ok": "a"}))
        g.add_task("b", AgentTask(task_id="b", handler=lambda ctx: {"ok": "b"}))
        g.add_task("c", AgentTask(task_id="c", dependencies=["a", "b"], handler=lambda ctx: {"ok": "c"}))

        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        result = orch.execute_plan(plan, task_graph=g)
        assert result.success is True
        assert result.completed_count == 3

    def test_execute_failed_task_blocks_dependents(self):
        """任务失败后，其依赖任务应被阻塞。"""
        g = TaskGraph()

        def _fail_handler(ctx):
            raise RuntimeError("task failed")

        g.add_task("a", AgentTask(task_id="a", agent_type="code_gen", handler=_fail_handler, max_retries=0))
        g.add_task("b", AgentTask(task_id="b", dependencies=["a"], handler=lambda ctx: {"ok": True}))

        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        result = orch.execute_plan(plan, task_graph=g)
        assert result.success is False
        assert result.failed_count >= 1

    def test_execute_empty_plan(self):
        """空计划应返回 success=False。"""
        orch = Orchestrator()
        plan = ExecutionPlan()
        result = orch.execute_plan(plan)
        assert result.success is False
        assert result.total_duration >= 0

    def test_execution_result_summary(self):
        """OrchestratorResult.summary 应返回完整摘要。"""
        result = OrchestratorResult(success=True, completed_count=3, failed_count=1)
        summary = result.summary
        assert summary["success"] is True
        assert summary["completed"] == 3
        assert summary["failed"] == 1
        assert summary["total_tasks"] == 4


# ====================================================================== #
#  失败恢复策略
# ====================================================================== #


class TestRecoveryStrategy:
    """异常恢复策略选择。"""

    def test_code_gen_failure_uses_retry(self):
        """code_gen 失败应选 RETRY。"""
        orch = Orchestrator()
        task = AgentTask(task_id="cg", agent_type="code_gen")
        state = OrchestratorState()
        strategy = orch.handle_failure(task, "timeout", state)
        assert strategy == RecoveryStrategy.RETRY

    def test_review_failure_uses_retry(self):
        """review 失败应选 RETRY。"""
        orch = Orchestrator()
        task = AgentTask(task_id="rv", agent_type="review")
        state = OrchestratorState()
        strategy = orch.handle_failure(task, "error", state)
        assert strategy == RecoveryStrategy.RETRY

    def test_requirement_failure_uses_abort(self):
        """requirement 失败应选 ABORT。"""
        orch = Orchestrator()
        task = AgentTask(task_id="req", agent_type="requirement")
        state = OrchestratorState()
        strategy = orch.handle_failure(task, "error", state)
        assert strategy == RecoveryStrategy.ABORT

    def test_unknown_type_uses_skip(self):
        """未知类型任务失败应选 SKIP。"""
        orch = Orchestrator()
        task = AgentTask(task_id="unk", agent_type="custom_type")
        state = OrchestratorState()
        strategy = orch.handle_failure(task, "error", state)
        assert strategy == RecoveryStrategy.SKIP

    def test_apply_skip_marks_skipped(self):
        """SKIP 策略应将任务标记为 SKIPPED。"""
        orch = Orchestrator()
        task = AgentTask(task_id="t1", agent_type="custom")
        task.error = "skip me"
        state = OrchestratorState()
        orch._apply_recovery(task, RecoveryStrategy.SKIP, state, TaskGraph(), ExecutionPlan())
        assert task.status == TaskStatus.SKIPPED

    def test_apply_abort_marks_failed(self):
        """ABORT 策略应将任务标记为 FAILED。"""
        orch = Orchestrator()
        task = AgentTask(task_id="t1", agent_type="custom")
        task.error = "abort me"
        state = OrchestratorState()
        orch._apply_recovery(task, RecoveryStrategy.ABORT, state, TaskGraph(), ExecutionPlan())
        assert task.status == TaskStatus.FAILED


# ====================================================================== #
#  便捷工厂方法 run()
# ====================================================================== #


class TestOrchestratorRun:
    """一站式 run() 方法。"""

    def test_run_with_default_handlers(self):
        """run() 应完成从拆解到执行的全流程。"""
        orch = Orchestrator()
        result = orch.run("实现一个简单的计算器")
        assert isinstance(result, OrchestratorResult)
        assert result.success is True
        assert result.completed_count >= 4

    def test_run_with_custom_handlers(self):
        """run() 支持通过 extra_handlers 注入自定义 handler。"""
        orch = Orchestrator()

        def _custom_handler(ctx):
            return {"custom": True, "context_keys": list(ctx.keys())}

        result = orch.run(
            "实现一个登录模块",
            extra_handlers={"requirement": _custom_handler},
        )
        assert result.success is True

    def test_create_plan_from_request(self):
        """create_plan_from_request 应生成有效的执行计划。"""
        orch = Orchestrator()
        plan = orch.create_plan_from_request("实现一个登录模块")
        assert isinstance(plan, ExecutionPlan)
        assert plan.total_tasks >= 4
        assert len(plan.execution_order) >= 1


# ====================================================================== #
#  与旧 pipeline 兼容性
# ====================================================================== #


class TestPipelineCompatibility:
    """与旧 pipeline 状态/阶段的兼容性。"""

    def test_orchestrator_state_has_pipeline_status(self):
        """OrchestratorState 应包含 PipelineStatus。"""
        state = OrchestratorState()
        assert isinstance(state.pipeline_status, PipelineStatus)
        assert state.current_phase == PipelinePhase.PENDING

    def test_mark_completed_updates_phase(self):
        """标记完成后 phase 应正确推进。"""
        state = OrchestratorState()
        task = AgentTask(task_id="t1")
        state.mark_completed(task)
        assert task.status == TaskStatus.COMPLETED
        assert "t1" in state.completed_tasks

    def test_mark_failed_updates_phase(self):
        """标记失败后应记录错误。"""
        state = OrchestratorState()
        task = AgentTask(task_id="t1")
        state.mark_failed(task, "something wrong")
        assert task.status == TaskStatus.FAILED
        assert task.error == "something wrong"
        assert "t1" in state.failed_tasks

    def test_get_task_from_state(self):
        """从 state 中查找已完成/已失败的任务。"""
        state = OrchestratorState()
        task = AgentTask(task_id="t1")
        state.mark_completed(task)
        found = state.get_task("t1")
        assert found is task
        assert state.get_task("nonexistent") is None

    def test_final_state_sets_done_phase(self):
        """成功执行后 phase 应为 DONE。"""
        orch = Orchestrator()
        result = orch.run("实现一个简单的加法函数")
        assert result.state.current_phase == PipelinePhase.DONE

    def test_failed_state_sets_failed_phase(self):
        """全部失败后 phase 应为 FAILED。"""
        g = TaskGraph()

        def _fail(ctx):
            raise RuntimeError("fail")

        g.add_task("a", AgentTask(task_id="a", agent_type="code_gen", handler=_fail, max_retries=0))
        orch = Orchestrator()
        plan = orch.schedule_tasks(g)
        state = OrchestratorState()
        result = orch.execute_plan(plan, state, task_graph=g)
        assert result.state.current_phase == PipelinePhase.FAILED


# ====================================================================== #
#  数据类基础测试
# ====================================================================== #


class TestDataClasses:
    """数据类属性测试。"""

    def test_agent_task_duration(self):
        """AgentTask.duration 应计算正确。"""
        import time
        task = AgentTask(task_id="t1")
        assert task.duration == 0.0
        task.started_at = 100.0
        task.completed_at = 150.0
        assert task.duration == 50.0

    def test_agent_task_status_transitions(self):
        """AgentTask 状态流转应正确。"""
        task = AgentTask(task_id="t1")
        assert task.status == TaskStatus.PENDING
        task.status = TaskStatus.RUNNING
        assert task.status == TaskStatus.RUNNING

    def test_execution_plan_properties(self):
        """ExecutionPlan 的派生属性应正确。"""
        plan = ExecutionPlan(
            execution_order=[["a", "b"], ["c"], ["d"]],
        )
        assert plan.total_tasks == 4
        assert plan.max_parallelism == 2

    def test_orchestrator_result_summary(self):
        """空结果的 summary 应合理。"""
        result = OrchestratorResult()
        assert result.summary["success"] is False
        assert result.summary["total_tasks"] == 0


# ====================================================================== #
#  Orchestrator 上下文管理
# ====================================================================== #


class TestOrchestratorContext:
    """上下文管理器协议。"""

    def test_context_manager(self):
        """Orchestrator 支持 with 语句。"""
        with Orchestrator() as orch:
            result = orch.run("实现一个加法函数")
            assert result.success is True

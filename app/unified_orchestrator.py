"""新一代多智能体系统门面（Facade）。

整合 Orchestrator、TaskDecomposer、AgentExecutor、MemorySystem，
提供统一的一站式执行入口，支持新旧架构并存。

核心能力：
- run():       一站式同步执行，返回 UnifiedResult
- stream_run(): 流式执行，逐步产出 StreamEvent 供 SSE 推送
- legacy_mode=True 时走原有 pipeline.py 流程
- 默认走新编排引擎：需求分析 → 任务拆解 → 策略选择 → 任务调度 → 执行编排 → 结果汇总

与现有 app.graph.pipeline 完全兼容，可作为其升级替代方案。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from app.agents.agent_protocol import AgentProfile
from app.agent_executor import (
    AgentCollaborationManager,
    AgentPool,
    ArchitectExecutor,
    CoderExecutor,
    OrchestratorExecutor,
    ReviewerExecutor,
    TesterExecutor,
)
from app.core.memory_system import MemoryFacade, memory_system
from app.graph.orchestrator import (
    ExecutionPlan,
    Orchestrator,
    OrchestratorResult,
    OrchestratorState,
)
from app.graph.state.enums import PipelinePhase
from app.graph.state.models import CodePackage, PipelineStatus, ReviewReport, TestReport
from app.graph.task_decomposer import (
    DecisionEngine,
    RequirementAnalysis,
    Strategy,
    TaskDecomposer,
    TaskTree,
)

logger = logging.getLogger(__name__)


# ====================================================================== #
#  数据类
# ====================================================================== #


@dataclass
class UnifiedResult:
    """统一执行结果数据类。

    Attributes:
        success:       是否执行成功
        artifacts:     产出物字典（代码文件、审查报告、测试报告等）
        execution_plan: 使用的执行计划（新引擎），legacy 模式为 None
        agent_outputs: 各智能体输出映射 {agent_type: output}
        memory_summary: 记忆系统摘要
        execution_time: 执行耗时（秒）
        token_usage:   Token 用量统计
    """

    success: bool = False
    artifacts: Dict[str, Any] = field(default_factory=dict)
    execution_plan: Optional[ExecutionPlan] = None
    agent_outputs: Dict[str, Any] = field(default_factory=dict)
    memory_summary: Dict[str, Any] = field(default_factory=dict)
    execution_time: float = 0.0
    token_usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "success": self.success,
            "artifacts": self.artifacts,
            "execution_plan": (
                {
                    "total_tasks": self.execution_plan.total_tasks,
                    "estimated_duration": self.execution_plan.estimated_duration,
                    "max_parallelism": self.execution_plan.max_parallelism,
                    "critical_path": self.execution_plan.critical_path,
                }
                if self.execution_plan
                else None
            ),
            "agent_outputs": self.agent_outputs,
            "memory_summary": self.memory_summary,
            "execution_time": round(self.execution_time, 3),
            "token_usage": self.token_usage,
        }


@dataclass
class StreamEvent:
    """流式事件数据类，供 SSE 推送使用。

    Attributes:
        event_type: 事件类型（requirement_started / task_dispatched / ...）
        data:       事件负载数据
        timestamp:  事件时间戳（ISO 格式）
        progress:   整体进度百分比（0-100）
    """

    event_type: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    progress: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "progress": self.progress,
        }


@dataclass
class SystemStatus:
    """系统状态快照数据类。

    Attributes:
        engine:       当前使用的引擎模式（legacy / new）
        agents:       已注册的智能体画像列表
        memory:       记忆系统统计
        pipeline_phase: 当前流水线阶段
        uptime_seconds: 运行时长
    """

    engine: str = "new"
    agents: List[Dict[str, Any]] = field(default_factory=list)
    memory: Dict[str, Any] = field(default_factory=dict)
    pipeline_phase: str = "idle"
    uptime_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "engine": self.engine,
            "agents": self.agents,
            "memory": self.memory,
            "pipeline_phase": self.pipeline_phase,
            "uptime_seconds": round(self.uptime_seconds, 2),
        }


# ====================================================================== #
#  UnifiedOrchestrator —— 门面类
# ====================================================================== #


class UnifiedOrchestrator:
    """新一代多智能体系统统一入口（Facade）。

    整合了 Orchestrator、TaskDecomposer、AgentExecutor、MemorySystem
    四大子系统，提供一站式执行体验。

    支持新旧架构共存：
    - legacy_mode=True 时走原有 app.graph.pipeline 流程
    - 默认走新编排引擎（需求分析 → 任务拆解 → 策略选择 →
      任务调度 → 执行编排 → 结果汇总）

    Usage::

        orchestrator = UnifiedOrchestrator()

        result = orchestrator.run("实现一个用户登录模块")
        for event in orchestrator.stream_run("优化数据库查询性能"):
            print(event.progress, event.event_type)

    Attributes:
        _orchestrator:      Orchestrator 编排引擎实例
        _decomposer:        TaskDecomposer 任务拆解器实例
        _decision_engine:   DecisionEngine 决策引擎实例
        _agent_pool:        AgentPool 智能体池实例
        _collaboration_mgr: AgentCollaborationManager 协作管理器实例
        _memory_facade:    MemoryFacade 记忆系统门面实例
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(f"{__name__}.UnifiedOrchestrator")
        self._orchestrator = Orchestrator()
        self._decomposer = TaskDecomposer()
        self._decision_engine = DecisionEngine()
        self._agent_pool = AgentPool()
        self._collaboration_mgr = AgentCollaborationManager(agent_pool=self._agent_pool)
        self._memory_facade: MemoryFacade = memory_system
        self._start_time = time.monotonic()
        self._register_default_agents()
        self._last_result: Optional[UnifiedResult] = None
        self._logger.info("UnifiedOrchestrator 已初始化")

    # ------------------------------------------------------------------ #
    #  初始化辅助
    # ------------------------------------------------------------------ #

    def _register_default_agents(self) -> None:
        """注册默认的智能体执行器到池中。"""
        self._agent_pool.register(ArchitectExecutor())
        self._agent_pool.register(CoderExecutor())
        self._agent_pool.register(ReviewerExecutor())
        self._agent_pool.register(TesterExecutor())
        self._agent_pool.register(OrchestratorExecutor())
        self._logger.info(
            "默认智能体已注册: %s",
            [e.agent_profile.name for e in self._agent_pool._executors.values()],
        )

    # ------------------------------------------------------------------ #
    #  核心执行入口
    # ------------------------------------------------------------------ #

    def run(
        self,
        user_request: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> UnifiedResult:
        """一站式同步执行入口。

        内部流程：
        需求分析 → 任务拆解 → 策略选择 → 任务调度 → 执行编排 → 结果汇总

        Args:
            user_request: 用户原始需求描述
            options:      可选配置项，支持：
                - max_parallel:        最大并行度（默认 4）
                - enable_memory:       是否启用记忆系统（默认 True）
                - enable_collaboration: 是否启用智能体协作（默认 True）
                - preferred_strategy:  优先策略（sequential/parallel/pipeline/iterative/hybrid）
                - legacy_mode:         是否走旧 pipeline 流程（默认 False）
                - extra_args:          透传给 pipeline 的额外参数
                - model:               指定 LLM 模型
                - skill_id:            指定技能 ID

        Returns:
            UnifiedResult 完整执行结果
        """
        options = options or {}
        legacy_mode = options.get("legacy_mode", False)

        self._logger.info(
            "开始执行: request='%s', legacy=%s, options=%s",
            user_request[:80], legacy_mode,
            {k: v for k, v in options.items() if k != "extra_args"},
        )

        if legacy_mode:
            return self._run_legacy(user_request, options)

        return self._run_new_engine(user_request, options)

    def stream_run(
        self,
        user_request: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> Iterator[StreamEvent]:
        """流式执行入口，逐步产出 StreamEvent 供 SSE 推送。

        Args:
            user_request: 用户原始需求描述
            options:      与 run() 相同的配置项

        Yields:
            StreamEvent 逐步产出的事件
        """
        options = options or {}
        legacy_mode = options.get("legacy_mode", False)

        yield StreamEvent(
            event_type="execution_started",
            data={"user_request": user_request[:200], "legacy_mode": legacy_mode},
            progress=0,
        )

        if legacy_mode:
            yield from self._stream_legacy(user_request, options)
        else:
            yield from self._stream_new_engine(user_request, options)

        yield StreamEvent(
            event_type="execution_completed",
            data={"success": self._last_result.success if self._last_result else False},
            progress=100,
        )

    # ------------------------------------------------------------------ #
    #  Legacy 模式 —— 复用 app.graph.pipeline
    # ------------------------------------------------------------------ #

    def _run_legacy(
        self, user_request: str, options: Dict[str, Any]
    ) -> UnifiedResult:
        """走原有 pipeline.py 流程。"""
        from app.graph.pipeline import run_pipeline

        task_id = f"legacy_{int(time.time())}"
        pipeline_start = time.monotonic()

        try:
            state = run_pipeline(
                task_id=task_id,
                user_request=user_request,
                extra_args=options.get("extra_args"),
                skill_id=options.get("skill_id", ""),
                model=options.get("model"),
            )

            execution_time = time.monotonic() - pipeline_start
            status = state.get("status") or PipelineStatus()
            is_success = (
                status.phase == PipelinePhase.DONE
                and not status.error_message
            )

            artifacts: Dict[str, Any] = {}
            code_pkg = state.get("code_package")
            if code_pkg and isinstance(code_pkg, CodePackage):
                artifacts["code"] = [
                    {"path": f.path, "content": f.content, "language": f.language}
                    for f in code_pkg.files
                ]
            review = state.get("review_report")
            if review and isinstance(review, ReviewReport):
                artifacts["review"] = {
                    "verdict": review.verdict.value,
                    "summary": review.summary,
                    "score": review.score,
                    "issues_count": len(review.issues),
                }
            test = state.get("test_report")
            if test and isinstance(test, TestReport):
                artifacts["test"] = {
                    "status": test.status.value,
                    "summary": test.summary,
                    "total": test.total,
                    "passed": test.passed,
                    "failed": test.failed,
                }

            token_usage = {
                "prompt_tokens": status.prompt_tokens,
                "completion_tokens": status.completion_tokens,
                "total_tokens": status.total_tokens,
            }

            result = UnifiedResult(
                success=is_success,
                artifacts=artifacts,
                execution_plan=None,
                agent_outputs={},
                memory_summary=self._memory_facade.get_system_stats(),
                execution_time=execution_time,
                token_usage=token_usage,
            )
            self._last_result = result
            self._logger.info(
                "Legacy 执行完成: success=%s, time=%.2fs", is_success, execution_time
            )
            return result

        except Exception as e:
            execution_time = time.monotonic() - pipeline_start
            self._logger.exception("Legacy 执行失败: %s", e)
            result = UnifiedResult(
                success=False,
                artifacts={},
                execution_plan=None,
                agent_outputs={},
                memory_summary=self._memory_facade.get_system_stats(),
                execution_time=execution_time,
                token_usage={},
            )
            self._last_result = result
            return result

    def _stream_legacy(
        self, user_request: str, options: Dict[str, Any]
    ) -> Iterator[StreamEvent]:
        """Legacy 流式执行，复用 stream_pipeline。"""
        from app.graph.pipeline import stream_pipeline

        task_id = f"legacy_stream_{int(time.time())}"

        events, state = stream_pipeline(
            task_id=task_id,
            user_request=user_request,
            extra_args=options.get("extra_args"),
            skill_id=options.get("skill_id", ""),
            model=options.get("model"),
        )

        for evt in events:
            yield StreamEvent(
                event_type=f"pipeline_{evt.get('phase', 'unknown')}",
                data={
                    "phase": evt.get("phase", ""),
                    "message": evt.get("message", ""),
                    "task_id": evt.get("task_id", ""),
                    "error_type": evt.get("error_type", ""),
                },
                progress=evt.get("progress", 0),
            )

        status = state.get("status") or PipelineStatus()
        self._last_result = UnifiedResult(
            success=status.phase == PipelinePhase.DONE and not status.error_message,
            artifacts={},
            execution_plan=None,
            agent_outputs={},
            memory_summary=self._memory_facade.get_system_stats(),
            execution_time=0.0,
            token_usage={},
        )

    # ------------------------------------------------------------------ #
    #  New Engine 模式 —— 编排引擎
    # ------------------------------------------------------------------ #

    def _run_new_engine(
        self, user_request: str, options: Dict[str, Any]
    ) -> UnifiedResult:
        """新引擎完整执行流程。"""
        max_parallel = options.get("max_parallel", 4)
        enable_memory = options.get("enable_memory", True)
        enable_collaboration = options.get("enable_collaboration", True)
        preferred_strategy = options.get("preferred_strategy")
        extra_handlers = options.get("extra_handlers")

        overall_start = time.monotonic()
        token_usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        # Step 1: 需求分析
        self._logger.info("Step 1: 需求分析")
        analysis = self._decomposer.analyze_request(user_request)

        # Step 2: 任务拆解
        self._logger.info("Step 2: 任务拆解")
        task_tree = self._decomposer.decompose(analysis)

        # Step 3: 策略选择
        self._logger.info("Step 3: 策略选择")
        if preferred_strategy:
            try:
                strategy = Strategy(preferred_strategy)
            except ValueError:
                self._logger.warning(
                    "无效的策略 '%s'，回退到自动选择", preferred_strategy
                )
                strategy = self._decision_engine.select_strategy(
                    {"complexity": analysis.complexity.level}
                )
        else:
            strategy = self._decision_engine.select_strategy(
                {"complexity": analysis.complexity.level}
            )

        # Step 4: 构造执行计划
        self._logger.info("Step 4: 构造执行计划")
        plan = self._orchestrator.create_plan_from_request(
            user_request, extra_handlers=extra_handlers
        )

        # Step 5: 执行编排
        self._logger.info("Step 5: 执行编排 (strategy=%s)", strategy.value)
        orch_state = OrchestratorState(
            context={
                "user_request": user_request,
                "task_id": f"orch_{int(time.time())}",
                "analysis": analysis,
                "task_tree": task_tree,
                "strategy": strategy.value,
            },
        )

        orch_result: OrchestratorResult = self._orchestrator.execute_plan(
            plan, orch_state, task_graph=self._orchestrator._current_graph
        )

        # Step 6: 结果汇总
        self._logger.info("Step 6: 结果汇总")
        artifacts = self._build_artifacts(orch_result)
        agent_outputs = self._build_agent_outputs(orch_result)

        if enable_memory:
            try:
                self._memory_facade.write(
                    "episodic",
                    "milestone",
                    {
                        "user_request": user_request[:200],
                        "strategy": strategy.value,
                        "success": orch_result.success,
                        "duration": orch_result.total_duration,
                    },
                )
                memory_summary = self._memory_facade.get_system_stats()
            except Exception as e:
                self._logger.warning("记忆系统写入失败: %s", e)
                memory_summary = {}
        else:
            memory_summary = {}

        execution_time = time.monotonic() - overall_start

        result = UnifiedResult(
            success=orch_result.success,
            artifacts=artifacts,
            execution_plan=plan,
            agent_outputs=agent_outputs,
            memory_summary=memory_summary,
            execution_time=execution_time,
            token_usage=token_usage,
        )
        self._last_result = result
        self._logger.info(
            "新引擎执行完成: success=%s, time=%.2fs, tasks=%d",
            orch_result.success, execution_time, plan.total_tasks,
        )
        return result

    def _stream_new_engine(
        self, user_request: str, options: Dict[str, Any]
    ) -> Iterator[StreamEvent]:
        """新引擎流式执行，逐步产出事件。"""
        max_parallel = options.get("max_parallel", 4)
        enable_memory = options.get("enable_memory", True)
        preferred_strategy = options.get("preferred_strategy")
        extra_handlers = options.get("extra_handlers")

        overall_start = time.monotonic()
        agent_outputs: Dict[str, Any] = {}
        artifacts: Dict[str, Any] = {}

        # Step 1: 需求分析
        yield StreamEvent(
            event_type="requirement_analysis_started",
            data={"user_request": user_request[:200]},
            progress=5,
        )
        analysis = self._decomposer.analyze_request(user_request)
        yield StreamEvent(
            event_type="requirement_analysis_completed",
            data={
                "goal": analysis.goal[:200],
                "complexity": analysis.complexity.level,
                "suggested_roles": [r.value for r in analysis.suggested_roles],
                "estimated_steps": analysis.estimated_steps,
            },
            progress=15,
        )

        # Step 2: 任务拆解
        yield StreamEvent(
            event_type="task_decomposition_started",
            data={},
            progress=20,
        )
        task_tree = self._decomposer.decompose(analysis)
        leaf_count = len(task_tree.get_leaf_tasks())
        yield StreamEvent(
            event_type="task_decomposition_completed",
            data={
                "total_tasks": task_tree.total_tasks,
                "leaf_tasks": leaf_count,
                "depth": task_tree.depth,
            },
            progress=30,
        )

        # Step 3: 策略选择
        yield StreamEvent(
            event_type="strategy_selection_started",
            data={},
            progress=35,
        )
        if preferred_strategy:
            try:
                strategy = Strategy(preferred_strategy)
            except ValueError:
                strategy = self._decision_engine.select_strategy(
                    {"complexity": analysis.complexity.level}
                )
        else:
            strategy = self._decision_engine.select_strategy(
                {"complexity": analysis.complexity.level}
            )
        yield StreamEvent(
            event_type="strategy_selected",
            data={
                "strategy": strategy.value,
                "reasoning": f"基于复杂度 {analysis.complexity.level} 选择 {strategy.description}",
            },
            progress=40,
        )

        # Step 4: 构造执行计划
        yield StreamEvent(
            event_type="plan_creation_started",
            data={},
            progress=45,
        )
        plan = self._orchestrator.create_plan_from_request(
            user_request, extra_handlers=extra_handlers
        )
        yield StreamEvent(
            event_type="plan_creation_completed",
            data={
                "total_tasks": plan.total_tasks,
                "parallel_groups": len(plan.execution_order),
                "critical_path": plan.critical_path,
                "estimated_duration": plan.estimated_duration,
            },
            progress=50,
        )

        # Step 5: 执行编排（逐任务产出事件）
        yield StreamEvent(
            event_type="execution_started",
            data={"strategy": strategy.value, "total_tasks": plan.total_tasks},
            progress=55,
        )

        orch_state = OrchestratorState(
            context={
                "user_request": user_request,
                "task_id": f"orch_{int(time.time())}",
                "analysis": analysis,
                "task_tree": task_tree,
                "strategy": strategy.value,
            },
        )

        completed = 0
        total = plan.total_tasks

        for group_idx, group in enumerate(plan.execution_order):
            for task_id in group:
                try:
                    task = self._orchestrator._current_graph.get_task(task_id)
                except (KeyError, AttributeError):
                    task = None

                yield StreamEvent(
                    event_type="task_started",
                    data={
                        "task_id": task_id,
                        "group_index": group_idx,
                        "group_size": len(group),
                        "agent_type": task.agent_type if task else "unknown",
                    },
                    progress=55 + int((completed / max(total, 1)) * 40),
                )

                completed += 1

        # 实际执行
        orch_result = self._orchestrator.execute_plan(plan, orch_state, task_graph=self._orchestrator._current_graph)

        # Step 6: 结果汇总
        artifacts = self._build_artifacts(orch_result)
        agent_outputs = self._build_agent_outputs(orch_result)

        yield StreamEvent(
            event_type="execution_completed",
            data={
                "success": orch_result.success,
                "completed_count": orch_result.completed_count,
                "failed_count": orch_result.failed_count,
                "total_duration": round(orch_result.total_duration, 2),
            },
            progress=95,
        )

        memory_summary: Dict[str, Any] = {}
        if enable_memory:
            try:
                self._memory_facade.write(
                    "episodic",
                    "milestone",
                    {
                        "user_request": user_request[:200],
                        "strategy": strategy.value,
                        "success": orch_result.success,
                    },
                )
                memory_summary = self._memory_facade.get_system_stats()
            except Exception:
                pass

        execution_time = time.monotonic() - overall_start
        token_usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        self._last_result = UnifiedResult(
            success=orch_result.success,
            artifacts=artifacts,
            execution_plan=plan,
            agent_outputs=agent_outputs,
            memory_summary=memory_summary,
            execution_time=execution_time,
            token_usage=token_usage,
        )

    # ------------------------------------------------------------------ #
    #  便捷方法
    # ------------------------------------------------------------------ #

    def analyze(self, user_request: str) -> RequirementAnalysis:
        """对用户需求进行深度分析。

        Args:
            user_request: 用户原始需求描述

        Returns:
            RequirementAnalysis 结构化需求分析结果
        """
        return self._decomposer.analyze_request(user_request)

    def decompose(self, user_request: str) -> TaskTree:
        """将用户需求拆解为层级化任务树。

        Args:
            user_request: 用户原始需求描述

        Returns:
            TaskTree 层级任务树
        """
        analysis = self._decomposer.analyze_request(user_request)
        return self._decomposer.decompose(analysis)

    def list_agents(self) -> List[AgentProfile]:
        """列出当前系统中所有已注册的智能体画像。

        Returns:
            AgentProfile 列表
        """
        return [
            e.agent_profile
            for e in self._agent_pool._executors.values()
        ]

    def get_system_status(self) -> SystemStatus:
        """获取系统当前状态快照。

        Returns:
            SystemStatus 状态快照
        """
        agent_info = self._agent_pool.list_available()
        memory_info = self._memory_facade.get_system_stats()
        uptime = time.monotonic() - self._start_time

        last_phase = "idle"
        if self._last_result:
            last_phase = "completed" if self._last_result.success else "failed"

        return SystemStatus(
            engine="new",
            agents=agent_info,
            memory=memory_info,
            pipeline_phase=last_phase,
            uptime_seconds=uptime,
        )

    # ------------------------------------------------------------------ #
    #  内部辅助
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_artifacts(orch_result: OrchestratorResult) -> Dict[str, Any]:
        """从编排结果中提取结构化产物。"""
        artifacts: Dict[str, Any] = {}

        for task_id, result in orch_result.results.items():
            if result:
                agent_type = result.get("agent_type", task_id)
                if agent_type not in artifacts:
                    artifacts[agent_type] = []
                artifacts[agent_type].append({
                    "task_id": task_id,
                    "status": "completed",
                    "output": result.get("output", "")[:500]
                    if isinstance(result.get("output"), str)
                    else str(result)[:500],
                })

        if orch_result.errors:
            artifacts["_errors"] = [
                {"task_id": tid, "error": err}
                for tid, err in orch_result.errors.items()
            ]

        return artifacts

    @staticmethod
    def _build_agent_outputs(
        orch_result: OrchestratorResult,
    ) -> Dict[str, Any]:
        """构建各智能体的输出映射。"""
        outputs: Dict[str, Any] = {}
        for task_id, result in orch_result.results.items():
            agent_name = result.get("agent", task_id) if result else task_id
            outputs[agent_name] = result or {}
        return outputs

    # ------------------------------------------------------------------ #
    #  上下文管理
    # ------------------------------------------------------------------ #

    def __enter__(self) -> UnifiedOrchestrator:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# ====================================================================== #
#  全局单例
# ====================================================================== #

unified_orchestrator = UnifiedOrchestrator()

__all__ = [
    "UnifiedOrchestrator",
    "UnifiedResult",
    "StreamEvent",
    "SystemStatus",
    "unified_orchestrator",
]
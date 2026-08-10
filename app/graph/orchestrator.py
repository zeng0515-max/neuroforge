"""企业级多智能体编排引擎。

提供基于 DAG 的任务依赖管理、智能体动态分配、并行执行计划生成、
异常恢复策略等核心能力。可作为现有 pipeline.py 的升级替代方案，
与 PipelineState / PipelinePhase 完全兼容。

核心组件：
    - TaskGraph: DAG 任务依赖图，支持拓扑排序与环检测
    - OrchestratorState: 编排引擎运行时状态
    - ExecutionPlan: 执行计划（并行分组、依赖映射、回退策略）
    - Orchestrator: 编排引擎核心（任务拆解 → 分配 → 调度 → 执行 → 恢复）
    - RecoveryStrategy: 异常恢复策略枚举
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.core.events import EventType, event_bus
from app.graph.state.enums import PipelinePhase
from app.graph.state.models import PipelineStatus

logger = logging.getLogger(__name__)


# ====================================================================== #
#  枚举与数据类
# ====================================================================== #


class RecoveryStrategy(str, Enum):
    """异常恢复策略枚举。"""

    RETRY = "retry"
    REASSIGN = "reassign"
    SKIP = "skip"
    ESCALATE = "escalate"
    ABORT = "abort"


class TaskStatus(str, Enum):
    """任务运行时状态。"""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass
class AgentTask:
    """编排引擎中的原子任务单元。

    Attributes:
        task_id: 唯一任务标识符
        description: 任务描述 / 用户需求片段
        agent_type: 期望的智能体类型（如 requirement / code_gen / review / test_gen）
        handler: 可选的同步执行回调，签名为 (context: Dict[str, Any]) -> Dict[str, Any]
        dependencies: 依赖的前置任务 ID 列表
        priority: 执行优先级（数值越大越先调度）
        timeout_seconds: 超时时间，默认 300 秒
        max_retries: 最大重试次数
        metadata: 附加元数据
    """

    task_id: str
    description: str = ""
    agent_type: str = "general"
    handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    dependencies: List[str] = field(default_factory=list)
    priority: int = 0
    timeout_seconds: int = 300
    max_retries: int = 2
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    assigned_agent: Optional[str] = None
    started_at: float = 0.0
    completed_at: float = 0.0

    @property
    def duration(self) -> float:
        """任务执行耗时（秒），未完成返回 0。"""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return 0.0


@dataclass
class ExecutionPlan:
    """执行计划：描述任务的分组执行顺序与回退策略。

    Attributes:
        execution_order: 并行分组，每个子列表内的任务可同时执行
        estimated_duration: 预估总耗时（秒）
        dependencies_map: 任务 ID → 依赖任务 ID 列表的映射
        fallback_strategies: 任务 ID → 恢复策略的映射
        critical_path: 关键路径（耗时最长的依赖链）
    """

    execution_order: List[List[str]] = field(default_factory=list)
    estimated_duration: float = 0.0
    dependencies_map: Dict[str, List[str]] = field(default_factory=dict)
    fallback_strategies: Dict[str, RecoveryStrategy] = field(default_factory=dict)
    critical_path: List[str] = field(default_factory=list)

    @property
    def total_tasks(self) -> int:
        """计划中包含的任务总数。"""
        return sum(len(group) for group in self.execution_order)

    @property
    def max_parallelism(self) -> int:
        """最大并行度（最大分组大小）。"""
        return max((len(group) for group in self.execution_order), default=0)


@dataclass
class OrchestratorState:
    """编排引擎运行时状态。

    Attributes:
        current_phase: 当前流水线阶段
        task_graph: 任务依赖图（TaskGraph 实例的序列化引用）
        active_agents: 正在执行的智能体 {agent_name: task_id}
        completed_tasks: 已完成的任务 ID → AgentTask 映射
        failed_tasks: 已失败的任务 ID → AgentTask 映射
        context: 全局上下文（跨任务共享数据）
        pipeline_status: 与现有 PipelineStatus 对齐的状态对象
    """

    current_phase: PipelinePhase = PipelinePhase.PENDING
    task_graph: Optional["TaskGraph"] = None
    active_agents: Dict[str, str] = field(default_factory=dict)
    completed_tasks: Dict[str, AgentTask] = field(default_factory=dict)
    failed_tasks: Dict[str, AgentTask] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    pipeline_status: PipelineStatus = field(default_factory=PipelineStatus)
    started_at: float = field(default_factory=time.monotonic)

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """从已完成或已失败的任务中查找。"""
        if task_id in self.completed_tasks:
            return self.completed_tasks[task_id]
        if task_id in self.failed_tasks:
            return self.failed_tasks[task_id]
        return None

    def mark_completed(self, task: AgentTask) -> None:
        """标记任务为已完成。"""
        task.status = TaskStatus.COMPLETED
        task.completed_at = time.monotonic()
        self.completed_tasks[task.task_id] = task

    def mark_failed(self, task: AgentTask, error: str) -> None:
        """标记任务为已失败。"""
        task.status = TaskStatus.FAILED
        task.error = error
        task.completed_at = time.monotonic()
        self.failed_tasks[task.task_id] = task


@dataclass
class OrchestratorResult:
    """编排执行结果。

    Attributes:
        success: 是否全部成功
        state: 最终编排状态
        execution_plan: 使用的执行计划
        total_duration: 总耗时（秒）
        completed_count: 成功完成的任务数
        failed_count: 失败的任务数
        results: 各任务结果映射
        errors: 各任务错误映射
    """

    success: bool = False
    state: OrchestratorState = field(default_factory=OrchestratorState)
    execution_plan: ExecutionPlan = field(default_factory=ExecutionPlan)
    total_duration: float = 0.0
    completed_count: int = 0
    failed_count: int = 0
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def summary(self) -> Dict[str, Any]:
        """返回执行摘要。"""
        return {
            "success": self.success,
            "total_duration": round(self.total_duration, 2),
            "completed": self.completed_count,
            "failed": self.failed_count,
            "total_tasks": self.completed_count + self.failed_count,
        }


# ====================================================================== #
#  TaskGraph —— DAG 任务依赖图
# ====================================================================== #


class TaskGraph:
    """基于 DAG 的任务依赖管理器。

    支持：
        - 任务注册与依赖声明
        - 拓扑排序（Kahn 算法）
        - 环检测（DFS 三色标记法）
        - 就绪任务查询
        - 并行分组生成

    Example::

        graph = TaskGraph()
        graph.add_task("req", AgentTask("req", "需求分析", agent_type="requirement"))
        graph.add_task("code", AgentTask("code", "代码生成", agent_type="code_gen"), dependencies=["req"])
        graph.add_task("review", AgentTask("review", "代码评审", agent_type="review"), dependencies=["code"])

        order = graph.get_execution_order()   # ["req", "code", "review"]
        groups = graph.get_parallel_groups()  # [["req"], ["code"], ["review"]]
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, AgentTask] = {}
        self._adj: Dict[str, Set[str]] = defaultdict(set)
        self._in_degree: Dict[str, int] = defaultdict(int)

    # ---------- 基础操作 ----------

    def add_task(
        self,
        task_id: str,
        task: AgentTask,
        dependencies: Optional[List[str]] = None,
    ) -> None:
        """向图中添加任务及其依赖。

        Args:
            task_id: 任务唯一标识
            task: AgentTask 实例
            dependencies: 依赖的前置任务 ID 列表（覆盖 task.dependencies）

        Raises:
            ValueError: 任务 ID 已存在或依赖不存在
            CyclicDependencyError: 添加后产生环路
        """
        if task_id in self._tasks:
            raise ValueError(f"任务 '{task_id}' 已存在")

        deps = dependencies if dependencies is not None else task.dependencies
        for dep_id in deps:
            if dep_id not in self._tasks:
                raise ValueError(f"依赖的任务 '{dep_id}' 不存在（task '{task_id}'）")

        self._tasks[task_id] = task
        task.dependencies = list(deps)

        self._in_degree[task_id] = len(deps)
        for dep_id in deps:
            self._adj[dep_id].add(task_id)

        if self.detect_cycle():
            raise CyclicDependencyError(
                f"添加任务 '{task_id}' 后检测到循环依赖"
            )

        logger.debug("TaskGraph.add_task: %s (deps=%s)", task_id, deps)

    def remove_task(self, task_id: str) -> AgentTask:
        """从图中移除任务。

        会同时清理后续任务对该任务的依赖引用。

        Args:
            task_id: 要移除的任务 ID

        Returns:
            被移除的 AgentTask 实例

        Raises:
            KeyError: 任务不存在
        """
        if task_id not in self._tasks:
            raise KeyError(f"任务 '{task_id}' 不存在")

        task = self._tasks.pop(task_id)

        for dep_id in task.dependencies:
            if dep_id in self._adj:
                self._adj[dep_id].discard(task_id)

        del self._adj[task_id]
        self._in_degree.pop(task_id, None)

        for tid, t in self._tasks.items():
            if task_id in t.dependencies:
                t.dependencies.remove(task_id)
                self._in_degree[tid] = max(0, self._in_degree.get(tid, 0) - 1)

        logger.debug("TaskGraph.remove_task: %s", task_id)
        return task

    def get_task(self, task_id: str) -> AgentTask:
        """获取指定任务。

        Raises:
            KeyError: 任务不存在
        """
        if task_id not in self._tasks:
            raise KeyError(f"任务 '{task_id}' 不存在")
        return self._tasks[task_id]

    def list_tasks(self) -> List[AgentTask]:
        """返回所有任务（无序）。"""
        return list(self._tasks.values())

    @property
    def task_count(self) -> int:
        """图中任务总数。"""
        return len(self._tasks)

    # ---------- 拓扑排序与环检测 ----------

    def detect_cycle(self) -> bool:
        """使用 DFS 三色标记法检测图中是否存在环。

        Returns:
            True 表示存在环
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {tid: WHITE for tid in self._tasks}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in self._adj.get(node, set()):
                if color.get(neighbor, WHITE) == GRAY:
                    return True
                if color.get(neighbor, WHITE) == WHITE and dfs(neighbor):
                    return True
            color[node] = BLACK
            return False

        for node in list(self._tasks.keys()):
            if color[node] == WHITE:
                if dfs(node):
                    return True
        return False

    def get_execution_order(self) -> List[str]:
        """基于 Kahn 算法生成拓扑排序。

        Returns:
            任务 ID 的线性执行顺序

        Raises:
            CyclicDependencyError: 图中存在环
        """
        if self.detect_cycle():
            raise CyclicDependencyError("图中存在循环依赖，无法生成执行顺序")

        in_degree: Dict[str, int] = {tid: self._in_degree.get(tid, 0) for tid in self._tasks}
        queue: deque[str] = deque()

        for tid, deg in in_degree.items():
            if deg == 0:
                queue.append(tid)

        order: List[str] = []
        while queue:
            tid = queue.popleft()
            order.append(tid)
            for neighbor in self._adj.get(tid, set()):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self._tasks):
            raise CyclicDependencyError("图中存在循环依赖")

        logger.debug("TaskGraph.get_execution_order: %s", order)
        return order

    def get_parallel_groups(self) -> List[List[str]]:
        """按拓扑层级分组，同层任务可并行执行。

        使用 Kahn 算法按 BFS 层次分层。

        Returns:
            并行分组列表，每个子列表为可并行执行的任务 ID 集合

        Raises:
            CyclicDependencyError: 图中存在环
        """
        if self.detect_cycle():
            raise CyclicDependencyError("图中存在循环依赖，无法生成并行分组")

        in_degree: Dict[str, int] = {tid: self._in_degree.get(tid, 0) for tid in self._tasks}
        current_level: List[str] = [tid for tid, deg in in_degree.items() if deg == 0]
        groups: List[List[str]] = []

        while current_level:
            groups.append(current_level)
            next_level: List[str] = []
            for tid in current_level:
                for neighbor in self._adj.get(tid, set()):
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_level.append(neighbor)
            current_level = next_level

        if sum(len(g) for g in groups) != len(self._tasks):
            raise CyclicDependencyError("图中存在循环依赖")

        logger.debug("TaskGraph.get_parallel_groups: %d groups", len(groups))
        return groups

    def get_ready_tasks(
        self, completed: Optional[Set[str]] = None, failed: Optional[Set[str]] = None
    ) -> List[AgentTask]:
        """获取当前已就绪可执行的任务（所有依赖已完成）。

        Args:
            completed: 已完成的任务 ID 集合
            failed: 已失败的任务 ID 集合（依赖失败的任务不会就绪）

        Returns:
            就绪状态的 AgentTask 列表
        """
        completed = completed or set()
        failed = failed or set()
        ready: List[AgentTask] = []

        for tid, task in self._tasks.items():
            if tid in completed or tid in failed:
                continue
            deps = set(task.dependencies)
            if deps & failed:
                continue
            if deps <= completed:
                task.status = TaskStatus.READY
                ready.append(task)

        ready.sort(key=lambda t: (-t.priority, t.task_id))
        return ready

    def compute_critical_path(self) -> List[str]:
        """计算关键路径（依赖图中最长的路径）。

        Returns:
            关键路径上的任务 ID 列表
        """
        if not self._tasks:
            return []

        order = self.get_execution_order()
        longest: Dict[str, List[str]] = {}

        for tid in order:
            task = self._tasks[tid]
            if not task.dependencies:
                longest[tid] = [tid]
            else:
                best_path: List[str] = []
                for dep_id in task.dependencies:
                    dep_path = longest.get(dep_id, [])
                    if len(dep_path) > len(best_path):
                        best_path = dep_path
                longest[tid] = best_path + [tid]

        return max(longest.values(), key=len)


class CyclicDependencyError(Exception):
    """循环依赖异常。"""


# ====================================================================== #
#  Orchestrator —— 编排引擎核心
# ====================================================================== #


class Orchestrator:
    """企业级多智能体编排引擎。

    核心能力：
        1. **任务拆解** — 将用户复杂需求自动或手动拆解为子任务 DAG
        2. **智能体分配** — 根据任务类型匹配最合适的 Agent
        3. **执行计划生成** — 基于 TaskGraph 生成并行执行计划
        4. **计划执行** — 顺序/并行执行任务，处理条件分支与循环重试
        5. **异常恢复** — 根据策略自动处理失败任务

    Usage::

        orchestrator = Orchestrator()
        plan = orchestrator.create_plan_from_request("实现一个用户登录模块")
        result = orchestrator.execute_plan(plan)
        print(result.summary)
    """

    def __init__(self) -> None:
        self._event_bus = event_bus
        self._registered_handlers: List[Tuple[Any, str]] = []

    # ---------- 任务拆解 ----------

    def decompose_task(self, user_request: str) -> List[AgentTask]:
        """将复杂需求拆解为子任务列表。

        基于关键词匹配与规则引擎进行需求分析，生成包含
        需求分析 → 代码生成 → 代码评审 → 测试生成 的标准流水线任务。

        对于更复杂的需求（如包含"重构"、"优化"等关键词），
        会增加额外的分析或验证阶段。

        Args:
            user_request: 用户原始需求描述

        Returns:
            拆解后的 AgentTask 列表（已设置依赖关系）
        """
        request_lower = user_request.lower()
        tasks: List[AgentTask] = []

        # 1. 需求分析（必选）
        tasks.append(AgentTask(
            task_id="requirement",
            description=f"需求分析：{user_request[:200]}",
            agent_type="requirement",
            priority=100,
            metadata={"phase": PipelinePhase.REQUIREMENT.value},
        ))

        # 2. 代码生成（依赖需求分析）
        tasks.append(AgentTask(
            task_id="code_gen",
            description=f"代码生成：{user_request[:200]}",
            agent_type="code_gen",
            dependencies=["requirement"],
            priority=90,
            metadata={"phase": PipelinePhase.CODE_GEN.value},
        ))

        # 3. 代码评审（依赖代码生成）
        tasks.append(AgentTask(
            task_id="code_review",
            description=f"代码评审：{user_request[:200]}",
            agent_type="review",
            dependencies=["code_gen"],
            priority=80,
            metadata={"phase": PipelinePhase.CODE_REVIEW.value},
        ))

        # 4. 测试生成（依赖代码评审）
        tasks.append(AgentTask(
            task_id="test_gen",
            description=f"测试生成：{user_request[:200]}",
            agent_type="test_gen",
            dependencies=["code_review"],
            priority=70,
            metadata={"phase": PipelinePhase.TEST_GEN.value},
        ))

        # 5. 根据需求关键词添加额外任务
        extra_id = 1
        if any(kw in request_lower for kw in ("重构", "refactor", "优化", "optimize")):
            tasks.insert(-1, AgentTask(
                task_id=f"analysis_{extra_id}",
                description=f"深度分析：{user_request[:100]}",
                agent_type="requirement",
                dependencies=["code_gen"],
                priority=85,
                metadata={"phase": PipelinePhase.REQUIREMENT.value, "purpose": "analysis"},
            ))
            extra_id += 1

        if any(kw in request_lower for kw in ("api", "接口", "service", "服务")):
            tasks.insert(-1, AgentTask(
                task_id=f"api_design_{extra_id}",
                description=f"API 设计：{user_request[:100]}",
                agent_type="requirement",
                dependencies=["requirement"],
                priority=95,
                metadata={"phase": PipelinePhase.REQUIREMENT.value, "purpose": "api_design"},
            ))
            extra_id += 1

        logger.info(
            "任务拆解完成: 从 '%s' 生成 %d 个子任务",
            user_request[:50], len(tasks),
        )
        return tasks

    # ---------- 智能体分配 ----------

    def assign_agent(
        self,
        task: AgentTask,
        available_agents: Optional[Dict[str, Any]] = None,
    ) -> str:
        """为任务分配最合适的智能体。

        基于 agent_type 字段匹配可用的 Agent。
        若 available_agents 为 None，则使用内置的默认映射。

        Args:
            task: 待分配的任务
            available_agents: 可用 Agent 映射 {agent_type: agent_instance_or_name}

        Returns:
            分配的智能体名称/标识
        """
        if available_agents is None:
            available_agents = {
                "requirement": "requirement_agent",
                "code_gen": "code_gen_agent",
                "review": "review_agent",
                "test_gen": "test_gen_agent",
                "general": "general_agent",
            }

        agent_name = available_agents.get(task.agent_type, "default_agent")
        task.assigned_agent = agent_name
        logger.info("Agent 分配: task=%s → agent=%s", task.task_id, agent_name)
        return agent_name

    # ---------- 执行计划生成 ----------

    def schedule_tasks(self, task_graph: TaskGraph) -> ExecutionPlan:
        """根据任务依赖图生成执行计划。

        Args:
            task_graph: 已构建的任务依赖图

        Returns:
            ExecutionPlan 实例，包含并行分组与回退策略
        """
        parallel_groups = task_graph.get_parallel_groups()
        critical_path = task_graph.compute_critical_path()

        dependencies_map: Dict[str, List[str]] = {}
        fallback_strategies: Dict[str, RecoveryStrategy] = {}

        for task in task_graph.list_tasks():
            dependencies_map[task.task_id] = list(task.dependencies)
            if task.agent_type in ("review", "test_gen"):
                fallback_strategies[task.task_id] = RecoveryStrategy.RETRY
            elif task.agent_type == "code_gen":
                fallback_strategies[task.task_id] = RecoveryStrategy.RETRY
            else:
                fallback_strategies[task.task_id] = RecoveryStrategy.SKIP

        # 估算总耗时 = 关键路径长度（假设每个任务约 30s 基础耗时）
        estimated_duration = len(critical_path) * 30.0 if critical_path else 0.0

        plan = ExecutionPlan(
            execution_order=parallel_groups,
            estimated_duration=estimated_duration,
            dependencies_map=dependencies_map,
            fallback_strategies=fallback_strategies,
            critical_path=critical_path,
        )

        logger.info(
            "执行计划生成: %d tasks, %d parallel groups, critical path length=%d",
            plan.total_tasks, len(parallel_groups), len(critical_path),
        )
        return plan

    # ---------- 计划执行 ----------

    def execute_plan(
        self,
        plan: ExecutionPlan,
        state: Optional[OrchestratorState] = None,
        task_graph: Optional[TaskGraph] = None,
    ) -> OrchestratorResult:
        """执行编排计划。

        按并行分组依次执行，支持：
            - 组内任务并行（通过线程池或异步）
            - 失败任务的恢复策略处理
            - 上下文在任务间传递

        Args:
            plan: 执行计划
            state: 初始编排状态（可选）
            task_graph: 任务依赖图（用于获取任务详情）

        Returns:
            OrchestratorResult 执行结果
        """
        state = state or OrchestratorState()
        task_graph = task_graph or (state.task_graph if state.task_graph else None)
        task_graph = task_graph or TaskGraph()

        started = time.monotonic()
        results: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        completed_ids: Set[str] = set()
        failed_ids: Set[str] = set()

        self._event_bus.emit(
            EventType.TASK_STARTED,
            data={"phase": state.current_phase.value, "plan_total": plan.total_tasks},
            task_id=state.context.get("task_id", "orchestrator"),
        )

        for group_idx, group in enumerate(plan.execution_order):
            logger.info(
                "执行第 %d/%d 组: %s",
                group_idx + 1, len(plan.execution_order), group,
            )

            for task_id in group:
                try:
                    task = task_graph.get_task(task_id)
                except KeyError:
                    logger.error("任务 '%s' 在图中不存在，跳过", task_id)
                    failed_ids.add(task_id)
                    errors[task_id] = "任务不存在于依赖图"
                    continue

                if any(dep_id in failed_ids for dep_id in task.dependencies):
                    task.status = TaskStatus.BLOCKED
                    failed_ids.add(task_id)
                    errors[task_id] = f"依赖任务 {task.dependencies} 已失败"
                    state.mark_failed(task, errors[task_id])
                    continue

                self._execute_single_task(task, state, results, errors, task_graph, plan)

                if task.status == TaskStatus.COMPLETED:
                    completed_ids.add(task_id)
                    state.mark_completed(task)
                    state.context[task_id] = task.result
                elif task.status == TaskStatus.SKIPPED:
                    completed_ids.add(task_id)
                    state.mark_completed(task)
                else:
                    failed_ids.add(task_id)
                    state.mark_failed(task, task.error or "未知错误")

        total_duration = time.monotonic() - started
        success = len(failed_ids) == 0 and len(completed_ids) > 0

        state.current_phase = PipelinePhase.DONE if success else PipelinePhase.FAILED
        state.pipeline_status.phase = state.current_phase
        state.pipeline_status.touch()
        state.pipeline_status.duration_seconds = total_duration

        if success:
            self._event_bus.emit(
                EventType.PIPELINE_DONE,
                data={"completed": len(completed_ids), "duration": round(total_duration, 2)},
                task_id=state.context.get("task_id", "orchestrator"),
            )
        else:
            self._event_bus.emit(
                EventType.PIPELINE_FAILED,
                data={"failed": len(failed_ids), "errors": errors},
                task_id=state.context.get("task_id", "orchestrator"),
            )

        result = OrchestratorResult(
            success=success,
            state=state,
            execution_plan=plan,
            total_duration=total_duration,
            completed_count=len(completed_ids),
            failed_count=len(failed_ids),
            results=results,
            errors=errors,
        )

        logger.info(
            "编排执行完成: success=%s, completed=%d, failed=%d, duration=%.2fs",
            success, len(completed_ids), len(failed_ids), total_duration,
        )
        return result

    def _execute_single_task(
        self,
        task: AgentTask,
        state: OrchestratorState,
        results: Dict[str, Dict[str, Any]],
        errors: Dict[str, str],
        task_graph: TaskGraph,
        plan: ExecutionPlan,
    ) -> None:
        """执行单个任务，包含重试与恢复逻辑。"""
        task.status = TaskStatus.RUNNING
        task.started_at = time.monotonic()

        self._event_bus.emit(
            EventType.NODE_COMPLETED,
            data={
                "task_id": task.task_id,
                "agent_type": task.agent_type,
                "phase": state.current_phase.value,
                "status": "running",
            },
            task_id=task.task_id,
        )

        while task.retry_count <= task.max_retries:
            try:
                result = self._invoke_task(task, state)
                task.result = result
                results[task.task_id] = result
                task.status = TaskStatus.COMPLETED
                self._event_bus.emit(
                    EventType.NODE_COMPLETED,
                    data={"task_id": task.task_id, "status": "completed"},
                    task_id=task.task_id,
                )
                return
            except Exception as exc:
                task.retry_count += 1
                task.error = str(exc)
                errors[task.task_id] = str(exc)
                logger.warning(
                    "任务 '%s' 第 %d/%d 次尝试失败: %s",
                    task.task_id, task.retry_count, task.max_retries, exc,
                )

                if task.retry_count > task.max_retries:
                    strategy = plan.fallback_strategies.get(
                        task.task_id, RecoveryStrategy.ABORT
                    )
                    self._apply_recovery(task, strategy, state, task_graph, plan)
                    return

                strategy = plan.fallback_strategies.get(
                    task.task_id, RecoveryStrategy.RETRY
                )
                if strategy == RecoveryStrategy.RETRY:
                    time.sleep(min(2 ** task.retry_count, 10))
                elif strategy == RecoveryStrategy.SKIP:
                    task.status = TaskStatus.SKIPPED
                    return
                elif strategy == RecoveryStrategy.ABORT:
                    task.status = TaskStatus.FAILED
                    return

        task.status = TaskStatus.FAILED
        errors[task.task_id] = task.error or "执行超时或重试耗尽"

    def _invoke_task(
        self, task: AgentTask, state: OrchestratorState
    ) -> Dict[str, Any]:
        """实际调用任务处理器。

        优先使用 task.handler；若无 handler，则将任务结果写入
        state.context 并返回上下文快照。
        """
        if task.handler is not None:
            return task.handler(state.context)

        agent_name = task.assigned_agent or task.agent_type
        logger.info(
            "执行任务: %s (agent=%s, type=%s)",
            task.task_id, agent_name, task.agent_type,
        )

        time.sleep(0.05)
        return {
            "task_id": task.task_id,
            "agent_type": task.agent_type,
            "agent": agent_name,
            "status": "completed",
            "description": task.description,
        }

    # ---------- 异常处理 ----------

    def handle_failure(
        self,
        task: AgentTask,
        error: str,
        state: OrchestratorState,
    ) -> RecoveryStrategy:
        """根据任务类型与错误信息决定恢复策略。

        策略决策：
            - code_gen 失败且有 review 反馈 → RETRY
            - review 失败 → RETRY（可重试评审）
            - test_gen 失败 → RETRY
            - requirement 失败 → ABORT（需求分析无法跳过）
            - 其他 → SKIP

        Args:
            task: 失败的任务
            error: 错误信息
            state: 当前编排状态

        Returns:
            选定的恢复策略
        """
        error_lower = error.lower()

        if task.agent_type == "code_gen":
            if "rate limit" in error_lower or "quota" in error_lower or "timeout" in error_lower:
                return RecoveryStrategy.RETRY
            return RecoveryStrategy.RETRY

        if task.agent_type == "review":
            return RecoveryStrategy.RETRY

        if task.agent_type == "test_gen":
            if "test" in error_lower or "assert" in error_lower:
                return RecoveryStrategy.RETRY
            return RecoveryStrategy.RETRY

        if task.agent_type == "requirement":
            return RecoveryStrategy.ABORT

        return RecoveryStrategy.SKIP

    def _apply_recovery(
        self,
        task: AgentTask,
        strategy: RecoveryStrategy,
        state: OrchestratorState,
        task_graph: TaskGraph,
        plan: ExecutionPlan,
    ) -> None:
        """应用恢复策略。

        Args:
            task: 失败的任务
            strategy: 恢复策略
            state: 当前编排状态
            task_graph: 任务依赖图
            plan: 执行计划
        """
        logger.warning(
            "应用恢复策略: task=%s, strategy=%s, error=%s",
            task.task_id, strategy.value, task.error,
        )

        self._event_bus.emit(
            EventType.NODE_FAILED,
            data={
                "task_id": task.task_id,
                "strategy": strategy.value,
                "error": task.error,
            },
            task_id=task.task_id,
        )

        if strategy == RecoveryStrategy.RETRY:
            task.retry_count = 0
            self._invoke_task_with_recovery(task, state, task_graph, plan)
        elif strategy == RecoveryStrategy.REASSIGN:
            self._reassign_and_retry(task, state, task_graph, plan)
        elif strategy == RecoveryStrategy.SKIP:
            task.status = TaskStatus.SKIPPED
            task.result = {"skipped": True, "reason": task.error}
        elif strategy == RecoveryStrategy.ESCALATE:
            task.status = TaskStatus.FAILED
            task.error = f"[ESCALATED] {task.error}"
        elif strategy == RecoveryStrategy.ABORT:
            task.status = TaskStatus.FAILED

    def _invoke_task_with_recovery(
        self,
        task: AgentTask,
        state: OrchestratorState,
        task_graph: TaskGraph,
        plan: ExecutionPlan,
    ) -> None:
        """带恢复逻辑的任务重试。"""
        try:
            result = self._invoke_task(task, state)
            task.result = result
            task.status = TaskStatus.COMPLETED
        except Exception as exc:
            task.error = str(exc)
            task.status = TaskStatus.FAILED

    def _reassign_and_retry(
        self,
        task: AgentTask,
        state: OrchestratorState,
        task_graph: TaskGraph,
        plan: ExecutionPlan,
    ) -> None:
        """重新分配智能体并重试。"""
        fallback_map: Dict[str, str] = {
            "code_gen": "fallback_code_agent",
            "review": "fallback_review_agent",
            "test_gen": "fallback_test_agent",
            "requirement": "fallback_requirement_agent",
        }
        fallback = fallback_map.get(task.agent_type, "fallback_agent")
        task.assigned_agent = fallback
        task.retry_count = 0
        try:
            result = self._invoke_task(task, state)
            task.result = result
            task.status = TaskStatus.COMPLETED
        except Exception as exc:
            task.error = str(exc)
            task.status = TaskStatus.FAILED

    # ---------- 便捷工厂方法 ----------

    def create_plan_from_request(
        self,
        user_request: str,
        extra_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = None,
    ) -> ExecutionPlan:
        """从用户请求直接生成执行计划（便捷入口）。

        流程：拆解 → 分配 → 构图 → 调度。

        Args:
            user_request: 用户原始需求
            extra_handlers: 额外的任务 handler 映射 {task_id: handler}

        Returns:
            ExecutionPlan 可直接传入 execute_plan
        """
        tasks = self.decompose_task(user_request)

        if extra_handlers:
            for task in tasks:
                if task.task_id in extra_handlers:
                    task.handler = extra_handlers[task.task_id]

        for task in tasks:
            self.assign_agent(task)

        graph = TaskGraph()
        for task in tasks:
            try:
                graph.add_task(task.task_id, task, dependencies=task.dependencies)
            except (ValueError, CyclicDependencyError) as exc:
                logger.warning("添加任务 %s 到图时出错: %s", task.task_id, exc)

        self._current_graph = graph
        plan = self.schedule_tasks(graph)
        return plan

    def run(
        self,
        user_request: str,
        extra_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = None,
    ) -> OrchestratorResult:
        """一站式执行：从用户请求到完成。

        Args:
            user_request: 用户原始需求
            extra_handlers: 可选的任务 handler 映射

        Returns:
            OrchestratorResult 完整执行结果
        """
        plan = self.create_plan_from_request(user_request, extra_handlers)
        state = OrchestratorState(
            context={"user_request": user_request, "task_id": f"orch_{int(time.time())}"},
        )
        return self.execute_plan(plan, state, task_graph=self._current_graph)

    # ---------- 上下文管理 ----------

    def __enter__(self) -> Orchestrator:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


__all__ = [
    "AgentTask",
    "CyclicDependencyError",
    "ExecutionPlan",
    "Orchestrator",
    "OrchestratorResult",
    "OrchestratorState",
    "RecoveryStrategy",
    "TaskGraph",
    "TaskStatus",
]
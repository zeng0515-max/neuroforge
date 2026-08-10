"""企业级任务拆解与自主决策系统。

本模块提供智能任务拆解、复杂度评估、智能体推荐、任务依赖生成
以及自主决策引擎等核心能力，与现有 Orchestrator、AgentProtocol、
LLMAdapter 完全兼容，复用 llm_adapter 进行复杂度分析和策略选择。

核心组件:
    - ComplexityScore: 任务复杂度评分
    - RequirementAnalysis: 需求分析结果
    - Strategy: 执行策略枚举
    - TaskTree: 层级任务树
    - TaskDecomposer: 智能任务拆解器
    - DecisionEngine: 自主决策引擎
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.agents.agent_protocol import AgentRole
from app.core.llm_adapter import ChatResponse, LLMUsage, llm
from app.graph.orchestrator import (
    AgentTask as OrchAgentTask,
    ExecutionPlan,
    TaskGraph,
)

logger = logging.getLogger(__name__)


# ====================================================================== #
#  复杂度评分
# ====================================================================== #


@dataclass
class ComplexityScore:
    """任务复杂度评分结果。

    Attributes:
        score: 复杂度分值 (0-100)，分数越高表示任务越复杂
        level: 复杂度等级 (simple/medium/complex/advanced)
        factors: 影响因素及权重映射，如 {"multi_step": 0.3, "cross_domain": 0.2}
        reasoning: 复杂度评估的推理过程说明
    """

    score: float = 0.0
    level: str = "simple"
    factors: Dict[str, float] = field(default_factory=dict)
    reasoning: str = ""

    @property
    def level_label(self) -> str:
        """返回复杂度等级的中文描述。"""
        mapping = {
            "simple": "简单",
            "medium": "中等",
            "complex": "复杂",
            "advanced": "高级",
        }
        return mapping.get(self.level, self.level)


# ====================================================================== #
#  需求分析
# ====================================================================== #


@dataclass
class RequirementAnalysis:
    """需求分析结果，包含目标、范围、约束、复杂度评估等。

    Attributes:
        goal: 用户需求的核心目标描述
        scope: 需求范围列表，描述需要覆盖的功能点
        constraints: 约束条件列表，如技术限制、时间要求、性能指标等
        assumptions: 假设前提列表，即默认成立的前置条件
        complexity: 需求的复杂度评分
        suggested_roles: 推荐参与的智能体角色列表
        estimated_steps: 预估的执行步骤数
        keywords: 需求关键词列表
        tech_stack: 涉及的技术栈列表
    """

    goal: str = ""
    scope: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    complexity: ComplexityScore = field(default_factory=ComplexityScore)
    suggested_roles: List[AgentRole] = field(default_factory=list)
    estimated_steps: int = 0
    keywords: List[str] = field(default_factory=list)
    tech_stack: List[str] = field(default_factory=list)

    @property
    def is_simple(self) -> bool:
        """判断是否为简单需求。"""
        return self.complexity.level == "simple"

    @property
    def is_complex(self) -> bool:
        """判断是否为复杂或更高等级需求。"""
        return self.complexity.level in ("complex", "advanced")


# ====================================================================== #
#  执行策略枚举
# ====================================================================== #


class Strategy(str, Enum):
    """执行策略枚举，定义任务编排的执行模式。

    - SEQUENTIAL: 顺序执行，任务按依赖顺序逐个执行
    - PARALLEL: 并行执行，无依赖关系的任务同时执行
    - PIPELINE: 流水线执行，任务按流水线阶段依次推进
    - ITERATIVE: 迭代执行，支持多轮迭代优化直至收敛
    - HYBRID: 混合执行，结合顺序/并行/流水线的复合策略
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    PIPELINE = "pipeline"
    ITERATIVE = "iterative"
    HYBRID = "hybrid"

    @property
    def description(self) -> str:
        """返回策略的中文描述。"""
        descriptions = {
            "sequential": "顺序执行：任务按依赖顺序逐个执行",
            "parallel": "并行执行：无依赖关系的任务同时执行",
            "pipeline": "流水线执行：任务按流水线阶段依次推进",
            "iterative": "迭代执行：支持多轮迭代优化直至收敛",
            "hybrid": "混合执行：结合多种执行模式的复合策略",
        }
        return descriptions.get(self.value, self.value)


# ====================================================================== #
#  任务树节点
# ====================================================================== #


@dataclass
class TaskTreeNode:
    """任务树中的单个节点。

    Attributes:
        node_id: 节点唯一标识符
        description: 任务描述
        agent_type: 建议的智能体类型 (如 requirement/code_gen/review/test_gen)
        agent_role: 关联的智能体角色
        complexity: 该任务的复杂度评分
        children: 子节点 ID 列表
        metadata: 扩展元数据
        status: 节点状态 (pending/ready/completed/failed)
        result: 任务执行结果
    """

    node_id: str = ""
    description: str = ""
    agent_type: str = "general"
    agent_role: Optional[AgentRole] = None
    complexity: ComplexityScore = field(default_factory=ComplexityScore)
    children: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: Optional[Dict[str, Any]] = None

    def is_leaf(self) -> bool:
        """判断是否为叶子节点（无子节点）。"""
        return len(self.children) == 0


# ====================================================================== #
#  任务树
# ====================================================================== #


@dataclass
class TaskTree:
    """层级任务树，描述需求的层级分解结构。

    任务树以 root 为根节点，每个节点可包含子节点，表示
    父任务可以进一步分解为更细粒度的子任务。

    Attributes:
        root: 根节点 ID
        nodes: 节点 ID → TaskTreeNode 的映射
        edges: 边列表，每条边为 (父节点ID, 子节点ID) 元组
        depth: 树的最大深度
        total_tasks: 树中包含的任务总数
    """

    root: str = ""
    nodes: Dict[str, TaskTreeNode] = field(default_factory=dict)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    depth: int = 0
    total_tasks: int = 0

    # ------------------------------------------------------------------ #
    #  节点操作
    # ------------------------------------------------------------------ #

    def add_node(self, node: TaskTreeNode, parent_id: Optional[str] = None) -> None:
        """向树中添加节点。

        Args:
            node: 要添加的节点
            parent_id: 可选的父节点 ID，若指定则自动建立父子关系

        Raises:
            ValueError: 节点 ID 已存在或父节点不存在
        """
        if not self.root and not self.nodes:
            self.root = node.node_id

        if node.node_id in self.nodes:
            raise ValueError(f"节点 '{node.node_id}' 已存在于任务树中")

        if parent_id is not None and parent_id not in self.nodes:
            raise ValueError(f"父节点 '{parent_id}' 不存在")

        self.nodes[node.node_id] = node

        if parent_id is not None:
            self.edges.append((parent_id, node.node_id))
            self.nodes[parent_id].children.append(node.node_id)

        self._recompute_stats()
        logger.debug("TaskTree.add_node: %s (parent=%s)", node.node_id, parent_id)

    def remove_node(self, node_id: str) -> TaskTreeNode:
        """从树中移除节点及其所有子节点。

        Args:
            node_id: 要移除的节点 ID

        Returns:
            被移除的 TaskTreeNode 实例

        Raises:
            KeyError: 节点不存在
            ValueError: 尝试移除根节点
        """
        if node_id == self.root:
            raise ValueError("Cannot remove root node from task tree")
        if node_id not in self.nodes:
            raise KeyError(f"节点 '{node_id}' 不存在")

        removed = self.nodes.pop(node_id)

        for child_id in removed.children:
            if child_id in self.nodes:
                self.remove_node(child_id)

        self.edges = [(p, c) for p, c in self.edges if p != node_id and c != node_id]

        for parent_id, node in self.nodes.items():
            if node_id in node.children:
                node.children.remove(node_id)

        self._recompute_stats()
        logger.debug("TaskTree.remove_node: %s", node_id)
        return removed

    def get_node(self, node_id: str) -> TaskTreeNode:
        """获取指定节点。

        Args:
            node_id: 节点 ID

        Returns:
            TaskTreeNode 实例

        Raises:
            KeyError: 节点不存在
        """
        if node_id not in self.nodes:
            raise KeyError(f"节点 '{node_id}' 不存在")
        return self.nodes[node_id]

    def _recompute_stats(self) -> None:
        """重新计算树的深度和任务总数。"""
        self.total_tasks = len(self.nodes)
        self.depth = self._compute_depth(self.root)

    def _compute_depth(self, node_id: str, visited: Optional[Set[str]] = None) -> int:
        """递归计算从指定节点开始的深度。

        Args:
            node_id: 起始节点 ID
            visited: 已访问节点集合（用于防止循环）

        Returns:
            以该节点为根的子树深度
        """
        if visited is None:
            visited = set()
        if node_id in visited or node_id not in self.nodes:
            return 0
        visited.add(node_id)

        node = self.nodes[node_id]
        if not node.children:
            return 1

        max_child_depth = 0
        for child_id in node.children:
            child_depth = self._compute_depth(child_id, visited.copy())
            max_child_depth = max(max_child_depth, child_depth)

        return 1 + max_child_depth

    # ------------------------------------------------------------------ #
    #  查询方法
    # ------------------------------------------------------------------ #

    def get_leaf_tasks(self) -> List[TaskTreeNode]:
        """获取所有叶子节点（无子节点的任务）。

        Returns:
            叶子节点的 TaskTreeNode 列表
        """
        return [node for node in self.nodes.values() if node.is_leaf()]

    def get_critical_path(self) -> List[str]:
        """获取关键路径（从根到叶的最长路径）。

        Returns:
            关键路径上的节点 ID 列表
        """
        if not self.nodes or not self.root:
            return []

        longest_path: List[str] = []

        def dfs(node_id: str, current_path: List[str], visited: Set[str]) -> None:
            if node_id in visited or node_id not in self.nodes:
                return
            visited.add(node_id)
            current_path.append(node_id)

            nonlocal longest_path
            if len(current_path) > len(longest_path):
                longest_path = list(current_path)

            node = self.nodes[node_id]
            for child_id in node.children:
                dfs(child_id, current_path, visited.copy())

            current_path.pop()

        dfs(self.root, [], set())
        return longest_path

    def flatten(self) -> List[TaskTreeNode]:
        """按广度优先顺序扁平化所有节点。

        Returns:
            所有节点的 TaskTreeNode 列表，按 BFS 顺序排列
        """
        if not self.nodes or not self.root:
            return []

        result: List[TaskTreeNode] = []
        queue: deque[str] = deque([self.root])
        visited: Set[str] = set()

        while queue:
            node_id = queue.popleft()
            if node_id in visited or node_id not in self.nodes:
                continue
            visited.add(node_id)

            node = self.nodes[node_id]
            result.append(node)

            for child_id in node.children:
                if child_id not in visited:
                    queue.append(child_id)

        return result

    # ------------------------------------------------------------------ #
    #  工具方法
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        """将任务树序列化为字典。

        Returns:
            可 JSON 序列化的字典表示
        """
        return {
            "root": self.root,
            "nodes": {
                nid: {
                    "node_id": n.node_id,
                    "description": n.description,
                    "agent_type": n.agent_type,
                    "agent_role": n.agent_role.value if n.agent_role else None,
                    "complexity": {
                        "score": n.complexity.score,
                        "level": n.complexity.level,
                        "factors": n.complexity.factors,
                        "reasoning": n.complexity.reasoning,
                    },
                    "children": list(n.children),
                    "metadata": n.metadata,
                    "status": n.status,
                }
                for nid, n in self.nodes.items()
            },
            "edges": [list(e) for e in self.edges],
            "depth": self.depth,
            "total_tasks": self.total_tasks,
        }


# ====================================================================== #
#  决策选项与结果
# ====================================================================== #


@dataclass
class Option:
    """决策备选方案。

    Attributes:
        name: 方案名称
        description: 方案描述
        pros: 方案优点列表
        cons: 方案缺点列表
        score: 综合评分 (0-100)
        metadata: 扩展元数据
    """

    name: str = ""
    description: str = ""
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionResult:
    """决策评估结果。

    Attributes:
        selected: 选定的最优方案
        scores: 各方案名称 → 得分的映射
        reasoning: 选择该方案的推理过程
        alternatives: 未选中的备选方案列表
    """

    selected: Option = field(default_factory=Option)
    scores: Dict[str, float] = field(default_factory=dict)
    reasoning: str = ""
    alternatives: List[Option] = field(default_factory=list)


# ====================================================================== #
#  智能任务拆解器
# ====================================================================== #


class TaskDecomposer:
    """智能任务拆解器，负责需求分析、复杂度评估、任务分解与智能体推荐。

    本拆解器结合规则引擎与 LLM 能力，实现对用户需求的深度分析，
    生成层级化的任务树，并为每个子任务推荐最合适的智能体角色。

    Usage::

        decomposer = TaskDecomposer()
        analysis = decomposer.analyze_request("实现一个用户登录模块")
        tree = decomposer.decompose(analysis)
        graph = decomposer.generate_dependencies(tree.flatten())

    Attributes:
        _llm: LLMAdapter 实例，用于复杂度分析与策略选择
        _complexity_keywords: 复杂度关键词权重映射
        _role_keywords: 智能体角色关键词映射
    """

    _COMPLEXITY_KEYWORDS: Dict[str, float] = {
        "refactor": 0.3, "重构": 0.3,
        "optimize": 0.25, "优化": 0.25,
        "distributed": 0.3, "分布式": 0.3,
        "concurrent": 0.25, "并发": 0.25,
        "async": 0.2, "异步": 0.2,
        "multi": 0.15, "多": 0.1,
        "api": 0.1, "接口": 0.1,
        "database": 0.15, "数据库": 0.15,
        "security": 0.2, "安全": 0.2,
        "deploy": 0.1, "部署": 0.1,
        "test": 0.05, "测试": 0.05,
        "simple": -0.1, "简单": -0.1,
        "quick": -0.1, "快速": -0.1,
    }

    _ROLE_KEYWORDS: Dict[AgentRole, List[str]] = {
        AgentRole.ARCHITECT: [
            "架构", "设计", "系统", "模块", "结构", "design", "architecture",
            "refactor", "重构", "framework", "框架",
        ],
        AgentRole.CODER: [
            "实现", "编写", "代码", "开发", "函数", "类", "implement", "code",
            "write", "函数", "接口", "api", "逻辑",
        ],
        AgentRole.REVIEWER: [
            "评审", "审查", "检查", "代码质量", "review", "check",
            "validate", "验证", "规范", "standard",
        ],
        AgentRole.TESTER: [
            "测试", "用例", "验证", "覆盖率", "test", "unittest",
            "pytest", "assert", "bdd", "tdd",
        ],
        AgentRole.ORCHESTRATOR: [
            "编排", "调度", "协调", "流程", "pipeline", "workflow",
            "orchestration", "schedule", "decompose", "拆解",
        ],
        AgentRole.ANALYST: [
            "需求", "分析", "调研", "数据", "分析", "requirement",
            "analysis", "research", "spec", "规格",
        ],
        AgentRole.DEPLOYER: [
            "部署", "发布", "运维", "docker", "k8s", "deploy",
            "release", "devops", "ci", "cd", "monitor",
        ],
    }

    _TECH_KEYWORDS: Dict[str, List[str]] = {
        "python": ["python", "py", "pip"],
        "javascript": ["javascript", "js", "node", "nodejs"],
        "typescript": ["typescript", "ts"],
        "react": ["react", "jsx", "tsx"],
        "vue": ["vue", "vuejs"],
        "fastapi": ["fastapi"],
        "django": ["django"],
        "flask": ["flask"],
        "sql": ["sql", "mysql", "postgresql", "sqlite"],
        "mongodb": ["mongodb", "mongo"],
        "redis": ["redis"],
        "docker": ["docker", "container"],
        "aws": ["aws", "lambda", "ec2", "s3"],
        "linux": ["linux", "bash", "shell"],
        "git": ["git"],
        "api": ["api", "rest", "grpc", "http"],
        "websocket": ["websocket", "ws"],
        "ai": ["ai", "llm", "gpt", "model", "ml", "机器学习"],
    }

    def __init__(self) -> None:
        self._llm = llm

    # ------------------------------------------------------------------ #
    #  需求分析
    # ------------------------------------------------------------------ #

    def analyze_request(self, user_request: str) -> RequirementAnalysis:
        """分析用户需求，生成结构化的需求分析结果。

        分析过程包括：
        1. 提取需求目标和关键词
        2. 评估需求复杂度
        3. 识别涉及的技术栈
        4. 推荐合适的智能体角色
        5. 估算执行步骤数

        Args:
            user_request: 用户原始需求描述

        Returns:
            RequirementAnalysis 结构化需求分析结果
        """
        request_lower = user_request.lower()

        # 1. 提取关键词
        keywords = self._extract_keywords(user_request)

        # 2. 识别技术栈
        tech_stack = self._detect_tech_stack(request_lower)

        # 3. 估算复杂度
        complexity = self.estimate_complexity(user_request)

        # 4. 推断范围和约束
        scope = self._infer_scope(user_request, complexity)
        constraints = self._infer_constraints(request_lower)
        assumptions = self._infer_assumptions(request_lower)

        # 5. 推荐智能体角色
        suggested_roles = self.suggest_agents_for_request(user_request)

        # 6. 估算步骤数
        estimated_steps = self._estimate_step_count(complexity, suggested_roles)

        analysis = RequirementAnalysis(
            goal=user_request.strip(),
            scope=scope,
            constraints=constraints,
            assumptions=assumptions,
            complexity=complexity,
            suggested_roles=suggested_roles,
            estimated_steps=estimated_steps,
            keywords=keywords,
            tech_stack=tech_stack,
        )

        logger.info(
            "需求分析完成: goal='%s', complexity=%s, steps=%d, roles=%s",
            user_request[:50], complexity.level, estimated_steps,
            [r.value for r in suggested_roles],
        )
        return analysis

    # ------------------------------------------------------------------ #
    #  复杂度评估
    # ------------------------------------------------------------------ #

    def estimate_complexity(self, task_description: str) -> ComplexityScore:
        """估算任务复杂度。

        采用规则引擎 + LLM 双通道策略：
        1. 规则引擎：基于关键词匹配快速评估
        2. LLM 增强：规则评估后若复杂度不确定，调用 LLM 分析

        Args:
            task_description: 任务描述文本

        Returns:
            ComplexityScore 复杂度评分结果
        """
        description_lower = task_description.lower()
        factors: Dict[str, float] = {}
        score = 0.0

        # 1. 关键词权重评分
        keyword_score = 0.0
        for keyword, weight in self._COMPLEXITY_KEYWORDS.items():
            if keyword in description_lower:
                keyword_score += weight
                factors[keyword] = weight

        score += max(0.0, min(1.0, 0.5 + keyword_score)) * 40

        # 2. 多步任务评估
        step_indicators = [
            "然后", "接着", "之后", "同时", "并且", "以及",
            "then", "and", "also", "同时",
        ]
        step_count = sum(
            1 for indicator in step_indicators if indicator in description_lower
        )
        if step_count >= 2:
            step_factor = min(step_count * 0.08, 0.3)
            score += step_factor * 40
            factors["multi_step"] = step_factor

        # 3. 描述长度评估
        desc_len = len(task_description)
        if desc_len > 200:
            length_factor = 0.1
            score += length_factor * 40
            factors["long_description"] = length_factor

        # 4. 句子数量
        sentences = [s for s in re.split(r"[.。!！?？\n]+", task_description) if s.strip()]
        if len(sentences) > 3:
            sentence_factor = min(len(sentences) * 0.03, 0.2)
            score += sentence_factor * 40
            factors["multiple_sentences"] = sentence_factor

        # 5. LLM 增强分析（仅在 DryRun 关闭时）
        llm_score = self._llm_complexity_analysis(task_description)
        if llm_score is not None:
            score = score * 0.4 + llm_score * 0.6
            factors["llm_assessed"] = 0.5

        # 归一化到 0-100
        score = max(0.0, min(100.0, score))
        level = self._score_to_level(score)

        reasoning = self._build_complexity_reasoning(score, level, factors)

        return ComplexityScore(
            score=round(score, 1),
            level=level,
            factors=factors,
            reasoning=reasoning,
        )

    def _llm_complexity_analysis(self, task_description: str) -> Optional[float]:
        """使用 LLM 分析任务复杂度。

        Args:
            task_description: 任务描述

        Returns:
            LLM 评估的复杂度分值 (0-100)，LLM 不可用时返回 None
        """
        if self._llm.dry_run:
            return None

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(content=(
                    "你是一个任务复杂度分析专家。请分析以下任务描述的复杂度，"
                    "给出 0-100 的复杂度评分。评分标准：\n"
                    "- 0-20: 简单（单步任务，无技术挑战）\n"
                    "- 20-50: 中等（多步任务，涉及基础技术）\n"
                    "- 50-80: 复杂（跨领域任务，需要架构设计）\n"
                    "- 80-100: 高级（系统级任务，涉及分布式/安全/高可用）\n\n"
                    "请仅返回一个数字，不要有其他内容。"
                )),
                HumanMessage(content=task_description[:1000]),
            ]

            response = self._llm.chat(messages, task_type="analysis")
            content = response.content.strip()
            numeric = re.findall(r"[\d.]+", content)
            if numeric:
                return float(numeric[0])
            return None
        except Exception as e:
            logger.debug("LLM 复杂度分析失败，回退到规则引擎: %s", e)
            return None

    @staticmethod
    def _score_to_level(score: float) -> str:
        """将数值评分转换为等级。

        Args:
            score: 复杂度分值 (0-100)

        Returns:
            复杂度等级字符串
        """
        if score < 20:
            return "simple"
        elif score < 50:
            return "medium"
        elif score < 80:
            return "complex"
        else:
            return "advanced"

    @staticmethod
    def _build_complexity_reasoning(
        score: float, level: str, factors: Dict[str, float]
    ) -> str:
        """构建复杂度评估的推理说明。

        Args:
            score: 复杂度分值
            level: 复杂度等级
            factors: 影响因素映射

        Returns:
            推理说明文本
        """
        level_desc = {
            "simple": "任务复杂度较低，可由单个智能体快速完成",
            "medium": "任务复杂度中等，需要 2-3 个智能体协作完成",
            "complex": "任务复杂度较高，需要多智能体协作及迭代优化",
            "advanced": "任务复杂度极高，需要全链路智能体协同及严格的质量管控",
        }

        factor_desc = ""
        if factors:
            top_factors = sorted(factors.items(), key=lambda x: -x[1])[:3]
            factor_names = [f for f, _ in top_factors]
            factor_desc = f"主要影响因素: {', '.join(factor_names)}"

        base = level_desc.get(level, "任务复杂度评估")
        return f"{base} (评分: {score:.1f}/100)。{factor_desc}"

    # ------------------------------------------------------------------ #
    #  智能体推荐
    # ------------------------------------------------------------------ #

    def suggest_agents(self, task: TaskTreeNode) -> List[AgentRole]:
        """为单个任务节点推荐最合适的智能体角色。

        基于任务描述、复杂度和关键词综合匹配智能体角色。

        Args:
            task: 任务树节点

        Returns:
            推荐的 AgentRole 列表，按匹配度排序
        """
        return self._match_roles(task.description, task.complexity)

    def suggest_agents_for_request(self, user_request: str) -> List[AgentRole]:
        """为整个用户请求推荐智能体角色。

        Args:
            user_request: 用户原始需求描述

        Returns:
            推荐的 AgentRole 列表，按匹配度排序
        """
        complexity = self.estimate_complexity(user_request)
        return self._match_roles(user_request, complexity)

    def _match_roles(
        self, text: str, complexity: ComplexityScore
    ) -> List[AgentRole]:
        """根据文本和复杂度匹配智能体角色。

        Args:
            text: 匹配文本
            complexity: 复杂度评分

        Returns:
            匹配的 AgentRole 列表
        """
        text_lower = text.lower()
        role_scores: Dict[AgentRole, float] = {}

        for role, keywords in self._ROLE_KEYWORDS.items():
            score = 0.0
            matched = sum(1 for kw in keywords if kw in text_lower)
            score += matched * 10.0

            if matched > 0 and complexity.score >= 50:
                score += 5.0

            if matched > 0:
                role_scores[role] = score

        if not role_scores:
            default_roles = [AgentRole.ANALYST, AgentRole.CODER]
            if complexity.score >= 50:
                default_roles.append(AgentRole.ARCHITECT)
            if complexity.score >= 30:
                default_roles.append(AgentRole.REVIEWER)
            return default_roles

        sorted_roles = sorted(
            role_scores.keys(), key=lambda r: role_scores[r], reverse=True
        )

        if complexity.score >= 60 and AgentRole.ARCHITECT not in sorted_roles[:3]:
            sorted_roles.insert(0, AgentRole.ARCHITECT)

        return sorted_roles[:5]

    # ------------------------------------------------------------------ #
    #  任务树生成
    # ------------------------------------------------------------------ #

    def decompose(
        self,
        requirement: RequirementAnalysis,
        max_depth: int = 3,
    ) -> TaskTree:
        """根据需求分析结果生成层级化任务树。

        采用递归分解策略：
        1. 以需求目标为根节点
        2. 识别需求的关键阶段和子任务
        3. 根据复杂度控制分解深度
        4. 为每个子任务分配智能体类型

        Args:
            requirement: 需求分析结果
            max_depth: 最大分解深度，默认 3 层

        Returns:
            TaskTree 层级任务树
        """
        tree = TaskTree()

        root_id = self._generate_node_id("root")
        root_node = TaskTreeNode(
            node_id=root_id,
            description=requirement.goal,
            agent_type="orchestration",
            agent_role=AgentRole.ORCHESTRATOR,
            complexity=requirement.complexity,
            metadata={
                "goal": requirement.goal,
                "scope": requirement.scope,
                "constraints": requirement.constraints,
                "keywords": requirement.keywords,
            },
        )
        tree.add_node(root_node)

        depth_budget = min(max_depth, self._recommend_depth(requirement.complexity))

        if depth_budget >= 2:
            self._decompose_children(
                tree, root_id, requirement, current_depth=1, max_depth=depth_budget
            )

        self._recompute_all_agent_types(tree)

        logger.info(
            "任务树生成完成: root=%s, total_tasks=%d, depth=%d",
            root_id, tree.total_tasks, tree.depth,
        )
        return tree

    def _decompose_children(
        self,
        tree: TaskTree,
        parent_id: str,
        requirement: RequirementAnalysis,
        current_depth: int,
        max_depth: int,
    ) -> None:
        """递归分解子任务。

        Args:
            tree: 任务树实例
            parent_id: 父节点 ID
            requirement: 原始需求分析
            current_depth: 当前深度
            max_depth: 最大深度
        """
        parent_node = tree.get_node(parent_id)
        child_tasks = self._generate_child_tasks(parent_node, requirement)

        for task_desc, agent_type in child_tasks:
            child_id = self._generate_node_id("task")
            child_complexity = self.estimate_complexity(task_desc)
            child_role = self._map_agent_type_to_role(agent_type)

            child_node = TaskTreeNode(
                node_id=child_id,
                description=task_desc,
                agent_type=agent_type,
                agent_role=child_role,
                complexity=child_complexity,
            )
            tree.add_node(child_node, parent_id=parent_id)

            if current_depth < max_depth and child_complexity.score >= 30:
                sub_tasks = self._generate_sub_tasks(task_desc, agent_type, requirement)
                for sub_desc, sub_type in sub_tasks:
                    sub_id = self._generate_node_id("subtask")
                    sub_complexity = self.estimate_complexity(sub_desc)
                    sub_role = self._map_agent_type_to_role(sub_type)

                    sub_node = TaskTreeNode(
                        node_id=sub_id,
                        description=sub_desc,
                        agent_type=sub_type,
                        agent_role=sub_role,
                        complexity=sub_complexity,
                    )
                    tree.add_node(sub_node, parent_id=child_id)

    def _generate_child_tasks(
        self,
        parent: TaskTreeNode,
        requirement: RequirementAnalysis,
    ) -> List[Tuple[str, str]]:
        """为父节点生成子任务列表。

        根据父节点的 agent_type 和需求特征，生成合适的子任务。

        Args:
            parent: 父节点
            requirement: 需求分析结果

        Returns:
            (任务描述, 智能体类型) 元组列表
        """
        parent_desc_lower = parent.description.lower()
        tasks: List[Tuple[str, str]] = []

        if parent.agent_type == "orchestration":
            tasks.append(("需求分析与确认", "requirement"))
            tasks.append(("系统设计与架构规划", "architecture"))
            tasks.append(("代码实现与编码", "code_gen"))
            tasks.append(("代码审查与质量保证", "review"))
            tasks.append(("测试验证与用例生成", "test_gen"))

            if requirement.complexity.score >= 50:
                tasks.append(("部署与运维配置", "deploy"))
                tasks.append(("文档与交付物生成", "requirement"))

        elif parent.agent_type == "requirement":
            tasks.append(("需求调研与数据收集", "requirement"))
            tasks.append(("需求分析与文档编写", "requirement"))
            tasks.append(("验收标准定义", "requirement"))

        elif parent.agent_type == "architecture":
            tasks.append(("技术选型与框架选择", "architecture"))
            tasks.append(("模块划分与接口设计", "architecture"))
            tasks.append(("数据模型设计", "architecture"))

        elif parent.agent_type == "code_gen":
            tasks.append(("核心逻辑实现", "code_gen"))
            tasks.append(("接口与服务实现", "code_gen"))
            tasks.append(("数据访问层实现", "code_gen"))

        elif parent.agent_type == "review":
            tasks.append(("代码规范检查", "review"))
            tasks.append(("逻辑正确性验证", "review"))
            tasks.append(("安全漏洞扫描", "review"))

        elif parent.agent_type == "test_gen":
            tasks.append(("单元测试用例生成", "test_gen"))
            tasks.append(("集成测试用例生成", "test_gen"))
            tasks.append(("边界条件测试", "test_gen"))

        elif parent.agent_type == "deploy":
            tasks.append(("构建与打包", "deploy"))
            tasks.append(("部署脚本编写", "deploy"))
            tasks.append(("监控与告警配置", "deploy"))

        else:
            tasks.append((f"执行: {parent.description[:80]}", "general"))

        return tasks

    def _generate_sub_tasks(
        self,
        task_desc: str,
        agent_type: str,
        requirement: RequirementAnalysis,
    ) -> List[Tuple[str, str]]:
        """为中等以上复杂度任务生成更细粒度的子任务。

        Args:
            task_desc: 父任务描述
            agent_type: 父任务智能体类型
            requirement: 原始需求分析

        Returns:
            (子任务描述, 智能体类型) 元组列表
        """
        tasks: List[Tuple[str, str]] = []

        if agent_type == "code_gen":
            tasks.append((f"实现 {task_desc} 的数据模型", "code_gen"))
            tasks.append((f"实现 {task_desc} 的业务逻辑", "code_gen"))
            tasks.append((f"实现 {task_desc} 的接口层", "code_gen"))

        elif agent_type == "architecture":
            tasks.append((f"分析 {task_desc} 的技术需求", "architecture"))
            tasks.append((f"设计 {task_desc} 的模块结构", "architecture"))
            tasks.append((f"定义 {task_desc} 的接口契约", "architecture"))

        elif agent_type == "test_gen":
            tasks.append((f"编写 {task_desc} 的测试用例", "test_gen"))
            tasks.append((f"执行 {task_desc} 的测试并报告", "test_gen"))

        elif agent_type == "review":
            tasks.append((f"检查 {task_desc} 的代码规范", "review"))
            tasks.append((f"验证 {task_desc} 的逻辑正确性", "review"))

        return tasks

    def _recommend_depth(self, complexity: ComplexityScore) -> int:
        """根据复杂度推荐分解深度。

        Args:
            complexity: 复杂度评分

        Returns:
            推荐的分解深度 (1-3)
        """
        if complexity.score < 20:
            return 1
        elif complexity.score < 50:
            return 2
        elif complexity.score < 80:
            return 3
        else:
            return 3

    def _recompute_all_agent_types(self, tree: TaskTree) -> None:
        """重新计算所有节点的 agent_type 和 agent_role。

        对于未指定 agent_type 的节点，根据关键词自动推断。

        Args:
            tree: 任务树实例
        """
        for node in tree.nodes.values():
            if node.agent_type == "general" or node.agent_role is None:
                roles = self._match_roles(node.description, node.complexity)
                if roles:
                    node.agent_role = roles[0]
                    node.agent_type = self._role_to_agent_type(roles[0])

    @staticmethod
    def _map_agent_type_to_role(agent_type: str) -> AgentRole:
        """将 agent_type 字符串映射为 AgentRole 枚举。

        Args:
            agent_type: 智能体类型字符串

        Returns:
            对应的 AgentRole 枚举值
        """
        mapping: Dict[str, AgentRole] = {
            "requirement": AgentRole.ANALYST,
            "architecture": AgentRole.ARCHITECT,
            "code_gen": AgentRole.CODER,
            "review": AgentRole.REVIEWER,
            "test_gen": AgentRole.TESTER,
            "deploy": AgentRole.DEPLOYER,
            "orchestration": AgentRole.ORCHESTRATOR,
            "general": AgentRole.CODER,
        }
        return mapping.get(agent_type, AgentRole.CODER)

    @staticmethod
    def _role_to_agent_type(role: AgentRole) -> str:
        """将 AgentRole 枚举映射为 agent_type 字符串。

        Args:
            role: 智能体角色

        Returns:
            对应的智能体类型字符串
        """
        mapping: Dict[AgentRole, str] = {
            AgentRole.ANALYST: "requirement",
            AgentRole.ARCHITECT: "architecture",
            AgentRole.CODER: "code_gen",
            AgentRole.REVIEWER: "review",
            AgentRole.TESTER: "test_gen",
            AgentRole.DEPLOYER: "deploy",
            AgentRole.ORCHESTRATOR: "orchestration",
        }
        return mapping.get(role, "general")

    # ------------------------------------------------------------------ #
    #  依赖图生成
    # ------------------------------------------------------------------ #

    def generate_dependencies(
        self, tasks: List[TaskTreeNode]
    ) -> "TaskGraph":
        """根据任务列表生成依赖关系图。

        基于任务描述和智能体类型，自动推断任务间的依赖关系：
        - 需求分析类任务 → 其他所有任务的前置依赖
        - 架构设计类任务 → 编码/测试任务的前置依赖
        - 编码任务 → 评审/测试任务的前置依赖
        - 评审任务 → 测试任务的前置依赖
        - 独立的编码任务之间尽可能并行

        Args:
            tasks: 任务节点列表

        Returns:
            TaskGraph 依赖图（兼容 orchestrator.TaskGraph）
        """
        task_map: Dict[str, OrchAgentTask] = {}
        type_groups: Dict[str, List[str]] = defaultdict(list)

        for task in tasks:
            orch_task = OrchAgentTask(
                task_id=task.node_id,
                description=task.description,
                agent_type=task.agent_type,
                dependencies=[],
                priority=self._calc_priority(task),
            )
            task_map[task.node_id] = orch_task
            type_groups[task.agent_type].append(task.node_id)

        dependency_map = self._infer_dependencies(type_groups, task_map)

        graph = TaskGraph()
        for task_id, orch_task in task_map.items():
            orch_task.dependencies = dependency_map.get(task_id, [])
            try:
                graph.add_task(task_id, orch_task)
            except (ValueError, Exception) as exc:
                logger.warning(
                    "添加任务 %s 到依赖图失败: %s", task_id, exc
                )

        logger.info(
            "依赖图生成完成: %d tasks, %d edges",
            graph.task_count, len(graph._adj),
        )
        return graph

    def _infer_dependencies(
        self,
        type_groups: Dict[str, List[str]],
        task_map: Dict[str, "OrchAgentTask"],
    ) -> Dict[str, List[str]]:
        """推断任务间的依赖关系。

        Args:
            type_groups: 按智能体类型分组的任务 ID
            task_map: 任务 ID → OrchAgentTask 映射

        Returns:
            任务 ID → 依赖任务 ID 列表的映射
        """
        dependency_map: Dict[str, List[str]] = {}

        requirement_ids = type_groups.get("requirement", [])
        architecture_ids = type_groups.get("architecture", [])
        code_ids = type_groups.get("code_gen", [])
        review_ids = type_groups.get("review", [])
        test_ids = type_groups.get("test_gen", [])
        deploy_ids = type_groups.get("deploy", [])

        for task_id in task_map:
            dependencies: List[str] = []
            agent_type = task_map[task_id].agent_type

            if agent_type == "requirement":
                pass
            elif agent_type == "architecture":
                dependencies.extend(requirement_ids)
            elif agent_type == "code_gen":
                dependencies.extend(requirement_ids)
                dependencies.extend(architecture_ids)
            elif agent_type == "review":
                dependencies.extend(code_ids)
            elif agent_type == "test_gen":
                dependencies.extend(code_ids)
                dependencies.extend(review_ids)
            elif agent_type == "deploy":
                dependencies.extend(code_ids)
                dependencies.extend(test_ids)
            else:
                dependencies.extend(requirement_ids)

            dependency_map[task_id] = list(dict.fromkeys(dependencies))

        return dependency_map

    @staticmethod
    def _calc_priority(task: TaskTreeNode) -> int:
        """计算任务优先级。

        Args:
            task: 任务节点

        Returns:
            优先级数值（越大越优先）
        """
        base_map: Dict[str, int] = {
            "requirement": 100,
            "architecture": 95,
            "code_gen": 90,
            "review": 80,
            "test_gen": 70,
            "deploy": 60,
            "orchestration": 100,
            "general": 50,
        }
        base = base_map.get(task.agent_type, 50)
        complexity_bonus = int(task.complexity.score * 0.3)
        return base + complexity_bonus

    # ------------------------------------------------------------------ #
    #  辅助方法
    # ------------------------------------------------------------------ #

    @staticmethod
    def _generate_node_id(prefix: str = "task") -> str:
        """生成唯一节点 ID。

        Args:
            prefix: ID 前缀

        Returns:
            唯一 ID 字符串
        """
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """从文本中提取关键词。

        Args:
            text: 输入文本

        Returns:
            关键词列表
        """
        keywords: List[str] = []

        for tech, aliases in TaskDecomposer._TECH_KEYWORDS.items():
            if any(alias.lower() in text.lower() for alias in aliases):
                keywords.append(tech)

        role_indicators = [
            "需要", "应该", "必须", "实现", "设计", "开发",
            "测试", "部署", "审查", "优化", "重构",
        ]
        for indicator in role_indicators:
            if indicator in text:
                keywords.append(indicator)

        return list(dict.fromkeys(keywords))

    @staticmethod
    def _detect_tech_stack(text_lower: str) -> List[str]:
        """检测涉及的技术栈。

        Args:
            text_lower: 小写的需求文本

        Returns:
            技术栈列表
        """
        tech_stack: List[str] = []
        for tech, aliases in TaskDecomposer._TECH_KEYWORDS.items():
            if any(alias in text_lower for alias in aliases):
                tech_stack.append(tech)
        return tech_stack

    @staticmethod
    def _infer_scope(
        user_request: str, complexity: ComplexityScore
    ) -> List[str]:
        """推断需求范围。

        Args:
            user_request: 用户需求文本
            complexity: 复杂度评分

        Returns:
            需求范围列表
        """
        scope: List[str] = []

        if complexity.score < 20:
            scope.append("核心功能实现")
        elif complexity.score < 50:
            scope.append("核心功能实现")
            scope.append("基础接口设计")
        elif complexity.score < 80:
            scope.append("核心功能实现")
            scope.append("接口与服务设计")
            scope.append("数据模型设计")
            scope.append("基础测试覆盖")
        else:
            scope.append("核心功能实现")
            scope.append("接口与服务设计")
            scope.append("数据模型设计")
            scope.append("完整测试覆盖")
            scope.append("部署与运维方案")
            scope.append("安全与性能优化")

        return scope

    @staticmethod
    def _infer_constraints(text_lower: str) -> List[str]:
        """推断约束条件。

        Args:
            text_lower: 小写的需求文本

        Returns:
            约束条件列表
        """
        constraints: List[str] = []

        if any(kw in text_lower for kw in ("api", "接口", "rest")):
            constraints.append("需提供 RESTful API 接口")
        if any(kw in text_lower for kw in ("secure", "安全", "auth")):
            constraints.append("需满足安全认证要求")
        if any(kw in text_lower for kw in ("fast", "快速", "性能")):
            constraints.append("需满足性能要求")
        if any(kw in text_lower for kw in ("test", "测试")):
            constraints.append("需包含测试用例")

        if not constraints:
            constraints.append("遵循现有代码规范")
            constraints.append("保证代码可读性与可维护性")

        return constraints

    @staticmethod
    def _infer_assumptions(text_lower: str) -> List[str]:
        """推断假设前提。

        Args:
            text_lower: 小写的需求文本

        Returns:
            假设前提列表
        """
        assumptions: List[str] = [
            "开发环境已配置完成",
            "相关依赖库可正常获取",
        ]

        if any(kw in text_lower for kw in ("database", "数据库", "sql")):
            assumptions.append("数据库连接已配置")
        if any(kw in text_lower for kw in ("api", "接口", "service")):
            assumptions.append("接口协议已确定")

        return assumptions

    @staticmethod
    def _estimate_step_count(
        complexity: ComplexityScore, roles: List[AgentRole]
    ) -> int:
        """估算执行步骤数。

        Args:
            complexity: 复杂度评分
            roles: 推荐的智能体角色

        Returns:
            预估步骤数
        """
        base = max(3, len(roles))
        if complexity.score < 20:
            return base
        elif complexity.score < 50:
            return base + 2
        elif complexity.score < 80:
            return base + 5
        else:
            return base + 8


# ====================================================================== #
#  自主决策引擎
# ====================================================================== #


class DecisionEngine:
    """自主决策引擎，负责策略选择、方案评估与反馈调整。

    核心能力：
    1. **策略选择** — 根据上下文选择最合适的执行策略
    2. **方案评估** — 对多个备选方案进行综合评估与排序
    3. **约束应用** — 在执行计划上叠加约束条件并调整
    4. **反馈调整** — 根据执行反馈动态调整计划

    Usage::

        engine = DecisionEngine()
        strategy = engine.select_strategy({"complexity": "high", "task_type": "deployment"})
        result = engine.evaluate_alternatives([option_a, option_b])
    """

    _STRATEGY_RULES: List[Tuple[str, Strategy, int]] = [
        # (context_keyword, strategy, priority)
        ("parallel", Strategy.PARALLEL, 100),
        ("并行", Strategy.PARALLEL, 100),
        ("concurrent", Strategy.PARALLEL, 90),
        ("迭代", Strategy.ITERATIVE, 95),
        ("iterative", Strategy.ITERATIVE, 95),
        ("review", Strategy.ITERATIVE, 80),
        ("评审", Strategy.ITERATIVE, 80),
        ("pipeline", Strategy.PIPELINE, 85),
        ("流水线", Strategy.PIPELINE, 85),
        ("sequential", Strategy.SEQUENTIAL, 70),
        ("顺序", Strategy.SEQUENTIAL, 70),
        ("step", Strategy.SEQUENTIAL, 65),
        ("simple", Strategy.SEQUENTIAL, 60),
        ("hybrid", Strategy.HYBRID, 50),
        ("混合", Strategy.HYBRID, 50),
    ]

    def __init__(self) -> None:
        self._llm = llm

    # ------------------------------------------------------------------ #
    #  策略选择
    # ------------------------------------------------------------------ #

    def select_strategy(self, context: Dict[str, Any]) -> Strategy:
        """根据上下文选择执行策略。

        综合考虑以下因素：
        - 任务复杂度
        - 任务间依赖关系
        - 可用的智能体数量
        - 时间约束
        - 质量要求

        Args:
            context: 策略选择上下文，支持以下字段：
                - complexity: 复杂度等级 (simple/medium/complex/advanced)
                - task_type: 任务类型
                - agent_count: 可用智能体数量
                - time_constraint: 时间约束 (seconds)
                - quality_requirement: 质量要求 (low/medium/high)
                - keywords: 额外关键词列表

        Returns:
            选定的 Strategy 枚举值
        """
        context_lower = str(context).lower()

        for keyword, strategy, _priority in self._STRATEGY_RULES:
            if keyword in context_lower:
                logger.info(
                    "策略选择: 基于关键词 '%s' 选择 %s 策略",
                    keyword, strategy.value,
                )
                return strategy

        complexity = context.get("complexity", "medium")
        agent_count = int(context.get("agent_count", 1))
        quality = context.get("quality_requirement", "medium")

        if complexity == "simple" or complexity == "low":
            return Strategy.SEQUENTIAL

        if complexity == "advanced" and quality == "high":
            return Strategy.ITERATIVE

        if agent_count >= 3 and complexity in ("complex", "advanced"):
            return Strategy.HYBRID

        if agent_count >= 2:
            return Strategy.PARALLEL

        if quality == "high":
            return Strategy.PIPELINE

        return Strategy.SEQUENTIAL

    # ------------------------------------------------------------------ #
    #  方案评估
    # ------------------------------------------------------------------ #

    def evaluate_alternatives(self, options: List[Option]) -> DecisionResult:
        """评估多个备选方案并返回最优结果。

        采用多维度评分方法：
        - 可行性评分
        - 成本效益评分
        - 风险评估
        - 与目标的对齐度

        Args:
            options: 备选方案列表（至少 2 个）

        Returns:
            DecisionResult 决策结果，包含最优方案及所有方案评分

        Raises:
            ValueError: 备选方案数量不足
        """
        if len(options) < 2:
            raise ValueError("至少需要 2 个备选方案才能进行评估")

        scored_options: List[Tuple[Option, float, Dict[str, float]]] = []

        for option in options:
            dimension_scores = self._score_dimensions(option)
            overall_score = sum(dimension_scores.values()) / len(dimension_scores)
            scored_options.append((option, overall_score, dimension_scores))

        scored_options.sort(key=lambda x: x[1], reverse=True)

        selected = scored_options[0][0]
        scores = {opt.name: score for opt, score, _ in scored_options}
        reasoning = self._build_evaluation_reasoning(
            scored_options[0], scored_options[1] if len(scored_options) > 1 else None
        )
        alternatives = [opt for opt, _, _ in scored_options[1:]]

        result = DecisionResult(
            selected=selected,
            scores=scores,
            reasoning=reasoning,
            alternatives=alternatives,
        )

        logger.info(
            "方案评估完成: selected='%s', score=%.1f, reasoning='%s'",
            selected.name, scored_options[0][1], reasoning[:80],
        )
        return result

    def _score_dimensions(self, option: Option) -> Dict[str, float]:
        """从多个维度评估方案。

        Args:
            option: 备选方案

        Returns:
            各维度得分映射
        """
        scores: Dict[str, float] = {}

        # 1. 成本效益比
        pros_count = len(option.pros)
        cons_count = len(option.cons)
        total = pros_count + cons_count
        if total > 0:
            scores["cost_benefit"] = (pros_count / total) * 100
        else:
            scores["cost_benefit"] = 50.0

        # 2. 方案自评分
        scores["self_score"] = option.score

        # 3. 描述完整度
        desc_len = len(option.description)
        if desc_len >= 100:
            scores["completeness"] = 90.0
        elif desc_len >= 50:
            scores["completeness"] = 70.0
        elif desc_len >= 20:
            scores["completeness"] = 50.0
        else:
            scores["completeness"] = 30.0

        # 4. 风险评估（缺点越多风险越高，得分越低）
        risk_penalty = min(cons_count * 5.0, 40.0)
        scores["risk"] = max(0.0, 100.0 - risk_penalty)

        return scores

    @staticmethod
    def _build_evaluation_reasoning(
        winner: Tuple[Option, float, Dict[str, float]],
        runner_up: Optional[Tuple[Option, float, Dict[str, float]]],
    ) -> str:
        """构建评估推理说明。

        Args:
            winner: 最优方案及其得分
            runner_up: 次优方案及其得分（可选）

        Returns:
            推理说明文本
        """
        winner_option, winner_score, dimensions = winner
        dim_names = {
            "cost_benefit": "成本效益",
            "self_score": "方案质量",
            "completeness": "完整度",
            "risk": "低风险性",
        }

        strengths = [
            dim_names.get(d, d)
            for d, s in dimensions.items()
            if s >= 70
        ]

        reasoning = f"方案 '{winner_option.name}' 综合得分 {winner_score:.1f}/100"
        if strengths:
            reasoning += f"，在 {', '.join(strengths)} 方面表现突出"

        if runner_up:
            run_option, run_score, _ = runner_up
            diff = winner_score - run_score
            reasoning += f"，优于备选方案 '{run_option.name}' (差距 {diff:.1f} 分)"

        return reasoning

    # ------------------------------------------------------------------ #
    #  约束应用
    # ------------------------------------------------------------------ #

    def apply_constraints(
        self, plan: "ExecutionPlan", constraints: Dict[str, Any]
    ) -> "ExecutionPlan":
        """在执行计划上叠加约束条件并进行相应调整。

        支持的约束类型：
        - max_parallel: 最大并行度限制
        - timeout: 全局超时时间
        - priority_order: 指定任务优先级顺序
        - resource_limit: 资源限制 (CPU/内存)
        - skip_tasks: 跳过指定任务
        - require_approval: 需要人工审批的任务列表

        Args:
            plan: 原始执行计划
            constraints: 约束条件字典

        Returns:
            调整后的 ExecutionPlan 实例
        """
        from app.graph.orchestrator import ExecutionPlan as OrchExecutionPlan

        adjusted = OrchExecutionPlan(
            execution_order=[group[:] for group in plan.execution_order],
            estimated_duration=plan.estimated_duration,
            dependencies_map=dict(plan.dependencies_map),
            fallback_strategies=dict(plan.fallback_strategies),
            critical_path=list(plan.critical_path),
        )

        if "max_parallel" in constraints:
            max_parallel = int(constraints["max_parallel"])
            adjusted.execution_order = [
                group[:max_parallel] for group in adjusted.execution_order
            ]
            logger.info(
                "应用约束: max_parallel=%d, 调整后 %d 组",
                max_parallel, len(adjusted.execution_order),
            )

        if "timeout" in constraints:
            timeout = float(constraints["timeout"])
            adjusted.estimated_duration = min(
                adjusted.estimated_duration, timeout
            )
            logger.info("应用约束: timeout=%.0fs", timeout)

        if "skip_tasks" in constraints:
            skip_set = set(constraints["skip_tasks"])
            adjusted.execution_order = [
                [t for t in group if t not in skip_set]
                for group in adjusted.execution_order
            ]
            adjusted.execution_order = [
                g for g in adjusted.execution_order if g
            ]
            logger.info("应用约束: skip %d tasks", len(skip_set))

        if "priority_order" in constraints:
            priority_list = list(constraints["priority_order"])
            priority_map = {tid: idx for idx, tid in enumerate(priority_list)}
            for group in adjusted.execution_order:
                group.sort(key=lambda tid: priority_map.get(tid, 999))
            logger.info("应用约束: priority_order 已排序")

        if "require_approval" in constraints:
            approval_set = set(constraints["require_approval"])
            for task_id in approval_set:
                if task_id in adjusted.fallback_strategies:
                    from app.graph.orchestrator import RecoveryStrategy
                    adjusted.fallback_strategies[task_id] = RecoveryStrategy.ESCALATE
            logger.info(
                "应用约束: %d tasks require approval", len(approval_set)
            )

        return adjusted

    # ------------------------------------------------------------------ #
    #  反馈调整
    # ------------------------------------------------------------------ #

    def adapt_on_feedback(
        self, plan: "ExecutionPlan", feedback: Dict[str, Any]
    ) -> "ExecutionPlan":
        """根据执行反馈动态调整执行计划。

        支持的反馈类型：
        - failed_tasks: 失败的任务列表 → 触发恢复策略
        - performance_metrics: 性能指标 → 调整并行度
        - quality_issues: 质量问题 → 增加评审/重试环节
        - user_corrections: 用户修正 → 调整优先级或跳过任务

        Args:
            plan: 当前执行计划
            feedback: 反馈信息字典

        Returns:
            调整后的 ExecutionPlan 实例
        """
        from app.graph.orchestrator import ExecutionPlan as OrchExecutionPlan
        from app.graph.orchestrator import RecoveryStrategy

        adapted = OrchExecutionPlan(
            execution_order=[group[:] for group in plan.execution_order],
            estimated_duration=plan.estimated_duration,
            dependencies_map=dict(plan.dependencies_map),
            fallback_strategies=dict(plan.fallback_strategies),
            critical_path=list(plan.critical_path),
        )

        if "failed_tasks" in feedback:
            failed_ids = set(feedback["failed_tasks"])
            for task_id in failed_ids:
                if task_id in adapted.fallback_strategies:
                    adapted.fallback_strategies[task_id] = RecoveryStrategy.RETRY
                for group in adapted.execution_order:
                    if task_id in group:
                        group.remove(task_id)
                        logger.info(
                            "反馈调整: 移除失败任务 %s", task_id
                        )

        if "quality_issues" in feedback:
            issue_tasks = set(feedback["quality_issues"])
            for task_id in issue_tasks:
                if task_id not in adapted.fallback_strategies:
                    adapted.fallback_strategies[task_id] = RecoveryStrategy.RETRY

        if "user_corrections" in feedback:
            corrections = feedback["user_corrections"]
            if isinstance(corrections, dict):
                for task_id, action in corrections.items():
                    if action == "skip":
                        for group in adapted.execution_order:
                            if task_id in group:
                                group.remove(task_id)
                    elif action == "prioritize":
                        for group in adapted.execution_order:
                            if task_id in group:
                                group.remove(task_id)
                                adapted.execution_order[0].insert(0, task_id)

        if "performance_metrics" in feedback:
            metrics = feedback["performance_metrics"]
            if metrics.get("avg_duration", 0) > 60:
                adapted.estimated_duration *= 1.2
                logger.info("反馈调整: 预估耗时增加 20%")

        return adapted


# ====================================================================== #
#  便捷工厂函数
# ====================================================================== #


def create_decomposer() -> TaskDecomposer:
    """创建 TaskDecomposer 实例的便捷方法。

    Returns:
        配置好的 TaskDecomposer 实例
    """
    return TaskDecomposer()


def create_decision_engine() -> DecisionEngine:
    """创建 DecisionEngine 实例的便捷方法。

    Returns:
        配置好的 DecisionEngine 实例
    """
    return DecisionEngine()


def quick_decompose(
    user_request: str,
    max_depth: int = 3,
) -> TaskTree:
    """一站式快速分解：从用户需求直接生成任务树。

    Args:
        user_request: 用户原始需求
        max_depth: 最大分解深度

    Returns:
        TaskTree 任务树
    """
    decomposer = TaskDecomposer()
    analysis = decomposer.analyze_request(user_request)
    return decomposer.decompose(analysis, max_depth=max_depth)


__all__ = [
    # 数据类
    "ComplexityScore",
    "RequirementAnalysis",
    "TaskTreeNode",
    "TaskTree",
    "Option",
    "DecisionResult",
    # 枚举
    "Strategy",
    # 核心类
    "TaskDecomposer",
    "DecisionEngine",
    # 便捷函数
    "create_decomposer",
    "create_decision_engine",
    "quick_decompose",
]

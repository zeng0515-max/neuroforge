"""任务拆解与决策引擎单元测试。

覆盖:
- ComplexityScore 复杂度评估（关键词评分、等级映射）
- TaskTree 任务树生成（节点操作、层级结构、扁平化）
- DecisionEngine 策略选择与方案评估
- TaskDecomposer 端到端需求分析→拆解
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agents.agent_protocol import AgentRole
from app.graph.task_decomposer import (
    ComplexityScore,
    DecisionEngine,
    DecisionResult,
    Option,
    RequirementAnalysis,
    Strategy,
    TaskDecomposer,
    TaskTree,
    TaskTreeNode,
)


# ====================================================================== #
#  ComplexityScore —— 复杂度评估
# ====================================================================== #


class TestComplexityScore:
    """复杂度评分与等级映射。"""

    def test_score_to_level_simple(self):
        """评分 < 20 应为 simple。"""
        level = TaskDecomposer._score_to_level(10)
        assert level == "simple"

    def test_score_to_level_medium(self):
        """20 <= 评分 < 50 应为 medium。"""
        assert TaskDecomposer._score_to_level(25) == "medium"
        assert TaskDecomposer._score_to_level(45) == "medium"

    def test_score_to_level_complex(self):
        """50 <= 评分 < 80 应为 complex。"""
        assert TaskDecomposer._score_to_level(55) == "complex"
        assert TaskDecomposer._score_to_level(75) == "complex"

    def test_score_to_level_advanced(self):
        """评分 >= 80 应为 advanced。"""
        assert TaskDecomposer._score_to_level(85) == "advanced"
        assert TaskDecomposer._score_to_level(100) == "advanced"

    def test_complexity_level_label(self):
        """ComplexityScore.level_label 应返回中文。"""
        score = ComplexityScore(score=10, level="simple")
        assert score.level_label == "简单"
        score = ComplexityScore(score=90, level="advanced")
        assert score.level_label == "高级"

    def test_estimate_simple_task(self):
        """简单任务应得到较低复杂度评分。"""
        decomposer = TaskDecomposer()
        score = decomposer.estimate_complexity("写一个简单的加法函数")
        assert score.score < 50
        assert score.level in ("simple", "medium")

    def test_estimate_complex_task(self):
        """包含重构/优化关键词的任务应得到较高评分。"""
        decomposer = TaskDecomposer()
        score = decomposer.estimate_complexity("重构现有系统架构并优化性能")
        assert score.score >= 40
        assert score.level in ("medium", "complex", "advanced")
        assert "refactor" in score.factors or "重构" in score.factors

    def test_estimate_api_task(self):
        """包含 API/接口关键词的任务应识别技术栈。"""
        decomposer = TaskDecomposer()
        score = decomposer.estimate_complexity("设计 REST API 接口服务")
        assert score.score > 0

    def test_complexity_reasoning_not_empty(self):
        """推理说明非空。"""
        decomposer = TaskDecomposer()
        score = decomposer.estimate_complexity("实现一个用户登录功能")
        assert score.reasoning != ""
        assert len(score.reasoning) > 10


# ====================================================================== #
#  TaskTree —— 任务树生成
# ====================================================================== #


class TestTaskTree:
    """TaskTree 节点操作与层级结构。"""

    def test_add_root_node(self):
        """添加根节点。"""
        tree = TaskTree()
        node = TaskTreeNode(node_id="root", description="根任务")
        tree.add_node(node)
        assert tree.root == "root"
        assert tree.total_tasks == 1
        assert tree.depth == 1

    def test_add_child_node(self):
        """添加子节点。"""
        tree = TaskTree()
        root = TaskTreeNode(node_id="root", description="根")
        child = TaskTreeNode(node_id="child", description="子")
        tree.add_node(root)
        tree.add_node(child, parent_id="root")
        assert tree.total_tasks == 2
        assert tree.get_node("root").children == ["child"]
        assert tree.depth == 2

    def test_add_duplicate_node_raises(self):
        """重复节点 ID 应抛 ValueError。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="a"))
        with pytest.raises(ValueError, match="已存在"):
            tree.add_node(TaskTreeNode(node_id="a"))

    def test_add_node_invalid_parent_raises(self):
        """父节点不存在应抛 ValueError。"""
        tree = TaskTree()
        with pytest.raises(ValueError, match="父节点"):
            tree.add_node(TaskTreeNode(node_id="c"), parent_id="nonexistent")

    def test_remove_node(self):
        """移除节点及其子节点。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="root"))
        tree.add_node(TaskTreeNode(node_id="child1"), parent_id="root")
        tree.add_node(TaskTreeNode(node_id="grandchild"), parent_id="child1")
        removed = tree.remove_node("child1")
        assert removed.node_id == "child1"
        assert tree.total_tasks == 1
        assert "grandchild" not in tree.nodes

    def test_remove_root_raises(self):
        """移除根节点应抛 ValueError。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="root"))
        with pytest.raises(ValueError, match="root"):
            tree.remove_node("root")

    def test_get_node(self):
        """获取指定节点。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="a", description="test"))
        node = tree.get_node("a")
        assert node.description == "test"

    def test_get_node_nonexistent_raises(self):
        """获取不存在的节点应抛 KeyError。"""
        tree = TaskTree()
        with pytest.raises(KeyError):
            tree.get_node("nope")

    def test_get_leaf_tasks(self):
        """获取所有叶子节点。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="root"))
        tree.add_node(TaskTreeNode(node_id="c1"), parent_id="root")
        tree.add_node(TaskTreeNode(node_id="c2"), parent_id="root")
        leaves = tree.get_leaf_tasks()
        assert len(leaves) == 2
        assert all(n.is_leaf() for n in leaves)

    def test_flatten_bfs_order(self):
        """扁平化应按 BFS 顺序。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="root"))
        tree.add_node(TaskTreeNode(node_id="c1"), parent_id="root")
        tree.add_node(TaskTreeNode(node_id="c2"), parent_id="root")
        tree.add_node(TaskTreeNode(node_id="gc1"), parent_id="c1")
        flat = tree.flatten()
        assert flat[0].node_id == "root"
        assert flat[1].node_id in ("c1", "c2")
        assert flat[2].node_id in ("c1", "c2")

    def test_critical_path(self):
        """关键路径应为最长路径。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="root"))
        tree.add_node(TaskTreeNode(node_id="a"), parent_id="root")
        tree.add_node(TaskTreeNode(node_id="b"), parent_id="a")
        tree.add_node(TaskTreeNode(node_id="c"), parent_id="b")
        tree.add_node(TaskTreeNode(node_id="short"), parent_id="root")
        path = tree.get_critical_path()
        assert len(path) == 4
        assert path[0] == "root"

    def test_to_dict(self):
        """序列化为字典。"""
        tree = TaskTree()
        tree.add_node(TaskTreeNode(node_id="root", description="test"))
        d = tree.to_dict()
        assert "root" in d
        assert "nodes" in d
        assert d["total_tasks"] == 1

    def test_node_is_leaf(self):
        """is_leaf 判断。"""
        node = TaskTreeNode(node_id="a")
        assert node.is_leaf() is True
        node.children.append("b")
        assert node.is_leaf() is False


# ====================================================================== #
#  DecisionEngine —— 决策引擎
# ====================================================================== #


class TestDecisionEngine:
    """决策引擎策略选择与方案评估。"""

    @pytest.fixture
    def engine(self):
        return DecisionEngine()

    def test_select_strategy_parallel(self, engine):
        """含'parallel'关键词应选 PARALLEL。"""
        strategy = engine.select_strategy({"keywords": ["parallel"]})
        assert strategy == Strategy.PARALLEL

    def test_select_strategy_sequential(self, engine):
        """含'simple'关键词应选 SEQUENTIAL。"""
        strategy = engine.select_strategy({"keywords": ["simple"]})
        assert strategy == Strategy.SEQUENTIAL

    def test_select_strategy_iterative(self, engine):
        """含'review'关键词应选 ITERATIVE。"""
        strategy = engine.select_strategy({"keywords": ["review"]})
        assert strategy == Strategy.ITERATIVE

    def test_select_strategy_pipeline(self, engine):
        """含'pipeline'关键词应选 PIPELINE。"""
        strategy = engine.select_strategy({"keywords": ["pipeline"]})
        assert strategy == Strategy.PIPELINE

    def test_select_strategy_by_complexity_high(self, engine):
        """高复杂度且多智能体时应倾向并行或混合策略。"""
        strategy = engine.select_strategy({"complexity": "high", "agent_count": 3})
        assert strategy in (Strategy.PARALLEL, Strategy.HYBRID, Strategy.PIPELINE)

    def test_strategy_descriptions(self):
        """策略描述应非空。"""
        for s in Strategy:
            assert s.description != ""

    def test_evaluate_alternatives(self, engine):
        """方案评估应返回 DecisionResult。"""
        options = [
            Option(name="A", description="方案A", score=80),
            Option(name="B", description="方案B", score=60),
            Option(name="C", description="方案C", score=90),
        ]
        result = engine.evaluate_alternatives(options)
        assert isinstance(result, DecisionResult)
        assert result.selected.name == "C"
        assert result.scores["C"] > result.scores["A"] > result.scores["B"]

    def test_evaluate_alternatives_empty_list(self, engine):
        """空方案列表应抛 ValueError。"""
        with pytest.raises(ValueError):
            engine.evaluate_alternatives([])

    def test_select_strategy_no_match(self, engine):
        """无匹配关键词时应有默认策略。"""
        strategy = engine.select_strategy({"keywords": ["unknown_xyz"]})
        assert isinstance(strategy, Strategy)


# ====================================================================== #
#  TaskDecomposer —— 端到端拆解
# ====================================================================== #


class TestTaskDecomposer:
    """TaskDecomposer 需求分析与任务拆解。"""

    @pytest.fixture
    def decomposer(self):
        return TaskDecomposer()

    def test_analyze_request_simple(self, decomposer):
        """简单需求分析。"""
        analysis = decomposer.analyze_request("实现一个加法函数")
        assert isinstance(analysis, RequirementAnalysis)
        assert analysis.goal == "实现一个加法函数"
        assert analysis.complexity.score >= 0
        assert analysis.estimated_steps >= 3
        assert len(analysis.suggested_roles) > 0

    def test_analyze_request_complex(self, decomposer):
        """复杂需求分析应识别更多角色。"""
        analysis = decomposer.analyze_request(
            "重构分布式系统架构，优化数据库性能和并发处理能力"
        )
        assert analysis.complexity.score >= 40
        assert len(analysis.suggested_roles) >= 2

    def test_decompose_simple_request(self, decomposer):
        """简单请求应生成立刻任务树。"""
        analysis = decomposer.analyze_request("实现一个加法函数")
        tree = decomposer.decompose(analysis)
        assert isinstance(tree, TaskTree)
        assert tree.total_tasks >= 1
        assert tree.root != ""

    def test_decompose_complex_request(self, decomposer):
        """复杂请求应生成分层任务树。"""
        analysis = decomposer.analyze_request("实现一个完整的用户管理系统")
        tree = decomposer.decompose(analysis)
        assert tree.total_tasks >= 3
        assert tree.depth >= 2

    def test_suggest_agents_for_request(self, decomposer):
        """为请求推荐智能体角色。"""
        roles = decomposer.suggest_agents_for_request("实现代码并评审")
        assert len(roles) > 0
        for r in roles:
            assert isinstance(r, AgentRole)

    def test_generate_dependencies(self, decomposer):
        """根据任务树生成依赖图。"""
        analysis = decomposer.analyze_request("实现一个登录模块")
        tree = decomposer.decompose(analysis)
        tasks = tree.flatten()
        graph = decomposer.generate_dependencies(tasks)
        assert graph.task_count >= 1
        assert graph.task_count <= tree.total_tasks

    def test_detect_tech_stack(self, decomposer):
        """技术栈检测。"""
        text = "用 Python 和 FastAPI 实现 REST API 接口"
        stack = TaskDecomposer._detect_tech_stack(text.lower())
        assert "python" in stack
        assert "fastapi" in stack

    def test_extract_keywords(self, decomposer):
        """关键词提取。"""
        text = "需要实现一个用户登录功能，使用 Python 编写"
        keywords = TaskDecomposer._extract_keywords(text)
        assert len(keywords) > 0

    def test_recommend_depth(self, decomposer):
        """复杂度→深度推荐。"""
        assert decomposer._recommend_depth(ComplexityScore(score=10)) == 1
        assert decomposer._recommend_depth(ComplexityScore(score=30)) == 2
        assert decomposer._recommend_depth(ComplexityScore(score=60)) == 3

    def test_map_agent_type_to_role(self):
        """agent_type→AgentRole 映射。"""
        assert TaskDecomposer._map_agent_type_to_role("code_gen") == AgentRole.CODER
        assert TaskDecomposer._map_agent_type_to_role("review") == AgentRole.REVIEWER
        assert TaskDecomposer._map_agent_type_to_role("requirement") == AgentRole.ANALYST

    def test_role_to_agent_type(self):
        """AgentRole→agent_type 反向映射。"""
        assert TaskDecomposer._role_to_agent_type(AgentRole.CODER) == "code_gen"
        assert TaskDecomposer._role_to_agent_type(AgentRole.REVIEWER) == "review"

    def test_infer_scope(self):
        """范围推断。"""
        scope = TaskDecomposer._infer_scope(
            "简单任务", ComplexityScore(score=10)
        )
        assert len(scope) >= 1

    def test_infer_constraints(self):
        """约束推断。"""
        constraints = TaskDecomposer._infer_constraints(
            "需要提供 REST API 接口"
        )
        assert len(constraints) >= 1

    def test_calc_priority(self):
        """优先级计算。"""
        node = TaskTreeNode(
            node_id="t1",
            agent_type="code_gen",
            complexity=ComplexityScore(score=50),
        )
        priority = TaskDecomposer._calc_priority(node)
        assert priority >= 90


# ====================================================================== #
#  数据类补充测试
# ====================================================================== #


class TestDataClasses:
    """RequirementAnalysis / Option / DecisionResult 补充测试。"""

    def test_requirement_analysis_is_simple(self):
        """is_simple 属性。"""
        analysis = RequirementAnalysis(
            complexity=ComplexityScore(level="simple", score=10)
        )
        assert analysis.is_simple is True
        assert analysis.is_complex is False

    def test_requirement_analysis_is_complex(self):
        """is_complex 属性。"""
        analysis = RequirementAnalysis(
            complexity=ComplexityScore(level="complex", score=60)
        )
        assert analysis.is_complex is True

    def test_option_fields(self):
        """Option 字段。"""
        opt = Option(
            name="方案A",
            description="方案描述",
            pros=["优点1"],
            cons=["缺点1"],
            score=85.0,
        )
        assert opt.name == "方案A"
        assert opt.score == 85.0

    def test_decision_result_fields(self):
        """DecisionResult 字段。"""
        result = DecisionResult(
            selected=Option(name="A", score=90),
            scores={"A": 90, "B": 70},
            reasoning="A 方案得分更高",
            alternatives=[Option(name="B", score=70)],
        )
        assert result.selected.name == "A"
        assert result.reasoning == "A 方案得分更高"

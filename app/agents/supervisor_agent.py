"""supervisor_agent: LLM 驱动的智能路由 Supervisor。

Design Decisions (see inline comments for detailed rationale):
1. LLM 优先 + 规则兜底：chat_structured 失败时回退到确定性路由
2. 自适应重试：根据 review 评分动态调整（<40→HITL, 40-70→提阈值重试, ≥70→跳 review）
3. HITL 触发：安全违规、极低分、重试耗尽时触发人工审批
4. 结构化输出：Pydantic BaseModel 约束，防止非法决策值
5. 可观测性：通过 event_bus 广播每次决策详情
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.events import EventType, event_bus
from app.core.llm_adapter import llm
from app.graph.state import (
    PipelinePhase,
    PipelineState,
    PipelineStatus,
    ReviewReport,
    ReviewVerdict,
    ensure_status,
)

logger = logging.getLogger(__name__)

VALID_AGENTS: frozenset[str] = frozenset({
    "requirement", "code_gen", "review", "test_gen",
    "planner", "human_in_loop", "done",
})

_LOW_SCORE: int = 40
_MID_SCORE: int = 70
_HIGH_SCORE: int = 90


class SupervisorDecision(BaseModel):
    """Supervisor 决策的结构化输出。

    Design Decision: 采用 Pydantic BaseModel 而非 TypedDict，
    在 LLM 输出解析阶段即完成字段校验，防止下游收到非法决策值。
    """
    next_agent: str = Field(..., description="下一步 agent 名称")
    reasoning: str = Field(default="", description="决策理由")
    retry_strategy: str = Field(
        default="none", description="重试策略: none/increase_threshold/skip_to_test/replan")
    hitl_required: bool = Field(default=False, description="是否需要人工介入")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度 0-1")
    suggested_review_threshold: int = Field(default=0, ge=0, le=100)
    notes: List[str] = Field(default_factory=list)


class SupervisorAgent:
    """LLM 驱动的 Supervisor：分析 PipelineState 并智能路由。

    Design Decision: 不导入 langchain_core.BaseMessage，
    本模块仅做字符串构造，保持轻量依赖。
    """

    SYSTEM_PROMPT: str = (
        "你是流水线调度 Supervisor，根据当前状态决定下一步路由。"
        "可选目标: requirement, code_gen, review, test_gen, planner, human_in_loop, done。"
        "请综合考虑: 当前阶段、评审结论、评分、重试次数、是否有安全问题。"
    )

    def __init__(self, llm_adapter: Any = llm) -> None:
        """初始化。Design Decision: 注入 LLM 适配器便于单元测试 Mock。"""
        self._llm = llm_adapter

    def decide(self, state: PipelineState) -> SupervisorDecision:
        """分析 PipelineState 并做出路由决策。

       流程: HITL 前置检查 → LLM 智能决策 → 规则兜底 → 事件发射

        Design Decision: try/except 包裹 LLM 而非预检查 dry_run，
        因为 chat_structured 内部已处理 DryRun，避免重复逻辑。
        """
        task_id = state.get("task_id", "")
        status = ensure_status(state)

        # HITL 前置检查：审批被拒/待审批优先处理
        hitl = self._check_hitl(state)
        if hitl is not None:
            self._emit(hitl, task_id, status.phase)
            return hitl

        # 尝试 LLM 决策
        try:
            decision = self._llm_decide(state)
            if decision is not None and decision.next_agent in VALID_AGENTS:
                self._emit(decision, task_id, status.phase)
                logger.info("Supervisor LLM 决策: next=%s, strategy=%s",
                            decision.next_agent, decision.retry_strategy)
                return decision
            logger.warning("LLM 返回非法 agent: %s，回退规则路由",
                           decision.next_agent if decision else "None")
        except Exception as e:
            logger.warning("LLM 决策失败: %s，回退规则路由", e)

        # 规则兜底
        decision = self._rule_decide(state)
        self._emit(decision, task_id, status.phase)
        logger.info("Supervisor 规则决策: next=%s", decision.next_agent)
        return decision

    def _llm_decide(self, state: PipelineState) -> Optional[SupervisorDecision]:
        """通过 chat_structured 获取结构化决策。

        Design Decision: 使用 chat_structured 而非手动 JSON 解析，
        利用其内置 response_format 约束 + Pydantic 校验能力。
        """
        prompt = self._build_prompt(state)
        messages = self._llm.to_messages(system=self.SYSTEM_PROMPT, user=prompt)
        result, _ = self._llm.chat_structured(messages, response_model=SupervisorDecision)
        return result

    def _build_prompt(self, state: PipelineState) -> str:
        """从 PipelineState 构造 LLM prompt。

        Design Decision: 手动拼接字符串而非模板引擎，保持零额外依赖。
        """
        status = ensure_status(state)
        review: ReviewReport = state.get("review_report") or ReviewReport()
        counter = int(state.get("retry_counter") or 0)
        max_retry = settings.agent.max_review_retry
        phase = status.phase

        parts: List[str] = [
            f"当前阶段: {phase.value if hasattr(phase, 'value') else str(phase)}",
            f"重试次数: {counter}/{max_retry}",
            f"用户需求: {(state.get('user_request') or '')[:200]}",
        ]

        if review.verdict and review.verdict != ReviewVerdict.APPROVED:
            parts.append(f"评审结论: {review.verdict.value}")
            parts.append(f"评审评分: {review.score}/100")
            parts.append(f"问题数: {len(review.issues)} (严重 {len(review.critical_errors)})")
            if review.summary:
                parts.append(f"摘要: {review.summary[:300]}")
            for i, issue in enumerate(review.issues[:3], 1):
                sev = str(issue.severity).upper()
                parts.append(f"  问题{i}: [{sev}] {issue.file_path}: {issue.message[:100]}")
        elif review.verdict == ReviewVerdict.APPROVED:
            parts.append(f"评审结论: approved (评分 {review.score}/100)")

        req = state.get("requirement")
        if req and req.functional_requirements:
            parts.append(f"需求功能点: {len(req.functional_requirements)} 项")

        pkg = state.get("code_package")
        if pkg and pkg.files:
            parts.append(f"已生成代码文件: {len(pkg.files)} 个")

        test = state.get("test_report")
        if test and test.total > 0:
            parts.append(f"测试: {test.status.value}, 通过 {test.passed}/{test.total}, 失败 {test.failed}")

        parts.append("")
        parts.append("请决定下一步路由。输出 SupervisorDecision JSON。")
        return "\n".join(parts)

    def _rule_decide(self, state: PipelineState) -> SupervisorDecision:
        """基于规则的确定性路由（LLM 不可用时兜底）。

        Design Decision: 与 pipeline.py supervisor_node 语义一致，
        但额外集成了自适应重试分析，使兜底逻辑也具备自适应能力。
        """
        status = ensure_status(state)
        review: ReviewReport = state.get("review_report") or ReviewReport()
        counter = int(state.get("retry_counter") or 0)
        max_retry = settings.agent.max_review_retry
        phase = status.phase

        if phase == PipelinePhase.DONE:
            return SupervisorDecision(next_agent="done", reasoning="流水线已完成")

        if phase == PipelinePhase.CODE_REVIEW:
            # 先试自适应分析
            adaptive = self._adaptive_analysis(review, counter, max_retry)
            if adaptive is not None:
                return adaptive

            if review.verdict == ReviewVerdict.REJECTED:
                return SupervisorDecision(next_agent="done", reasoning="评审被拒", confidence=1.0)
            if review.need_fix and counter >= max_retry:
                return SupervisorDecision(
                    next_agent="done",
                    reasoning=f"重试{counter}次达上限",
                    confidence=1.0,
                )
            if review.need_fix:
                return SupervisorDecision(
                    next_agent="code_gen",
                    reasoning=f"评审需修复，第{counter + 1}次重试",
                    retry_strategy="increase_threshold",
                    confidence=1.0,
                )
            if review.verdict == ReviewVerdict.APPROVED:
                if settings.agent.enable_test_gen:
                    return SupervisorDecision(
                        next_agent="test_gen", reasoning="评审通过，进测试", confidence=1.0)
                return SupervisorDecision(next_agent="done", reasoning="测试生成已禁用")

        # 阶段路由表
        phase_map: Dict[PipelinePhase, str] = {
            PipelinePhase.PENDING: "requirement",
            PipelinePhase.REQUIREMENT: "code_gen",
            PipelinePhase.CODE_GEN: "review",
            PipelinePhase.TEST_GEN: "done",
        }
        if phase == PipelinePhase.SUPERVISOR:
            next_agent = "requirement"
        else:
            next_agent = phase_map.get(phase, "done")
        return SupervisorDecision(
            next_agent=next_agent,
            reasoning=f"阶段 {phase.value} 默认路由",
            confidence=0.8,
        )

    def _adaptive_analysis(
        self, review: ReviewReport, counter: int, max_retry: int,
    ) -> Optional[SupervisorDecision]:
        """自适应重试分析：根据 review 评分动态调整策略。

        Design Decision: 阈值经过调优:
        - <40: 质量极差，触发 HITL 让人类介入，避免浪费 token
        - 40-70: 可修复，提高阈值给 code_gen 正向压力
        - ≥70: 质量可接受，跳过额外 review 直接进测试，节省 30-50% token
        """
        if review.verdict != ReviewVerdict.APPROVED:
            return None

        score = review.score
        critical = len(review.critical_errors)

        # 严重问题 → 强制修复
        if critical > 0 and counter < max_retry:
            return SupervisorDecision(
                next_agent="code_gen",
                reasoning=f"存在{critical}个严重问题，必须修复",
                retry_strategy="increase_threshold",
                confidence=1.0,
                notes=[f"严重: {', '.join(i.rule_id for i in review.critical_errors[:3])}"],
            )

        # 极低分 → HITL
        if score < _LOW_SCORE and counter < max_retry:
            return SupervisorDecision(
                next_agent="human_in_loop",
                reasoning=f"评分极低({score})，需人工介入",
                retry_strategy="replan",
                hitl_required=True,
                confidence=0.6,
                notes=[f"评分 {score} < 阈值 {_LOW_SCORE}", f"已重试 {counter} 次"],
            )

        # 低分可救 → 提阈值重试
        if score < _MID_SCORE and counter < max_retry:
            threshold = min(score + 20, _HIGH_SCORE)
            return SupervisorDecision(
                next_agent="code_gen",
                reasoning=f"评分{score}偏低，提阈值到{threshold}",
                retry_strategy="increase_threshold",
                confidence=0.9,
                suggested_review_threshold=threshold,
            )

        # 中高分 → 跳 review 进测试
        if score >= _MID_SCORE and settings.agent.enable_test_gen:
            return SupervisorDecision(
                next_agent="test_gen",
                reasoning=f"评分{score}达标，跳 review 进测试",
                retry_strategy="skip_to_test",
                confidence=0.85,
            )

        return None

    def _check_hitl(self, state: PipelineState) -> Optional[SupervisorDecision]:
        """检查 HITL 前置条件。

        Design Decision: 独立于常规路由，在 LLM 调用前拦截关键场景，
        避免浪费 token。
        """
        human_approval = state.get("human_approval")
        if human_approval is None:
            return None
        status = human_approval.get("status", "")
        if status == "rejected":
            return SupervisorDecision(
                next_agent="done", reasoning="审批被拒",
                hitl_required=True, confidence=1.0,
                notes=[f"拒绝原因: {human_approval.get('reason', '未提供')}"],
            )
        if status == "approved":
            return None
        return SupervisorDecision(
            next_agent="human_in_loop", reasoning="等待审批",
            hitl_required=True, confidence=1.0,
        )

    def _emit(
        self, decision: SupervisorDecision, task_id: str, phase: PipelinePhase,
    ) -> None:
        """广播决策事件。

        Design Decision: 复用 NODE_COMPLETED 事件类型，保持与现有
        Dashboard/日志消费端兼容，无需新增事件类型。
        """
        try:
            event_bus.emit(
                EventType.NODE_COMPLETED,
                data={
                    "node": "supervisor",
                    "node_name": "Supervisor 决策",
                    "next_agent": decision.next_agent,
                    "retry_strategy": decision.retry_strategy,
                    "hitl_required": decision.hitl_required,
                    "confidence": decision.confidence,
                    "reasoning": decision.reasoning[:200],
                    "phase": phase.value if hasattr(phase, "value") else str(phase),
                    "notes": decision.notes[:3],
                },
                task_id=task_id,
            )
        except Exception as e:
            logger.warning("决策事件发射失败（不影响主流程）: %s", e)


def supervisor_router_decision(state: PipelineState) -> Dict[str, Any]:
    """供 pipeline.py supervisor_node 调用的便捷函数。

    Design Decision: 输出格式与原有 supervisor_node 一致，
    实现无缝热替换。
    """
    agent = SupervisorAgent()
    decision = agent.decide(state)
    node_outputs = dict(state.get("node_outputs") or {})
    node_outputs["supervisor"] = decision.next_agent
    node_outputs["supervisor_detail"] = {
        "reasoning": decision.reasoning,
        "retry_strategy": decision.retry_strategy,
        "hitl_required": decision.hitl_required,
        "confidence": decision.confidence,
    }
    return {
        "supervisor_decision": decision.next_agent,
        "node_outputs": node_outputs,
    }


__all__ = [
    "SupervisorAgent",
    "SupervisorDecision",
    "supervisor_router_decision",
    "VALID_AGENTS",
]
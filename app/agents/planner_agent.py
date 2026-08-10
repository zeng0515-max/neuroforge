"""PlannerAgent: Plan-Execute-Reflect 循环。

Design Decision:
    三阶段闭环控制替代线性流水线：
    Plan(需求分解) → Execute(调度子智能体) → Reflect(结果反思)
    反思决策: continue / adjust / replan / abort

    拓扑排序用 Kahn 算法。LLM 不可用时降级为规则驱动。
"""
from __future__ import annotations

import importlib
import logging
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.events import EventType, event_bus
from app.core.llm_adapter import LLMAdapter, llm
from app.graph.state import (
    CodePackage, PipelinePhase, PipelineState,
    RequirementSpec, ReviewReport, TestReport,
)

logger = logging.getLogger(__name__)
_MAX_REFLECT = 5

_AGENT_DISPATCH: Dict[str, tuple] = {
    "code_gen": ("app.agents.code_gen_agent", "CodeGenAgent"),
    "review": ("app.agents.review_agent", "ReviewAgent"),
    "test_gen": ("app.agents.test_gen_agent", "TestGenAgent"),
}


class PlanStep(BaseModel):
    """计划步骤：原子化执行单元。Design Decision: 懒加载避免循环依赖。"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    description: str = ""
    agent_type: Literal["code_gen", "review", "test_gen"] = "code_gen"
    depends_on: List[str] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    result: Dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    """执行计划：有序步骤集合 + 元数据。"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_request: str = ""
    requirement: Optional[RequirementSpec] = None
    steps: List[PlanStep] = Field(default_factory=list)
    current_step_index: int = 0
    status: Literal["planning", "ready", "executing", "adjusted", "completed", "failed"] = "planning"

    def ordered_steps(self) -> List[PlanStep]:
        """Kahn 算法拓扑排序，确保依赖前置。"""
        if not self.steps:
            return []
        id_map = {s.id: s for s in self.steps}
        in_deg = {sid: 0 for sid in id_map}
        for s in self.steps:
            for dep in s.depends_on:
                if dep in in_deg:
                    in_deg[s.id] += 1
        queue = [sid for sid, d in in_deg.items() if d == 0]
        ordered: List[PlanStep] = []
        while queue:
            sid = queue.pop(0)
            ordered.append(id_map[sid])
            for s in self.steps:
                if sid in s.depends_on:
                    in_deg[s.id] -= 1
                    if in_deg[s.id] == 0:
                        queue.append(s.id)
        if len(ordered) != len(self.steps):
            for s in self.steps:
                if s not in ordered:
                    ordered.append(s)
        return ordered


class PlanReflection(BaseModel):
    """反思结果：continue/adjust/replan/abort 四选一。"""
    decision: Literal["continue", "adjust", "replan", "abort"] = "continue"
    adjustments: List[str] = Field(default_factory=list)
    reasoning: str = ""
    affected_step_ids: List[str] = Field(default_factory=list)


class PlannerAgent:
    """规划智能体：Plan-Execute-Reflect 循环。

    Design Decision:
    - llm_adapter 可注入便于测试
    - 反思轮次上限防止无限循环
    - 所有公共方法发布 event_bus 事件
    """
    MAX_REFLECT_ROUNDS = _MAX_REFLECT

    def __init__(self, llm_adapter: Optional[LLMAdapter] = None) -> None:
        self.llm = llm_adapter or llm
        self._reflect_round = 0
        self._last_reflection = None  # type: ignore[assignment]

    def create_plan(
        self,
        user_request: str,
        requirement: Optional[RequirementSpec] = None,
        state: Optional[PipelineState] = None,
    ) -> ExecutionPlan:
        """根据需求创建 ExecutionPlan，LLM 失败降级为三段式默认计划。"""
        task_id = (state or {}).get("task_id", "")
        event_bus.publish(EventType.TASK_STARTED, {"phase": "planning"}, task_id=task_id)
        plan = self._llm_decompose(user_request, requirement)
        if plan is None:
            plan = self._fallback_plan(user_request)
        plan.status = "ready"
        self._reflect_round = 0
        logger.info("Plan created: %d steps", len(plan.steps))
        return plan

    def _llm_decompose(
        self, user_request: str, requirement: Optional[RequirementSpec],
    ) -> Optional[ExecutionPlan]:
        """LLM 任务分解，失败返回 None 触发降级。"""
        req_line = ""
        if requirement and requirement.functional_requirements:
            req_line = "\n".join(f"- {r}" for r in requirement.functional_requirements[:5])
        prompt = (
            f"分解需求为有序执行步骤，每步指定 agent_type (code_gen/review/test_gen)：\n"
            f"需求：{user_request}\n要点：\n{req_line or '（无）'}\n\n"
            f'输出 JSON：{{"steps": [{{"title":"..", "description":"..", '
            f'"agent_type":"code_gen", "depends_on": []}}]}}'
        )
        from langchain_core.messages import HumanMessage, SystemMessage
        import json
        try:
            resp = self.llm.chat(
                [SystemMessage(content="你是任务规划专家，输出严格 JSON。"),
                 HumanMessage(content=prompt)],
                use_code_model=False,
            )
            content = resp if isinstance(resp, str) else str(resp.content)
            parsed = json.loads(self._extract_json(content))
            raw_steps = parsed.get("steps", [])
            if not raw_steps:
                return None
            steps = [PlanStep(
                title=str(s.get("title", "")),
                description=str(s.get("description", "")),
                agent_type=str(s.get("agent_type", "code_gen")),
                depends_on=list(s.get("depends_on", [])),
            ) for s in raw_steps]
            return ExecutionPlan(user_request=user_request, requirement=requirement, steps=steps)
        except Exception as exc:
            logger.warning("LLM decompose failed: %s", exc)
            return None

    @staticmethod
    def _extract_json(text: str) -> str:
        """从 LLM 输出中提取 JSON 字符串。"""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        s, e = text.find("{"), text.rfind("}") + 1
        return text[s:e] if 0 <= s < e else text

    @staticmethod
    def _fallback_plan(user_request: str) -> ExecutionPlan:
        """三段式默认计划：code_gen → review → test_gen。"""
        return ExecutionPlan(
            user_request=user_request,
            steps=[
                PlanStep(title="代码生成", description="根据需求生成代码", agent_type="code_gen"),
                PlanStep(title="代码评审", description="审查生成的代码", agent_type="review"),
                PlanStep(title="测试生成", description="生成并执行测试", agent_type="test_gen"),
            ],
        )

    def execute_step(
        self, plan: ExecutionPlan, step_index: int, state: PipelineState,
    ) -> Dict[str, Any]:
        """执行指定步骤，调度对应子智能体。Design Decision: 懒加载避免循环依赖。"""
        ordered = plan.ordered_steps()
        if step_index >= len(ordered):
            return {"error": "step_index out of range"}
        step = ordered[step_index]
        step.status = "running"
        task_id = state.get("task_id", "")
        event_bus.publish(EventType.NODE_COMPLETED,
                          {"step_id": step.id, "agent_type": step.agent_type}, task_id=task_id)
        try:
            mod = importlib.import_module(_AGENT_DISPATCH[step.agent_type][0])
            agent_cls = getattr(mod, _AGENT_DISPATCH[step.agent_type][1])
            result = agent_cls().run(state)
            step.status = "completed"
            step.result = result.model_dump() if hasattr(result, "model_dump") else (
                result if isinstance(result, dict) else {"raw": str(result)})
            self._sync_state(result, state, step.agent_type)
            logger.info("Step %d (%s) completed", step_index, step.title)
            return step.result
        except Exception as exc:
            step.status = "failed"
            step.result = {"error": str(exc)}
            event_bus.publish(EventType.NODE_FAILED,
                              {"step_id": step.id, "error": str(exc)}, task_id=task_id)
            logger.error("Step %d failed: %s", step_index, exc)
            return step.result

    @staticmethod
    def _sync_state(result: Any, state: PipelineState, agent_type: str) -> None:
        """将执行结果同步回 PipelineState。"""
        mapping = {"code_gen": CodePackage, "review": ReviewReport, "test_gen": TestReport}
        if agent_type in mapping and isinstance(result, mapping[agent_type]):
            state[{"code_package" if agent_type == "code_gen" else
                   "review_report" if agent_type == "review" else
                   "test_report"}] = result

    def reflect(
        self, plan: ExecutionPlan, state: PipelineState,
        review_report: Optional[ReviewReport] = None,
    ) -> PlanReflection:
        """分析执行结果，决定计划走向。LLM 失败时规则降级。"""
        if self._reflect_round >= self.MAX_REFLECT_ROUNDS:
            self._last_reflection = PlanReflection(
                decision="abort", reasoning=f"反思轮次达上限 ({self.MAX_REFLECT_ROUNDS})")
            return self._last_reflection
        self._reflect_round += 1
        reflection = self._llm_reflect(plan, state, review_report)
        if reflection is None:
            reflection = self._rule_reflect(state, review_report)
        self._last_reflection = reflection
        logger.info("Reflect round %d: %s", self._reflect_round, reflection.decision)
        return reflection

    def _llm_reflect(
        self, plan: ExecutionPlan, state: PipelineState,
        review_report: Optional[ReviewReport],
    ) -> Optional[PlanReflection]:
        """LLM 语义分析反思。"""
        report = review_report or state.get("review_report")
        review_summary = ""
        if report:
            review_summary = f"评审: {report.verdict.value}, 评分: {report.score}"
        test_rpt = state.get("test_report")
        test_summary = f"测试: {test_rpt.passed}/{test_rpt.total} 通过" if test_rpt else ""
        prompt = (
            f"分析执行结果决定下一步：\n"
            f"计划: {len(plan.steps)} 步, 当前: {plan.current_step_index}\n"
            f"{review_summary}\n{test_summary}\n\n"
            f'输出: {{"decision": "continue|adjust|replan|abort", '
            f'"adjustments": [".."], "reasoning": ".."}}'
        )
        from langchain_core.messages import HumanMessage, SystemMessage
        import json
        try:
            resp = self.llm.chat(
                [SystemMessage(content="你是流程控制专家。"),
                 HumanMessage(content=prompt)],
                use_code_model=False,
            )
            content = resp if isinstance(resp, str) else str(resp.content)
            parsed = json.loads(self._extract_json(content))
            decision = parsed.get("decision", "continue")
            if decision not in ("continue", "adjust", "replan", "abort"):
                decision = "continue"
            return PlanReflection(
                decision=decision,
                adjustments=list(parsed.get("adjustments", [])),
                reasoning=str(parsed.get("reasoning", "")),
            )
        except Exception as exc:
            logger.warning("LLM reflect failed: %s", exc)
            return None

    @staticmethod
    def _rule_reflect(
        state: PipelineState, review_report: Optional[ReviewReport],
    ) -> PlanReflection:
        """规则降级：评审未通过/测试失败 → adjust。"""
        report = review_report or state.get("review_report")
        if report and getattr(report, "need_fix", False):
            return PlanReflection(
                decision="adjust",
                adjustments=["review_failed: 回退代码生成修复"],
                reasoning=f"评审未通过 ({report.verdict.value})",
            )
        test_rpt = state.get("test_report")
        if test_rpt and test_rpt.failed > 0:
            return PlanReflection(
                decision="adjust",
                adjustments=["test_failed: 缩减范围或增加修复步骤"],
                reasoning=f"测试失败 ({test_rpt.failed} 个)",
            )
        return PlanReflection(decision="continue", reasoning="所有步骤正常")

    def apply_reflection(
        self, plan: ExecutionPlan, reflection: PlanReflection, state: PipelineState,
    ) -> ExecutionPlan:
        """根据反思结果调整计划。"""
        tid = state.get("task_id", "")
        if reflection.decision == "abort":
            plan.status = "failed"
            event_bus.publish(EventType.PIPELINE_FAILED,
                              {"reason": reflection.reasoning}, task_id=tid)
            return plan
        if reflection.decision == "replan":
            new_plan = self.create_plan(plan.user_request, plan.requirement, state)
            event_bus.publish(EventType.TASK_STARTED,
                              {"phase": "replanned", "round": self._reflect_round}, task_id=tid)
            return new_plan
        if reflection.decision == "adjust":
            for step in plan.steps:
                if step.id in reflection.affected_step_ids:
                    step.status = "skipped"
            plan.status = "adjusted"
            logger.info("Plan adjusted: %s", reflection.adjustments)
        if plan.current_step_index >= len(plan.ordered_steps()):
            plan.status = "completed"
            event_bus.publish(EventType.PIPELINE_DONE,
                              {"rounds": self._reflect_round}, task_id=tid)
        return plan

    def run_cycle(
        self, user_request: str, state: PipelineState,
        requirement: Optional[RequirementSpec] = None,
    ) -> ExecutionPlan:
        """完整 Plan-Execute-Reflect 循环。"""
        plan = self.create_plan(user_request, requirement, state)
        max_iter = max(len(plan.steps) * 2, 3)
        iteration = 0
        while plan.status not in ("completed", "failed") and iteration < max_iter:
            iteration += 1
            ordered = plan.ordered_steps()
            if all(s.status in ("completed", "failed", "skipped") for s in ordered):
                plan.status = "completed"
                break
            for idx, step in enumerate(ordered):
                if step.status in ("completed", "failed", "skipped"):
                    continue
                self.execute_step(plan, idx, state)
                reflection = self.reflect(plan, state, state.get("review_report"))
                plan = self.apply_reflection(plan, reflection, state)
                if plan.status in ("completed", "failed") or reflection.decision == "replan":
                    break
        if plan.status not in ("completed", "failed"):
            plan.status = "completed"
        return plan
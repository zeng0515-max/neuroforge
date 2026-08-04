from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict as _TypedDict

from app.core.config import settings
from app.graph.state.enums import (
    PipelinePhase,
    ReviewSeverity,
    ReviewVerdict,
    TestStatus,
)
from app.graph.state.models import (
    CodePackage,
    GeneratedFile,
    PipelineStatus,
    RequirementSpec,
    ReviewIssue,
    ReviewReport,
    TestCase,
    TestReport,
)


# ====================================================================== #
#  LangGraph State
# ====================================================================== #

def _concat_list(left: List[Any], right: Optional[List[Any]]) -> List[Any]:
    """LangGraph reducer：将新消息追加到历史消息末端。"""
    if right is None:
        return list(left)
    return list(left) + list(right)


class PipelineState(TypedDict, total=False):
    """
    LangGraph 全局 State（TypedDict 形式，配合 LangGraph StateGraph 使用）。
    说明：
      - user_request  / requirement  / code 等：简单值，节点间直接覆盖
      - messages ：Annotated[list, add_reducer]，用作多轮聊天历史累积
      - review_feedback ：评审失败时携带的 issues，供 code_gen 重试时针对性修复
    """

    task_id: str
    user_request: str
    extra_args: Dict[str, Any]

    requirement: RequirementSpec
    code_package: CodePackage
    review_report: ReviewReport
    test_report: TestReport

    # 方案A：Retry with Feedback
    # review_node 在 verdict=needs_changes 时把 issues 写入此字段
    # code_gen_node 检测到此字段时，在 prompt 追加"上一版问题"段，针对性修复
    review_feedback: List[ReviewIssue]

    status: PipelineStatus

    messages: Annotated[List[BaseMessage], _concat_list]

    retry_counter: int
    node_outputs: Dict[str, Any]

    # Supervisor 决策：由 supervisor_node 分析各节点输出后决定下一步路由
    supervisor_decision: str

    # Human-in-the-Loop：流水线暂停等待人工审批
    human_approval: Optional[Dict[str, Any]]

    # Bug S3：per-task 模型透传，避免全局 switch_model 在并发任务间串扰
    skill_id: str
    model: Optional[str]


# ====================================================================== #
#  便捷构造 / 工具函数
# ====================================================================== #
#  LangGraph 状态规范化工具
# ====================================================================== #


def normalize_state(state: PipelineState) -> PipelineState:
    """把 LangGraph 传过来的 dict/TypedDict 字段全部转回 Pydantic 对象。

    LangGraph 在节点之间传递的是 dict 形态（因为 StateGraph 会把 TypedDict 字段
    展开为原始 dict），这会导致节点函数里 `state.get("requirement")` 拿到的是 dict
    而不是 RequirementSpec。此函数做一次集中转换，保证后续节点拿到的是强类型对象。
    """
    extra: Dict[str, Any] = dict(state) if hasattr(state, "items") else {}

    # status
    st = extra.get("status")
    if isinstance(st, PipelineStatus):
        pass
    elif isinstance(st, dict):
        try:
            extra["status"] = PipelineStatus(
                **{k: v for k, v in st.items() if k in PipelineStatus.model_fields}
            )
        except Exception:
            extra["status"] = PipelineStatus()
    elif st is None:
        extra["status"] = PipelineStatus()

    # requirement
    req = extra.get("requirement")
    if isinstance(req, RequirementSpec):
        pass
    elif isinstance(req, dict):
        try:
            extra["requirement"] = RequirementSpec(
                **{k: v for k, v in req.items() if k in RequirementSpec.model_fields}
            )
        except Exception:
            extra["requirement"] = RequirementSpec()

    # code_package
    pkg = extra.get("code_package")
    if isinstance(pkg, CodePackage):
        pass
    elif isinstance(pkg, dict):
        files_raw = pkg.get("files") or []
        files: List[GeneratedFile] = []
        for f in files_raw:
            if isinstance(f, GeneratedFile):
                files.append(f)
            elif isinstance(f, dict):
                try:
                    files.append(GeneratedFile(**{k: v for k, v in f.items() if k in GeneratedFile.model_fields}))
                except Exception:
                    pass
        extra["code_package"] = CodePackage(
            files=files,
            entry_point=str(pkg.get("entry_point", "")),
            install_steps=list(pkg.get("install_steps", []) or []),
            raw_llm_output=str(pkg.get("raw_llm_output", "")),
        )

    # review_report
    rr = extra.get("review_report")
    if isinstance(rr, ReviewReport):
        pass
    elif isinstance(rr, dict):
        issues_raw = rr.get("issues") or []
        issues: List[ReviewIssue] = []
        for it in issues_raw:
            if isinstance(it, ReviewIssue):
                issues.append(it)
            elif isinstance(it, dict):
                issues.append(ReviewIssue(
                    severity=it.get("severity", "warning"),
                    file=it.get("file") or it.get("file_path") or "",
                    line=it.get("line") or it.get("line_start"),
                    rule=it.get("rule") or it.get("rule_id") or "",
                    message=it.get("message", ""),
                    suggestion=it.get("suggestion", ""),
                    file_path=it.get("file_path") or it.get("file") or "",
                    line_start=it.get("line_start") or it.get("line") or 0,
                    rule_id=it.get("rule_id") or it.get("rule") or "",
                ))
        try:
            verdict = ReviewVerdict(str(rr.get("verdict") or "approved"))
        except Exception:
            verdict = ReviewVerdict.APPROVED
        extra["review_report"] = ReviewReport(
            verdict=verdict,
            summary=str(rr.get("summary", "")),
            issues=issues,
            score=int(rr.get("score", 100) or 100),
            retry_count=int(rr.get("retry_count", 0) or 0),
            coverage_check=dict(rr.get("coverage_check") or {}),
            raw_llm_output=str(rr.get("raw_llm_output", "")),
        )

    # test_report
    tr = extra.get("test_report")
    if isinstance(tr, TestReport):
        pass
    elif isinstance(tr, dict):
        cases_raw = tr.get("test_cases") or []
        cases: List[TestCase] = []
        for c in cases_raw:
            if isinstance(c, TestCase):
                cases.append(c)
            elif isinstance(c, dict):
                cases.append(TestCase(
                    name=str(c.get("name", "")),
                    description=str(c.get("description", "")),
                    target_file=str(c.get("target_file", "")),
                    target_function=str(c.get("target_function", "")),
                    priority=str(c.get("priority", "medium")),
                    status=str(c.get("status", "generated")),
                    error_message=str(c.get("error_message", "")),
                    output=str(c.get("output", "")),
                    duration_ms=int(c.get("duration_ms", 0) or 0),
                    code_snippet=str(c.get("code_snippet", "")),
                ))
        try:
            ts = TestStatus(str(tr.get("status") or "not_run"))
        except Exception:
            ts = TestStatus.NOT_RUN
        extra["test_report"] = TestReport(
            status=ts,
            summary=str(tr.get("summary", "")),
            total=int(tr.get("total") or tr.get("total_tests") or len(cases)),
            passed=int(tr.get("passed", 0) or 0),
            failed=int(tr.get("failed", 0) or 0),
            skipped=int(tr.get("skipped", 0) or 0),
            error_count=int(tr.get("error_count", 0) or 0),
            duration_ms=int(tr.get("duration_ms", 0) or 0),
            run_command=str(tr.get("run_command", "")),
            test_cases=cases,
            test_files=list(tr.get("test_files") or []),
            raw_llm_output=str(tr.get("raw_llm_output", "")),
        )

    return PipelineState(**extra)  # type: ignore[return-value]


def ensure_status(state: PipelineState) -> PipelineStatus:
    """从 state 中安全取出 PipelineStatus，自动处理 dict 形态。"""
    st = state.get("status")
    if isinstance(st, PipelineStatus):
        return st
    if isinstance(st, dict):
        try:
            return PipelineStatus(
                **{k: v for k, v in st.items() if k in PipelineStatus.model_fields}
            )
        except Exception:
            return PipelineStatus()
    return PipelineStatus()


# ====================================================================== #

def new_state(
    *,
    task_id: str,
    user_request: str,
    extra_args: Optional[Dict[str, Any]] = None,
    skill_id: str = "",
    model: Optional[str] = None,
) -> PipelineState:
    """构造一个初始状态，便于上层调用。

    Bug S3：支持透传 skill_id 与 model，避免依赖全局 switch_model 造成并发串扰。
    """
    state: PipelineState = {
        "task_id": task_id,
        "user_request": user_request,
        "extra_args": extra_args or {},
        "requirement": RequirementSpec(),
        "code_package": CodePackage(),
        "review_report": ReviewReport(),
        "test_report": TestReport(),
        "status": PipelineStatus(phase=PipelinePhase.PENDING),
        "messages": [],
        "retry_counter": 0,
        "node_outputs": {},
        "supervisor_decision": "",
        "human_approval": None,
    }
    if skill_id:
        state["skill_id"] = skill_id
    if model:
        state["model"] = model
    return state


def should_enter_test_gen(state: PipelineState) -> bool:
    """条件边：是否进入测试生成阶段。"""
    if not settings.agent.enable_test_gen:
        return False
    report: ReviewReport = state.get("review_report") or ReviewReport()
    return report.verdict == ReviewVerdict.APPROVED


def should_retry_codegen(state: PipelineState) -> bool:
    """条件边：评审未通过，是否需要回退到代码生成重新修改。"""
    report: ReviewReport = state.get("review_report") or ReviewReport()
    if report.verdict not in (ReviewVerdict.NEED_FIX, ReviewVerdict.NEEDS_CHANGES):
        return False
    return (state.get("retry_counter") or 0) < settings.agent.max_review_retry


__all__ = [
    "PipelinePhase",
    "ReviewVerdict",
    "ReviewSeverity",
    "TestStatus",
    "TestCase",
    "RequirementSpec",
    "GeneratedFile",
    "CodePackage",
    "ReviewIssue",
    "ReviewReport",
    "TestReport",
    "PipelineStatus",
    "PipelineState",
    "new_state",
    "normalize_state",
    "ensure_status",
    "should_enter_test_gen",
    "should_retry_codegen",
]
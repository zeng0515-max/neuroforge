"""Pipeline 编排单元测试。

重点测：
- PipelineState / new_state 构造
- 阶段枚举 PipelinePhase
- 条件边 _router_after_review 的四种路由分支
- retry_code_gen_node 自增计数器
- LinearPipeline 在 mock 节点下的回退与终止逻辑
- should_enter_test_gen / should_retry_codegen 条件函数
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

from app.graph.pipeline import (
    LinearPipeline,
    _router_after_review,
    retry_code_gen_node,
)
from app.graph.state import (
    PipelinePhase,
    PipelineState,
    PipelineStatus,
    ReviewReport,
    ReviewVerdict,
    TestStatus,
    ensure_status,
    new_state,
    should_enter_test_gen,
    should_retry_codegen,
)
from app.graph.state.models import (
    CodePackage,
    GeneratedFile,
    RequirementSpec,
    TestReport,
)


# ------------------------------------------------------------------ #
#  枚举与状态构造
# ------------------------------------------------------------------ #
def test_pipeline_phase_enum_values() -> None:
    """阶段枚举应包含 PENDING/REQUIREMENT/CODE_GEN/REVIEW/TEST_GEN/DONE/FAILED。"""
    expected = {"pending", "requirement", "code_gen", "review", "test_gen", "done", "failed"}
    actual = {p.value for p in PipelinePhase}
    assert expected.issubset(actual)
    # PipelinePhase 是 str Enum，可直接当字符串用
    assert PipelinePhase.PENDING == "pending"


def test_new_state_initial_fields() -> None:
    """new_state 应产出带默认空对象的 state，retry_counter=0。"""
    state = new_state(task_id="t-1", user_request="写个加法")
    assert state["task_id"] == "t-1"
    assert state["user_request"] == "写个加法"
    assert state["retry_counter"] == 0
    assert isinstance(state["requirement"], RequirementSpec)
    assert isinstance(state["code_package"], CodePackage)
    assert isinstance(state["review_report"], ReviewReport)
    assert isinstance(state["status"], PipelineStatus)
    assert state["status"].phase == PipelinePhase.PENDING


def test_ensure_status_handles_dict_and_none() -> None:
    """ensure_status 应兼容 PipelineStatus / dict / None 三种输入。"""
    s1 = ensure_status({"status": PipelineStatus(phase=PipelinePhase.CODE_GEN)})
    assert s1.phase == PipelinePhase.CODE_GEN

    s2 = ensure_status({"status": {"phase": "review"}})
    assert s2.phase == PipelinePhase.REVIEW

    s3 = ensure_status({})
    assert s3.phase == PipelinePhase.PENDING


# ------------------------------------------------------------------ #
#  条件边 _router_after_review
# ------------------------------------------------------------------ #
def test_router_rejected_returns_done() -> None:
    """评审 REJECTED 应直接 done。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.REJECTED)
    assert _router_after_review(state) == "done"


def test_router_need_fix_under_limit_returns_retry() -> None:
    """NEED_FIX 且未达上限应返回 retry。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.NEED_FIX)
    state["retry_counter"] = 0
    # max_review_retry 默认 3，0 < 3 → retry
    assert _router_after_review(state) == "retry"


def test_router_need_fix_at_limit_returns_done() -> None:
    """NEED_FIX 但已达重试上限应返回 done（不再重试）。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.NEED_FIX)
    # 让 retry_counter 达到上限；通过 monkeypatch settings.agent.max_review_retry
    with patch("app.graph.pipeline.settings") as mock_s:
        mock_s.agent.max_review_retry = 2
        state["retry_counter"] = 2
        assert _router_after_review(state) == "done"


def test_router_approved_with_test_gen_returns_test_gen() -> None:
    """APPROVED 且启用测试生成应返回 test_gen。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.APPROVED)
    # should_enter_test_gen 依赖 settings.agent.enable_test_gen，默认 True
    assert _router_after_review(state) == "test_gen"


def test_router_approved_without_test_gen_returns_done() -> None:
    """APPROVED 但禁用测试生成应返回 done。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.APPROVED)
    with patch("app.graph.state.settings") as mock_s:
        mock_s.agent.enable_test_gen = False
        assert _router_after_review(state) == "done"


# ------------------------------------------------------------------ #
#  条件辅助函数
# ------------------------------------------------------------------ #
def test_should_retry_codegen_need_fix_under_limit() -> None:
    """NEED_FIX 且未达上限 → True。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.NEED_FIX)
    state["retry_counter"] = 1
    assert should_retry_codegen(state) is True


def test_should_retry_codegen_approved_returns_false() -> None:
    """APPROVED 不需要重试 → False。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.APPROVED)
    assert should_retry_codegen(state) is False


def test_should_enter_test_gen_approved_returns_true() -> None:
    """APPROVED 且启用测试生成 → True。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.APPROVED)
    assert should_enter_test_gen(state) is True


def test_should_enter_test_gen_rejected_returns_false() -> None:
    """REJECTED 不进入测试生成 → False。"""
    state = new_state(task_id="t", user_request="x")
    state["review_report"] = ReviewReport(verdict=ReviewVerdict.REJECTED)
    assert should_enter_test_gen(state) is False


# ------------------------------------------------------------------ #
#  retry_code_gen_node
# ------------------------------------------------------------------ #
def test_retry_code_gen_node_increments_counter() -> None:
    """retry_code_gen_node 应将 retry_counter +1 并切换 phase 到 CODE_GEN。"""
    state = new_state(task_id="t", user_request="x")
    state["retry_counter"] = 2
    patch_dict = retry_code_gen_node(state)
    assert patch_dict["retry_counter"] == 3
    status = patch_dict["status"]
    assert status.phase == PipelinePhase.CODE_GEN


# ------------------------------------------------------------------ #
#  LinearPipeline 回退逻辑（mock 所有节点）
# ------------------------------------------------------------------ #
def _make_state_for_pipeline() -> PipelineState:
    """构造一个用于 LinearPipeline 测试的初始 state。"""
    return new_state(task_id="t-lin", user_request="写个加法函数")


def test_linear_pipeline_approved_path() -> None:
    """APPROVED 路径：requirement → code_gen → review → test_gen → done。"""
    state = _make_state_for_pipeline()

    def _req_node(s):
        return {"requirement": RequirementSpec(title="加法", goal="实现 a+b")}

    def _code_node(s):
        return {
            "code_package": CodePackage(
                files=[GeneratedFile(path="add.py", content="def add(a,b): return a+b")],
            )
        }

    def _review_node(s):
        return {"review_report": ReviewReport(verdict=ReviewVerdict.APPROVED, score=95)}

    def _test_node(s):
        return {"test_report": TestReport(status=TestStatus.PASSED, total=1, passed=1)}

    with patch("app.graph.pipeline.requirement_node", side_effect=_req_node), \
            patch("app.graph.pipeline.code_gen_node", side_effect=_code_node), \
            patch("app.graph.pipeline.review_node", side_effect=_review_node), \
            patch("app.graph.pipeline.test_gen_node", side_effect=_test_node):
        result = LinearPipeline.run(state)

    # 最终 status 应为 DONE
    status = ensure_status(result)
    assert status.phase == PipelinePhase.DONE
    assert result["requirement"].title == "加法"
    assert result["review_report"].verdict == ReviewVerdict.APPROVED
    assert result["test_report"].status == TestStatus.PASSED


def test_linear_pipeline_rejected_path() -> None:
    """REJECTED 路径：requirement → code_gen → review → done（不进入 test_gen）。

    Bug S1 修复后：REJECTED 应被标记为 FAILED（而非 DONE），并写入错误信息。
    """
    state = _make_state_for_pipeline()

    review_call_count = {"n": 0}

    def _req_node(s):
        return {"requirement": RequirementSpec()}

    def _code_node(s):
        return {"code_package": CodePackage()}

    def _review_node(s):
        review_call_count["n"] += 1
        return {"review_report": ReviewReport(verdict=ReviewVerdict.REJECTED)}

    def _test_node(s):  # 不应被调用
        raise AssertionError("REJECTED 路径不应进入 test_gen")

    with patch("app.graph.pipeline.requirement_node", side_effect=_req_node), \
            patch("app.graph.pipeline.code_gen_node", side_effect=_code_node), \
            patch("app.graph.pipeline.review_node", side_effect=_review_node), \
            patch("app.graph.pipeline.test_gen_node", side_effect=_test_node):
        result = LinearPipeline.run(state)

    status = ensure_status(result)
    # Bug S1：REJECTED 应标记为 FAILED，并写入"代码评审未通过"
    assert status.phase == PipelinePhase.FAILED
    assert status.error_message == "代码评审未通过"
    assert review_call_count["n"] == 1


def test_linear_pipeline_retry_then_approved() -> None:
    """重试路径：第一轮 NEED_FIX → retry → 第二轮 APPROVED → test_gen → done。"""
    state = _make_state_for_pipeline()
    call_log: list[str] = []

    def _req_node(s):
        call_log.append("req")
        return {"requirement": RequirementSpec()}

    def _code_node(s):
        call_log.append("code")
        return {"code_package": CodePackage()}

    review_states = [
        ReviewReport(verdict=ReviewVerdict.NEED_FIX),
        ReviewReport(verdict=ReviewVerdict.APPROVED, score=90),
    ]
    review_iter = iter(review_states)

    def _review_node(s):
        call_log.append("review")
        return {"review_report": next(review_iter)}

    def _test_node(s):
        call_log.append("test")
        return {"test_report": TestReport(status=TestStatus.PASSED)}

    with patch("app.graph.pipeline.requirement_node", side_effect=_req_node), \
            patch("app.graph.pipeline.code_gen_node", side_effect=_code_node), \
            patch("app.graph.pipeline.review_node", side_effect=_review_node), \
            patch("app.graph.pipeline.test_gen_node", side_effect=_test_node):
        result = LinearPipeline.run(state)

    # 调用顺序：req → code → review(need_fix) → retry → code → review(approved) → test
    assert call_log == ["req", "code", "review", "code", "review", "test"]
    status = ensure_status(result)
    assert status.phase == PipelinePhase.DONE
    assert result["retry_counter"] == 1


def test_linear_pipeline_node_exception_marks_failed() -> None:
    """节点抛异常应被捕获并标记 FAILED，不向上传播。"""
    state = _make_state_for_pipeline()

    def _req_node(s):
        raise RuntimeError("requirement 节点爆炸")

    with patch("app.graph.pipeline.requirement_node", side_effect=_req_node):
        # LinearPipeline 内部捕获异常并 raise，让上层感知；这里验证它确实 raise
        with pytest.raises(RuntimeError, match="爆炸"):
            LinearPipeline.run(state)

    # state 应被标记 FAILED（_apply 内部在 raise 前已写 status）
    status = ensure_status(state)
    assert status.phase == PipelinePhase.FAILED
    assert "requirement" in status.error_message

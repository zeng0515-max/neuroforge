"""Agent 评估框架测试。

覆盖范围：
- app/eval/metrics.py：compute_task_metrics / compute_eval_report
- app/eval/evaluator.py：Evaluator 类（通过注入 mock DB，不依赖真实数据）
- app/api/v1/eval.py：评估相关 API 端点（使用 conftest 提供的 TestClient）

设计要点：
- 用 SimpleNamespace 构造 mock 的 TaskRecord / StageLog，避免依赖 ORM session
- Evaluator 测试通过注入 mock DB 客户端，确保空数据场景稳定可复现
- 全部用例在离线 / DryRun 模式下可运行（conftest 已锁定环境变量）
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.eval.evaluator import Evaluator, evaluator as global_evaluator
from app.eval.metrics import (
    EvalReport,
    TaskMetrics,
    compute_eval_report,
    compute_task_metrics,
)


# ====================================================================== #
#  Mock 构造工具
# ====================================================================== #

def make_mock_task(
    task_id: str = "test-001",
    status: str = "done",
    phase: str = "done",
    error_message: str = "",
    duration_seconds: float = 10.5,
    total_tokens: int = 1000,
    prompt_tokens: int = 600,
    completion_tokens: int = 400,
    summary_json: str = (
        '{"review_summary": {"verdict": "approved", "score": 85, '
        '"retry_count": 1, "issue_count": 3, "critical_count": 0}, '
        '"test_summary": {"status": "passed", "total": 10, "passed": 8, "failed": 2}}'
    ),
) -> SimpleNamespace:
    """构造 mock TaskRecord，字段对齐 app/db/models.py 的 TaskRecord。"""
    return SimpleNamespace(
        task_id=task_id,
        status=status,
        phase=phase,
        error_message=error_message,
        duration_seconds=duration_seconds,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        summary_json=summary_json,
        started_at=datetime.now(),
        finished_at=datetime.now(),
    )


def make_mock_stage_log(
    task_id: str = "test-001",
    stage: str = "code_gen",
    error: str = "",
    duration_ms: int = 5000,
    total_tokens: int = 800,
    prompt_tokens: int = 500,
    completion_tokens: int = 300,
) -> SimpleNamespace:
    """构造 mock StageLog，字段对齐 app/db/models.py 的 StageLog。"""
    return SimpleNamespace(
        task_id=task_id,
        stage=stage,
        error=error,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        created_at=datetime.now(),
    )


def make_metrics(
    task_id: str = "t1",
    success: bool = True,
    duration_seconds: float = 10.0,
    total_tokens: int = 1000,
    stages: Optional[List[Dict[str, Any]]] = None,
    failed_stages: Optional[List[str]] = None,
    review_verdict: str = "approved",
    review_score: int = 80,
    review_retry_count: int = 0,
    review_issue_count: int = 0,
    review_critical_count: int = 0,
    test_status: str = "passed",
    test_total: int = 10,
    test_passed: int = 10,
    test_failed: int = 0,
    test_pass_rate: float = 1.0,
    golden_total: int = 0,
    golden_passed: int = 0,
    golden_pass_rate: float = 0.0,
    golden_gate: str = "SKIP",
    self_healed: bool = False,
    heal_attempts: int = 0,
) -> TaskMetrics:
    """直接构造 TaskMetrics 对象，用于 compute_eval_report 的聚合测试。"""
    if stages is None:
        stages = []
    if failed_stages is None:
        failed_stages = []
    return TaskMetrics(
        task_id=task_id,
        success=success,
        duration_seconds=duration_seconds,
        total_tokens=total_tokens,
        stages=stages,
        stage_count=len(stages),
        failed_stages=failed_stages,
        review_verdict=review_verdict,
        review_score=review_score,
        review_retry_count=review_retry_count,
        review_issue_count=review_issue_count,
        review_critical_count=review_critical_count,
        test_status=test_status,
        test_total=test_total,
        test_passed=test_passed,
        test_failed=test_failed,
        test_pass_rate=test_pass_rate,
        golden_total=golden_total,
        golden_passed=golden_passed,
        golden_pass_rate=golden_pass_rate,
        golden_gate=golden_gate,
        self_healed=self_healed,
        heal_attempts=heal_attempts,
    )


def make_mock_db(
    get_task_return: Any = None,
    list_tasks_return: Optional[List[Any]] = None,
    list_stage_logs_return: Optional[List[Any]] = None,
    stat_return: Optional[Dict[str, Any]] = None,
) -> MagicMock:
    """构造 mock DB 客户端，模拟 app/db/sqlite_client.py 的 SqliteClient 接口。

    默认所有方法返回空数据，确保 Evaluator 在空数据场景下稳定运行。
    """
    db = MagicMock()
    db.get_task.return_value = get_task_return
    db.list_tasks.return_value = list_tasks_return or []
    # list_stage_logs 默认返回空列表，调用方可覆盖
    db.list_stage_logs.return_value = list_stage_logs_return or []
    db.stat.return_value = stat_return or {
        "total_tasks": 0,
        "total_tokens": 0,
        "total_files": 0,
    }
    return db


# ====================================================================== #
#  compute_task_metrics 测试
# ====================================================================== #

def test_compute_task_metrics_success() -> None:
    """正常成功任务：验证 success=True、review_verdict、test_pass_rate。"""
    task = make_mock_task()
    stages = [make_mock_stage_log(stage="code_gen"), make_mock_stage_log(stage="review")]

    m = compute_task_metrics(task, stages)

    assert m.task_id == "test-001"
    # status=="done" 且 error_message 为空 → success=True
    assert m.success is True
    assert m.review_verdict == "approved"
    assert m.review_score == 85
    assert m.review_retry_count == 1
    assert m.review_issue_count == 3
    assert m.review_critical_count == 0
    # 测试指标：8 passed / 10 total = 0.8
    assert m.test_status == "passed"
    assert m.test_total == 10
    assert m.test_passed == 8
    assert m.test_failed == 2
    assert m.test_pass_rate == pytest.approx(0.8)
    # 阶段指标
    assert m.stage_count == 2
    assert m.failed_stages == []
    # 自愈指标：retry_count=1 且 success=True → self_healed=True
    assert m.self_healed is True
    assert m.heal_attempts == 1


def test_compute_task_metrics_failed() -> None:
    """失败任务：status=failed 且有 error_message，验证 success=False。"""
    task = make_mock_task(
        task_id="test-fail",
        status="failed",
        error_message="代码评审未通过",
        summary_json='{"review_summary": {"verdict": "rejected", "score": 30}}',
    )
    stages = [make_mock_stage_log(stage="review", error="评审失败")]

    m = compute_task_metrics(task, stages)

    assert m.success is False
    assert m.review_verdict == "rejected"
    assert m.review_score == 30
    # 失败任务不自愈
    assert m.self_healed is False
    # 失败阶段被记录
    assert m.failed_stages == ["review"]


def test_compute_task_metrics_self_healed() -> None:
    """自愈任务：retry_count=2 但最终成功，验证 self_healed=True、heal_attempts=2。"""
    task = make_mock_task(
        task_id="test-heal",
        status="done",
        error_message="",
        summary_json=(
            '{"review_summary": {"verdict": "approved", "score": 90, '
            '"retry_count": 2, "issue_count": 1}}'
        ),
    )
    stages = [
        make_mock_stage_log(stage="code_gen"),
        make_mock_stage_log(stage="review"),
        make_mock_stage_log(stage="retry"),
        make_mock_stage_log(stage="review"),
    ]

    m = compute_task_metrics(task, stages)

    assert m.success is True
    assert m.review_retry_count == 2
    # 有重试且最终成功 → 自愈
    assert m.self_healed is True
    assert m.heal_attempts == 2


def test_compute_task_metrics_no_review() -> None:
    """summary_json 无 review_summary：验证 review_verdict="" 、review_score=0。"""
    task = make_mock_task(
        summary_json='{"test_summary": {"status": "passed", "total": 5, "passed": 5}}',
    )

    m = compute_task_metrics(task, [])

    assert m.review_verdict == ""
    assert m.review_score == 0
    assert m.review_retry_count == 0
    assert m.review_issue_count == 0
    assert m.review_critical_count == 0


def test_compute_task_metrics_no_test() -> None:
    """summary_json 无 test_summary：验证 test_status="" 、test_pass_rate=0。"""
    task = make_mock_task(
        summary_json='{"review_summary": {"verdict": "approved", "score": 88}}',
    )

    m = compute_task_metrics(task, [])

    assert m.test_status == ""
    assert m.test_total == 0
    assert m.test_passed == 0
    assert m.test_failed == 0
    # test_pass_rate = passed / max(total, 1) = 0 / 1 = 0.0
    assert m.test_pass_rate == 0.0


def test_compute_task_metrics_hidden_eval() -> None:
    """summary_json 含 hidden_eval：验证 golden 指标与门禁判定。"""
    task = make_mock_task(
        summary_json=(
            '{"review_summary": {"verdict": "approved", "score": 85}, '
            '"hidden_eval": {"golden_total": 5, "golden_passed": 4}}'
        ),
    )

    m = compute_task_metrics(task, [])

    assert m.golden_total == 5
    assert m.golden_passed == 4
    # 4/5 = 0.8 >= 0.8 → PASS
    assert m.golden_pass_rate == pytest.approx(0.8)
    assert m.golden_gate == "PASS"


def test_compute_task_metrics_hidden_eval_no_golden() -> None:
    """summary_json 无 hidden_eval：golden 指标为 0，门禁为 SKIP。"""
    task = make_mock_task()

    m = compute_task_metrics(task, [])

    assert m.golden_total == 0
    assert m.golden_passed == 0
    assert m.golden_pass_rate == 0.0
    assert m.golden_gate == "SKIP"


def test_compute_task_metrics_hidden_eval_block() -> None:
    """golden_pass_rate < 0.5 → 门禁 BLOCK。"""
    task = make_mock_task(
        summary_json='{"hidden_eval": {"golden_total": 4, "golden_passed": 1}}',
    )

    m = compute_task_metrics(task, [])

    assert m.golden_pass_rate == pytest.approx(0.25)
    assert m.golden_gate == "BLOCK"


def test_compute_task_metrics_invalid_json() -> None:
    """summary_json 为非法 JSON：验证不抛异常，返回默认值。"""
    task = make_mock_task(summary_json="这不是合法的 JSON {{{")

    # 不应抛异常
    m = compute_task_metrics(task, [])

    assert m.review_verdict == ""
    assert m.review_score == 0
    assert m.test_status == ""
    assert m.test_pass_rate == 0.0
    # 任务本身 status=done 且无 error → 仍判定成功
    assert m.success is True


def test_compute_task_metrics_stage_errors() -> None:
    """stage_logs 含 error：验证 failed_stages 包含对应阶段名。"""
    task = make_mock_task()
    stages = [
        make_mock_stage_log(stage="code_gen", error=""),
        make_mock_stage_log(stage="review", error="评审超时"),
        make_mock_stage_log(stage="test_gen", error="测试生成失败"),
    ]

    m = compute_task_metrics(task, stages)

    assert m.stage_count == 3
    # 只有有 error 的阶段进入 failed_stages
    assert "code_gen" not in m.failed_stages
    assert "review" in m.failed_stages
    assert "test_gen" in m.failed_stages
    assert len(m.failed_stages) == 2
    # stages 列表中的 has_error 标记正确
    assert m.stages[0]["has_error"] is False
    assert m.stages[1]["has_error"] is True
    assert m.stages[2]["has_error"] is True


def test_compute_task_metrics_empty_stages() -> None:
    """stage_logs 为空列表：验证 stage_count=0、failed_stages=[]。"""
    task = make_mock_task()

    m = compute_task_metrics(task, [])

    assert m.stage_count == 0
    assert m.failed_stages == []
    assert m.stages == []


# ====================================================================== #
#  compute_eval_report 测试
# ====================================================================== #

def test_compute_eval_report_empty() -> None:
    """空列表：验证 total_tasks=0，所有聚合率为 0。"""
    report = compute_eval_report([])

    assert isinstance(report, EvalReport)
    assert report.total_tasks == 0
    assert report.evaluated_tasks == 0
    # 空列表时 n=max(0,1)=1，分子全为 0
    assert report.success_rate == 0.0
    assert report.self_heal_rate == 0.0
    assert report.review_pass_rate == 0.0
    assert report.test_pass_rate == 0.0
    assert report.avg_review_score == 0.0
    assert report.avg_tokens == 0.0
    assert report.avg_duration_seconds == 0.0
    assert report.stage_error_rate == {}
    assert report.task_metrics == []
    # failure_rate = 1 - success_rate = 1.0（空集的补集）
    assert report.failure_rate == 1.0


def test_compute_eval_report_mixed() -> None:
    """混合成功/失败任务（3 个任务，2 成功 1 失败）：验证 success_rate=2/3。"""
    metrics_list = [
        make_metrics(task_id="t1", success=True, review_verdict="approved", review_score=80),
        make_metrics(task_id="t2", success=True, review_verdict="approved", review_score=90),
        make_metrics(
            task_id="t3",
            success=False,
            review_verdict="rejected",
            review_score=40,
        ),
    ]

    report = compute_eval_report(metrics_list)

    assert report.total_tasks == 3
    assert report.evaluated_tasks == 3
    # 2 成功 / 3 总数
    assert report.success_rate == pytest.approx(2 / 3)
    assert report.failure_rate == pytest.approx(1 / 3)
    # 2 个 approved / 3
    assert report.review_pass_rate == pytest.approx(2 / 3)
    # avg_review_score 仅统计 review_score>0 的任务：(80+90+40)/3
    assert report.avg_review_score == pytest.approx((80 + 90 + 40) / 3)


def test_compute_eval_report_self_heal() -> None:
    """包含自愈任务：验证 self_heal_rate 正确。"""
    metrics_list = [
        make_metrics(task_id="t1", success=True, self_healed=False, heal_attempts=0),
        make_metrics(
            task_id="t2",
            success=True,
            self_healed=True,
            heal_attempts=2,
            review_retry_count=2,
        ),
        make_metrics(
            task_id="t3",
            success=False,
            self_healed=False,
            heal_attempts=0,
        ),
    ]

    report = compute_eval_report(metrics_list)

    # 1 个自愈 / 3 总数
    assert report.self_heal_rate == pytest.approx(1 / 3)
    # 平均重试次数 = (0+2+0)/3
    assert report.avg_heal_attempts == pytest.approx(2 / 3)


def test_compute_eval_report_stage_error_rate() -> None:
    """多任务多阶段错误：验证 stage_error_rate dict 正确聚合。"""
    # 构造带 stages 明细的 metrics
    metrics_list = [
        make_metrics(
            task_id="t1",
            stages=[
                {"stage": "code_gen", "duration_ms": 100, "tokens": 100, "has_error": False},
                {"stage": "review", "duration_ms": 200, "tokens": 200, "has_error": True},
            ],
        ),
        make_metrics(
            task_id="t2",
            stages=[
                {"stage": "code_gen", "duration_ms": 100, "tokens": 100, "has_error": True},
                {"stage": "review", "duration_ms": 200, "tokens": 200, "has_error": True},
            ],
        ),
        make_metrics(
            task_id="t3",
            stages=[
                {"stage": "code_gen", "duration_ms": 100, "tokens": 100, "has_error": False},
                {"stage": "review", "duration_ms": 200, "tokens": 200, "has_error": False},
            ],
        ),
    ]

    report = compute_eval_report(metrics_list)

    # code_gen: 3 次出现，1 次错误 → 1/3
    assert report.stage_error_rate["code_gen"] == pytest.approx(1 / 3)
    # review: 3 次出现，2 次错误 → 2/3
    assert report.stage_error_rate["review"] == pytest.approx(2 / 3)
    # 只包含出现过的阶段
    assert set(report.stage_error_rate.keys()) == {"code_gen", "review"}


def test_compute_eval_report_golden() -> None:
    """隐藏测试指标聚合：avg_golden_pass_rate 与 golden_gate_counts。"""
    metrics_list = [
        make_metrics(
            task_id="t1", golden_total=5, golden_passed=5,
            golden_pass_rate=1.0, golden_gate="PASS",
        ),
        make_metrics(
            task_id="t2", golden_total=4, golden_passed=3,
            golden_pass_rate=0.75, golden_gate="WARN",
        ),
        make_metrics(
            task_id="t3", golden_total=2, golden_passed=0,
            golden_pass_rate=0.0, golden_gate="BLOCK",
        ),
        make_metrics(task_id="t4"),  # 无黄金用例 → SKIP
    ]

    report = compute_eval_report(metrics_list)

    # avg_golden_pass_rate 仅统计有黄金用例的任务：(1.0 + 0.75 + 0.0) / 3
    assert report.avg_golden_pass_rate == pytest.approx((1.0 + 0.75) / 3)
    # golden_pass_rate = PASS 数 / 总数 = 1/4
    assert report.golden_pass_rate == pytest.approx(1 / 4)
    assert report.golden_gate_counts == {"PASS": 1, "WARN": 1, "BLOCK": 1, "SKIP": 1}


def test_evaluate_gate_thresholds() -> None:
    from app.eval.metrics import evaluate_gate

    assert evaluate_gate(1.0) == "PASS"
    assert evaluate_gate(0.8) == "PASS"      # 边界：>= 0.8 → PASS
    assert evaluate_gate(0.79) == "WARN"
    assert evaluate_gate(0.5) == "WARN"      # 边界：>= 0.5 → WARN
    assert evaluate_gate(0.49) == "BLOCK"
    assert evaluate_gate(0.0) == "BLOCK"


# ====================================================================== #
#  Evaluator 测试（注入 mock DB，不依赖真实数据）
# ====================================================================== #

def test_evaluator_instance() -> None:
    """Evaluator 可正常实例化（注入 mock DB 避免触发真实 sqlite 初始化）。"""
    mock_db = make_mock_db()
    ev = Evaluator(db_client=mock_db)
    assert ev is not None
    # 全局单例也应存在且为 Evaluator 类型
    assert isinstance(global_evaluator, Evaluator)


def test_evaluator_get_summary() -> None:
    """get_summary() 返回 dict 含 total_tasks / total_tokens / total_files。"""
    mock_db = make_mock_db(
        stat_return={"total_tasks": 5, "total_tokens": 1234, "total_files": 7},
    )
    ev = Evaluator(db_client=mock_db)

    summary = ev.get_summary()

    assert isinstance(summary, dict)
    assert summary["total_tasks"] == 5
    assert summary["total_tokens"] == 1234
    assert summary["total_files"] == 7


def test_evaluator_evaluate_task_not_found() -> None:
    """evaluate_task 对不存在的 task_id 返回 None。"""
    mock_db = make_mock_db(get_task_return=None)
    ev = Evaluator(db_client=mock_db)

    result = ev.evaluate_task("nonexistent-task-id")

    assert result is None
    mock_db.get_task.assert_called_once_with("nonexistent-task-id")


def test_evaluator_evaluate_batch_empty() -> None:
    """evaluate_batch 在无任务时返回 EvalReport（total_tasks=0）。"""
    mock_db = make_mock_db(list_tasks_return=[])
    ev = Evaluator(db_client=mock_db)

    report = ev.evaluate_batch()

    assert isinstance(report, EvalReport)
    assert report.total_tasks == 0
    assert report.evaluated_tasks == 0


def test_evaluator_evaluate_recent_empty() -> None:
    """evaluate_recent 在无任务时返回 EvalReport（total_tasks=0）。"""
    mock_db = make_mock_db(list_tasks_return=[])
    ev = Evaluator(db_client=mock_db)

    report = ev.evaluate_recent()

    assert isinstance(report, EvalReport)
    assert report.total_tasks == 0
    assert report.evaluated_tasks == 0


# ====================================================================== #
#  API 端点测试（使用 conftest 提供的 TestClient fixture）
# ====================================================================== #

def test_eval_api_summary(client: TestClient) -> None:
    """GET /api/v1/eval/summary 返回 200 且含必需字段。"""
    resp = client.get("/api/v1/eval/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert "total_tasks" in body
    assert "total_tokens" in body
    assert "total_files" in body


def test_eval_api_batch(client: TestClient) -> None:
    """GET /api/v1/eval/batch 返回 200 且为聚合报告结构。"""
    resp = client.get("/api/v1/eval/batch")

    assert resp.status_code == 200
    body = resp.json()
    assert "total_tasks" in body
    assert "success_rate" in body
    assert "stage_error_rate" in body
    assert "avg_golden_pass_rate" in body
    assert "golden_pass_rate" in body
    assert "golden_gate_counts" in body


def test_eval_api_recent(client: TestClient) -> None:
    """GET /api/v1/eval/recent 返回 200 且为聚合报告结构。"""
    resp = client.get("/api/v1/eval/recent")

    assert resp.status_code == 200
    body = resp.json()
    assert "total_tasks" in body
    assert "success_rate" in body


def test_eval_api_task_not_found(client: TestClient) -> None:
    """GET /api/v1/eval/task/nonexistent 返回 404。"""
    resp = client.get("/api/v1/eval/task/nonexistent-eval-task-xyz")

    assert resp.status_code == 404

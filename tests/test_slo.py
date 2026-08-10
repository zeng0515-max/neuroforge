from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.slo import SLOConfig, evaluate_slo
from app.main import app


def test_slo_reports_not_measured_for_empty_metrics() -> None:
    report = evaluate_slo({}, SLOConfig())
    assert report["overall"] == "not_measured"
    assert report["checks"]["success_rate"]["measured"] is False
    assert report["checks"]["p95_latency"]["measured"] is False
    assert report["checks"]["tool_failure_rate"]["measured"] is False


def test_slo_ok_when_within_targets() -> None:
    snapshot = {
        "pipeline": {
            "sample_size": 10,
            "success_rate": 0.995,
            "p95_duration_seconds": 12.0,
        },
        "tool": {"total_calls": 10, "failure_rate": 0.0},
    }
    report = evaluate_slo(snapshot, SLOConfig())
    assert report["overall"] == "ok"
    assert report["breached_checks"] == []


def test_slo_breached_when_metrics_degrade() -> None:
    snapshot = {
        "pipeline": {
            "sample_size": 10,
            "success_rate": 0.9,
            "p95_duration_seconds": 120.0,
        },
        "tool": {"total_calls": 10, "failure_rate": 0.3},
    }
    report = evaluate_slo(snapshot, SLOConfig())
    assert report["overall"] == "breached"
    assert set(report["breached_checks"]) == {
        "success_rate",
        "p95_latency",
        "tool_failure_rate",
    }


def test_slo_endpoint_returns_json() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/slo")
        assert response.status_code == 200
        assert "overall" in response.json()
        assert "checks" in response.json()

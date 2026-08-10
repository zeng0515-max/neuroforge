"""SLO evaluation helpers backed by the metrics snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class SLOConfig:
    success_target: float = 0.99
    p95_latency_target_seconds: float = 30.0
    tool_failure_rate_target: float = 0.01


def evaluate_slo(snapshot: Dict[str, Any], config: SLOConfig = SLOConfig()) -> Dict[str, Any]:
    """Evaluate current metrics against targets without inventing data.

    A service with zero samples is reported as ``not_measured`` instead of a
    fake 100% pass, which keeps the gate honest in CI and local runs.
    """
    pipeline = snapshot.get("pipeline", {})
    tool = snapshot.get("tool", {})
    sample_size = int(pipeline.get("sample_size", 0) or 0)

    checks: Dict[str, Any] = {}
    if sample_size == 0:
        checks["success_rate"] = {
            "measured": False,
            "status": "not_measured",
            "value": None,
            "target": config.success_target,
        }
        checks["p95_latency"] = {
            "measured": False,
            "status": "not_measured",
            "value": None,
            "target": config.p95_latency_target_seconds,
        }
    else:
        success_rate = float(pipeline.get("success_rate", 0.0))
        checks["success_rate"] = {
            "measured": True,
            "status": "ok" if success_rate >= config.success_target else "breached",
            "value": success_rate,
            "target": config.success_target,
        }
        p95 = float(pipeline.get("p95_duration_seconds", 0.0))
        checks["p95_latency"] = {
            "measured": True,
            "status": "ok" if p95 <= config.p95_latency_target_seconds else "breached",
            "value": p95,
            "target": config.p95_latency_target_seconds,
        }

    tool_failures = int(tool.get("total_calls", 0))
    if tool_failures == 0:
        checks["tool_failure_rate"] = {
            "measured": False,
            "status": "not_measured",
            "value": None,
            "target": config.tool_failure_rate_target,
        }
    else:
        failure_rate = float(tool.get("failure_rate", 0.0))
        checks["tool_failure_rate"] = {
            "measured": True,
            "status": "ok" if failure_rate <= config.tool_failure_rate_target else "breached",
            "value": failure_rate,
            "target": config.tool_failure_rate_target,
        }

    measured = [c for c in checks.values() if c["measured"]]
    breached = [name for name, c in checks.items() if c["status"] == "breached"]
    all_ok = bool(measured) and not breached

    return {
        "overall": "ok" if all_ok else ("not_measured" if not measured else "breached"),
        "sample_size": sample_size,
        "checks": checks,
        "breached_checks": breached,
    }


__all__ = ["SLOConfig", "evaluate_slo"]

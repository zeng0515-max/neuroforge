from __future__ import annotations

import pytest

from app.core.audit import ActorType, AuditEventType, AuditLogger
from app.graph.state import ReviewIssue, ReviewReport, ReviewSeverity
from app.tools.tool_registry import _check_write_blacklist


def test_audit_entry_coerces_string_enum_values() -> None:
    logger = AuditLogger(persist_to_db=False)
    entry = logger.log(
        event_type="state_transition",
        actor_type="system",
        actor_id="memory",
        action="memory_write",
        input_data={"key": "x"},
    )
    assert entry.event_type == AuditEventType.STATE_TRANSITION
    assert entry.actor_type == ActorType.SYSTEM
    assert entry.to_dict()["event_type"] == "state_transition"
    assert entry.to_dict()["actor_type"] == "system"


def test_review_severity_label_uses_enum_value() -> None:
    issue = ReviewIssue(severity=ReviewSeverity.LOW, message="x")
    assert issue.severity_label == "low"
    assert ReviewReport(issues=[issue]).critical_errors == []
    critical = ReviewIssue(severity=ReviewSeverity.CRITICAL, message="x")
    assert critical.severity_label == "critical"
    assert ReviewReport(issues=[critical]).critical_errors == [critical]


def test_write_blacklist_allows_generated_python_under_src() -> None:
    _check_write_blacklist("src/generated_module.py")
    _check_write_blacklist("generated/fib.py")
    with pytest.raises(ValueError):
        _check_write_blacklist("main.py")
    with pytest.raises(ValueError):
        _check_write_blacklist("app/core/config.py")

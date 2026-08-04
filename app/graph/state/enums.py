from __future__ import annotations

from enum import Enum


class PipelinePhase(str, Enum):
    """流水线各阶段标识。"""

    PENDING = "pending"
    REQUIREMENT = "requirement"
    CODE_GEN = "code_gen"
    REVIEW = "review"
    CODE_REVIEW = "review"  # alias
    TEST_GEN = "test_gen"
    SUPERVISOR = "supervisor"
    HITL = "hitl"
    DONE = "done"
    FAILED = "failed"


class ReviewVerdict(str, Enum):
    """代码评审结论。"""

    APPROVED = "approved"
    NEEDS_CHANGES = "needs_changes"
    NEED_FIX = "need_fix"  # alias, 兼容旧写法
    REJECTED = "rejected"


class ReviewSeverity(str, Enum):
    """评审问题严重级别。"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TestStatus(str, Enum):
    """测试报告状态。"""

    NOT_RUN = "not_run"
    GENERATED = "generated"
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"


__all__ = [
    "PipelinePhase",
    "ReviewVerdict",
    "ReviewSeverity",
    "TestStatus",
]
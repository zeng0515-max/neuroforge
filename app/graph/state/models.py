from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.graph.state.enums import PipelinePhase, ReviewSeverity, ReviewVerdict, TestStatus


class TestCase(BaseModel):
    """单条测试用例（结构化）。"""

    name: str = ""
    description: str = ""
    target_file: str = ""
    target_function: str = ""
    priority: str = "medium"  # high / medium / low
    status: str = "generated"  # generated / passed / failed / skipped / error
    error_message: str = ""
    output: str = ""
    duration_ms: int = 0
    code_snippet: str = ""


class RequirementSpec(BaseModel):
    """需求分析产物：结构化需求说明。"""

    title: str = Field(default="", description="需求标题")
    goal: str = Field(default="", description="核心目标 / 用户故事")
    functional_requirements: List[str] = Field(default_factory=list)
    non_functional_requirements: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    raw_llm_output: str = Field(default="", exclude=True)


class GeneratedFile(BaseModel):
    """单个代码文件产物。"""

    path: str = Field(..., description="相对项目根目录的文件路径")
    language: str = Field(default="python")
    content: str = Field(default="", description="完整文件内容")
    description: str = Field(default="")

    @property
    def line_count(self) -> int:
        return self.content.count("\n") + 1 if self.content else 0


class CodePackage(BaseModel):
    """代码生成阶段的完整产物。"""

    files: List[GeneratedFile] = Field(default_factory=list)
    entry_point: str = Field(default="", description="启动/入口文件相对路径")
    install_steps: List[str] = Field(default_factory=list)
    raw_llm_output: str = Field(default="", exclude=True)

    def as_dict(self) -> Dict[str, str]:
        return {f.path: f.content for f in self.files}


class ReviewIssue(BaseModel):
    """代码评审中的单条问题。同时兼容 file/line/rule 旧字段与 file_path/line_start/rule_id/message/suggestion 新字段。"""

    severity: Any = "warning"  # info / warning / error / critical 或 ReviewSeverity 枚举
    file: str = ""
    line: Optional[int] = None
    rule: str = ""
    message: str = ""
    suggestion: str = ""
    # 新字段
    file_path: str = ""
    line_start: int = 0
    rule_id: str = ""

    def __init__(self, **data: Any) -> None:  # type: ignore[override]
        # 新旧字段双写，确保后续使用任一字段都有值
        file_path = data.get("file_path") or data.get("file") or ""
        line = data.get("line_start") or data.get("line") or 0
        rule_id = data.get("rule_id") or data.get("rule") or ""
        data.setdefault("file", file_path)
        data.setdefault("file_path", file_path)
        data.setdefault("line", int(line) if line else None)
        data.setdefault("line_start", int(line) if line else 0)
        data.setdefault("rule", rule_id)
        data.setdefault("rule_id", rule_id)
        super().__init__(**data)

    @property
    def severity_label(self) -> str:
        return str(self.severity).lower()


class ReviewReport(BaseModel):
    """代码评审报告。"""

    verdict: ReviewVerdict = ReviewVerdict.APPROVED
    summary: str = ""
    issues: List[ReviewIssue] = Field(default_factory=list)
    score: int = Field(default=100, ge=0, le=100)  # 0~100 综合评分
    retry_count: int = 0
    coverage_check: Dict[str, Any] = Field(default_factory=dict)
    raw_llm_output: str = Field(default="", exclude=True)

    @property
    def need_fix(self) -> bool:
        return self.verdict in (ReviewVerdict.NEED_FIX, ReviewVerdict.NEEDS_CHANGES)

    @property
    def critical_errors(self) -> List[ReviewIssue]:
        sev_set = {"error", "critical", ReviewSeverity.CRITICAL.value, ReviewSeverity.HIGH.value}
        return [i for i in self.issues if str(i.severity).lower() in sev_set]


class TestReport(BaseModel):
    """测试生成与执行报告。支持 old（test_files + total_tests）与 new（status + test_cases + run_command）双字段。"""

    status: TestStatus = TestStatus.NOT_RUN
    summary: str = ""
    test_cases: List[TestCase] = Field(default_factory=list)
    test_files: List[Any] = Field(default_factory=list)  # 兼容 GeneratedFile 或 dict 形式
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error_count: int = 0
    duration_ms: int = 0
    run_command: str = ""
    # 旧字段别名（为了兼容保留）
    total_tests: int = 0
    raw_llm_output: str = Field(default="", exclude=True)

    def __init__(self, **data: Any) -> None:  # type: ignore[override]
        total = int(data.get("total") or data.get("total_tests") or data.get("total_tests") or 0)
        data.setdefault("total", total)
        data.setdefault("total_tests", total)
        # 若给了 test_files 是 dict 列表，先保持原样，让上层按需转换
        super().__init__(**data)

    @property
    def total_tests_count(self) -> int:
        return self.total or self.total_tests or len(self.test_cases)


class PipelineStatus(BaseModel):
    """流水线运行时统计信息。"""

    phase: PipelinePhase = PipelinePhase.PENDING
    started_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    error_message: str = ""
    verify_attempts: int = 0
    """执行验证自愈已尝试的修复轮次（供评估环节使用）"""
    verify_healed: bool = False
    """执行验证自愈是否最终修复成功（供评估环节使用）"""

    def touch(self) -> None:
        self.updated_at = datetime.now()

    def record_usage(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion


__all__ = [
    "TestCase",
    "RequirementSpec",
    "GeneratedFile",
    "CodePackage",
    "ReviewIssue",
    "ReviewReport",
    "TestReport",
    "PipelineStatus",
]
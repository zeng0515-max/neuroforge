"""LLM 驱动的审查逻辑：结构化输出模型、系统提示词、评审编排。

本模块提供基于大语言模型的代码审查能力：
- 结构化输出 Pydantic 模型（ReviewIssueOutput / ReviewReportOutput / CoverageCheckOutput）
- 审查系统提示词与 Prompt 构建
- JSON Schema 校验与结构化报告构建
- LLM 评审编排函数
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.graph.state import (
    CodePackage,
    GeneratedFile,
    RequirementSpec,
    ReviewIssue,
    ReviewReport,
    ReviewSeverity,
    ReviewVerdict,
)

logger = logging.getLogger(__name__)


# ====================================================================== #
#  结构化审查输出模型（供 chat_structured 使用）
# ====================================================================== #

class ReviewIssueOutput(BaseModel):
    """LLM 输出的单条审查问题。"""

    file_path: str = Field(..., description="问题文件路径")
    line_start: int = Field(default=0, description="问题起始行号")
    severity: str = Field(
        ..., description="严重等级",
        pattern="^(critical|high|medium|low|info|warning|error)$",
    )
    rule_id: str = Field(..., description="规则 ID")
    message: str = Field(..., description="问题描述")
    suggestion: str = Field(default="", description="修复建议")
    dimension: str = Field(
        ..., description="审查维度",
        pattern="^(quality|security|performance|compatibility)$",
    )


class CoverageCheckOutput(BaseModel):
    """LLM 输出的需求覆盖检查。"""

    covered: List[str] = Field(default_factory=list, description="已覆盖的需求")
    missing: List[str] = Field(default_factory=list, description="未覆盖的需求")


class ReviewReportOutput(BaseModel):
    """LLM 输出的结构化审查报告。"""

    verdict: str = Field(
        ..., description="评审结论",
        pattern="^(approved|needs_changes|rejected|need_fix)$",
    )
    summary: str = Field(..., description="评审总结")
    issues: List[ReviewIssueOutput] = Field(default_factory=list, description="问题列表")
    dimension_scores: Dict[str, int] = Field(
        default_factory=dict, description="各维度评分（0-100）"
    )
    coverage_check: CoverageCheckOutput = Field(
        default_factory=CoverageCheckOutput, description="需求覆盖检查"
    )
    fix_suggestions: List[Dict[str, Any]] = Field(
        default_factory=list, description="修复建议列表"
    )


# ====================================================================== #
#  审查系统提示词
# ====================================================================== #

REVIEW_SYSTEM_PROMPT = (
    "你是专业的代码评审专家，擅长从以下四个维度进行审查：\n"
    "1. **代码质量（quality）**：命名规范、函数复杂度、注释文档、类型注解\n"
    "2. **安全性（security）**：注入攻击、路径穿越、敏感信息泄露、危险函数调用\n"
    "3. **性能（performance）**：算法复杂度、资源管理、并发安全\n"
    "4. **兼容性（compatibility）**：Python 版本兼容性、弃用 API、依赖版本\n\n"
    "请严格按照以下要求输出 JSON 格式的结构化评审报告：\n"
    "- verdict: approved（通过）/ needs_changes（需修改）/ rejected（拒绝）\n"
    "- severity: critical / high / medium / low\n"
    "- 必须为每个问题提供具体的文件路径、行号、规则 ID 和修复建议"
)


def build_review_prompt(
    req: RequirementSpec,
    pkg: CodePackage,
    dimension_scores: Optional[Dict[str, int]] = None,
) -> str:
    """构建发送给 LLM 的审查 Prompt。

    Args:
        req: 需求规格说明
        pkg: 代码包
        dimension_scores: 本地规则引擎已计算的维度评分（可选）

    Returns:
        格式化的审查 Prompt 字符串
    """
    parts: List[str] = []

    if req.title or req.goal:
        parts.append(f"## 需求目标\n{req.title or req.goal}")

    if req.functional_requirements:
        parts.append("## 功能需求")
        for i, fr in enumerate(req.functional_requirements, 1):
            parts.append(f"  {i}. {fr}")

    if dimension_scores:
        parts.append("## 本地规则引擎评分（供参考）")
        for dim, score in dimension_scores.items():
            parts.append(f"  - {dim}: {score}/100")

    parts.append("## 待审查代码")
    for f in pkg.files:
        content = f.content or ""
        preview = content[:2000]
        if len(content) > 2000:
            preview += f"\n\n# ... 省略 {len(content) - 2000} 字符"
        parts.append(f"\n### 文件: {f.path}\n```python\n{preview}\n```")

    parts.append(
        "\n请从 quality / security / performance / compatibility 四个维度进行审查，"
        "输出结构化 JSON 报告。"
    )

    return "\n".join(parts)


# ====================================================================== #
#  结构化审查报告 JSON Schema 与校验
# ====================================================================== #

REVIEW_REPORT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "StructuredReviewReport",
    "type": "object",
    "required": ["verdict", "summary", "issues", "dimension_scores"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["approved", "needs_changes", "rejected", "need_fix"],
        },
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file_path", "severity", "rule_id", "message"],
                "properties": {
                    "file_path": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info", "warning", "error"],
                    },
                    "rule_id": {"type": "string"},
                    "message": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "dimension": {
                        "type": "string",
                        "enum": ["quality", "security", "performance", "compatibility"],
                    },
                    "auto_fix": {"type": ["string", "object", "null"]},
                },
            },
        },
        "dimension_scores": {
            "type": "object",
            "properties": {
                "quality": {"type": "integer", "minimum": 0, "maximum": 100},
                "security": {"type": "integer", "minimum": 0, "maximum": 100},
                "performance": {"type": "integer", "minimum": 0, "maximum": 100},
                "compatibility": {"type": "integer", "minimum": 0, "maximum": 100},
            },
        },
        "coverage_check": {"type": "object"},
        "fix_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue_rule_id": {"type": "string"},
                    "file_path": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "original_code": {"type": "string"},
                    "fixed_code": {"type": "string"},
                    "explanation": {"type": "string"},
                },
            },
        },
    },
}


def validate_review_report(report: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """验证审查报告是否符合 JSON Schema。

    Args:
        report: 审查报告字典

    Returns:
        (是否通过验证, 错误信息列表)
    """
    errors: List[str] = []

    def _validate(node: Any, schema: Any, path: str = "") -> None:
        if not isinstance(schema, dict):
            return
        schema_type = schema.get("type")
        if schema_type == "object":
            if not isinstance(node, dict):
                errors.append(f"{path}: expected object, got {type(node).__name__}")
                return
            required = schema.get("required", [])
            for r in required:
                if r not in node:
                    errors.append(f"{path}: missing required field '{r}'")
            props = schema.get("properties", {})
            for k, v in node.items():
                if k in props:
                    _validate(v, props[k], f"{path}.{k}")
        elif schema_type == "array":
            if not isinstance(node, list):
                errors.append(f"{path}: expected array, got {type(node).__name__}")
                return
            items_schema = schema.get("items", {})
            for i, item in enumerate(node):
                _validate(item, items_schema, f"{path}[{i}]")
        elif schema_type == "string":
            if not isinstance(node, str):
                errors.append(f"{path}: expected string, got {type(node).__name__}")
            enum = schema.get("enum")
            if enum and node not in enum:
                errors.append(f"{path}: value '{node}' not in enum {enum}")
        elif schema_type == "integer":
            if not isinstance(node, int):
                errors.append(f"{path}: expected integer, got {type(node).__name__}")
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if minimum is not None and node < minimum:
                errors.append(f"{path}: {node} < minimum {minimum}")
            if maximum is not None and node > maximum:
                errors.append(f"{path}: {node} > maximum {maximum}")

    _validate(report, REVIEW_REPORT_SCHEMA)
    return (len(errors) == 0, errors)


# ====================================================================== #
#  维度推断（与 review_rules._infer_dimension 保持一致）
# ====================================================================== #

def _infer_dimension(issue: ReviewIssue) -> str:
    """根据 rule_id 推断审查维度。"""
    rule_id = (issue.rule_id or "").upper()
    security_rules = ("AST-FORBIDDEN", "AST-PATH", "DANGEROUS", "SENSITIVE",
                      "SQL-INJECTION", "PATH-TRAVERSAL", "INSECURE-DESERIALIZATION")
    performance_rules = ("LONG-FUNCTION", "HIGH-COMPLEXITY", "DOUBLE-SORT",
                         "RESOURCE-LEAK", "CONCURRENCY")
    compat_rules = ("PY2-", "DEPRECATED-IMPORT")

    if any(r in rule_id for r in security_rules):
        return "security"
    if any(r in rule_id for r in performance_rules):
        return "performance"
    if any(r in rule_id for r in compat_rules):
        return "compatibility"
    return "quality"


# ====================================================================== #
#  结构化报告构建
# ====================================================================== #

def build_structured_report(
    report: ReviewReport,
    dimension_scores: Dict[str, int],
    fix_suggestions: List[Any],
) -> Dict[str, Any]:
    """构建符合 JSON Schema 的结构化报告。

    Args:
        report: 审查报告领域模型
        dimension_scores: 各维度评分
        fix_suggestions: 修复建议列表

    Returns:
        符合 REVIEW_REPORT_SCHEMA 的字典
    """
    return {
        "verdict": report.verdict.value,
        "summary": report.summary,
        "issues": [
            {
                "file_path": i.file_path,
                "line_start": i.line_start,
                "severity": (
                    i.severity.value
                    if hasattr(i.severity, "value")
                    else str(i.severity).lower()
                ),
                "rule_id": i.rule_id,
                "message": i.message,
                "suggestion": i.suggestion,
                "dimension": getattr(i, "dimension", None) or _infer_dimension(i),
            }
            for i in report.issues
        ],
        "dimension_scores": dimension_scores,
        "coverage_check": report.coverage_check,
        "fix_suggestions": [
            s.model_dump() for s in fix_suggestions
        ],
    }


# ====================================================================== #
#  LLM 评审编排
# ====================================================================== #

def run_llm_review(
    llm_adapter: Any,
    req: RequirementSpec,
    pkg: CodePackage,
    dimension_scores: Optional[Dict[str, int]] = None,
) -> Optional[ReviewReportOutput]:
    """通过 LLM 执行结构化代码评审。

    使用 chat_structured 获取结构化评审报告，失败时返回 None 供调用方回退。

    Args:
        llm_adapter: LLM 适配器实例
        req: 需求规格说明
        pkg: 代码包
        dimension_scores: 本地规则引擎已计算的维度评分

    Returns:
        ReviewReportOutput 或 None（LLM 不可用时）
    """
    prompt = build_review_prompt(req, pkg, dimension_scores)

    try:
        messages = llm_adapter.to_messages(
            system=REVIEW_SYSTEM_PROMPT,
            user=prompt,
        )
        result, _ = llm_adapter.chat_structured(
            messages,
            response_model=ReviewReportOutput,
            use_code_model=True,
        )
        if result is not None:
            logger.info(
                "LLM 评审完成：verdict=%s, issues=%d",
                result.verdict, len(result.issues),
            )
        return result
    except Exception as e:
        logger.warning("LLM 评审失败: %s", e)
        return None


__all__ = [
    "ReviewIssueOutput",
    "ReviewReportOutput",
    "CoverageCheckOutput",
    "REVIEW_SYSTEM_PROMPT",
    "build_review_prompt",
    "REVIEW_REPORT_SCHEMA",
    "validate_review_report",
    "build_structured_report",
    "run_llm_review",
]

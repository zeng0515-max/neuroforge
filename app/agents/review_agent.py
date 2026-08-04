from __future__ import annotations

import ast
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.db.sqlite_client import db
from app.graph.state import (
    CodePackage,
    GeneratedFile,
    PipelinePhase,
    PipelineState,
    RequirementSpec,
    ReviewIssue,
    ReviewReport,
    ReviewSeverity,
    ReviewVerdict,
    ensure_status,
)
from app.tools.ast_validator import validate_python_source
from app.tools.code_executor import execute_code

logger = logging.getLogger(__name__)


class ReviewAgent:
    """代码评审 Agent：纯 AST + 执行验证，0 Token 消耗。"""

    # --------------------------------------------------------------
    #  本地静态 AST 规则
    # --------------------------------------------------------------
    @staticmethod
    def _static_ast_scan(pkg: CodePackage) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for f in pkg.files:
            # 空文件检测
            if not f.content or not f.content.strip():
                issues.append(ReviewIssue(
                    file_path=f.path,
                    line_start=0,
                    severity=ReviewSeverity.CRITICAL,
                    rule_id="EMPTY-FILE",
                    message=f"文件 {f.path} 内容为空",
                    suggestion="重新生成代码，确保文件包含实际内容。",
                ))
                continue
            if not f.path.endswith(".py"):
                continue
            r = validate_python_source(f.content)
            for v in r.errors:
                severity = ReviewSeverity.CRITICAL if v.severity == "error" and v.rule in (
                    "forbidden_node", "forbidden_import", "path_traversal"
                ) else (ReviewSeverity.HIGH if v.severity == "error" else ReviewSeverity.LOW)
                issues.append(ReviewIssue(
                    file_path=f.path,
                    line_start=v.line,
                    severity=severity,
                    rule_id=f"AST-{v.rule.upper()}",
                    message=v.message,
                    suggestion=f"移除或替换第 {v.line} 行的违规代码。",
                ))
            for v in r.warnings:
                issues.append(ReviewIssue(
                    file_path=f.path,
                    line_start=v.line,
                    severity=ReviewSeverity.MEDIUM,
                    rule_id=f"AST-{v.rule.upper()}",
                    message=v.message,
                    suggestion="按最佳实践修正。",
                ))
        return issues

    # --------------------------------------------------------------
    #  AST 质量检查（补充 AST validator 没覆盖的规则）
    # --------------------------------------------------------------
    @staticmethod
    def _quality_scan(pkg: CodePackage) -> List[ReviewIssue]:
        """检查代码质量：函数无 docstring、过长函数、无类型注解等。"""
        issues: List[ReviewIssue] = []
        for f in pkg.files:
            if not f.path.endswith(".py") or not f.content.strip():
                continue
            try:
                tree = ast.parse(f.content)
            except SyntaxError:
                continue  # 语法错误已被 _static_ast_scan 捕获

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # 检查 docstring
                    docstring = ast.get_docstring(node)
                    if not docstring:
                        issues.append(ReviewIssue(
                            file_path=f.path,
                            line_start=node.lineno,
                            severity=ReviewSeverity.LOW,
                            rule_id="NO-DOCSTRING",
                            message=f"函数 {node.name} 缺少 docstring",
                            suggestion="添加函数文档字符串。",
                        ))
                    # 检查函数过长（>50 行）
                    end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
                    func_lines = end_line - node.lineno
                    if func_lines > 50:
                        issues.append(ReviewIssue(
                            file_path=f.path,
                            line_start=node.lineno,
                            severity=ReviewSeverity.MEDIUM,
                            rule_id="LONG-FUNCTION",
                            message=f"函数 {node.name} 过长（{func_lines} 行）",
                            suggestion="考虑拆分为更小的函数。",
                        ))
                    # 检查参数无类型注解
                    args = node.args
                    unannotated = [
                        a.arg for a in args.args
                        if a.annotation is None and a.arg != "self"
                    ]
                    if unannotated:
                        issues.append(ReviewIssue(
                            file_path=f.path,
                            line_start=node.lineno,
                            severity=ReviewSeverity.LOW,
                            rule_id="NO-TYPE-HINT",
                            message=f"函数 {node.name} 参数缺少类型注解: {', '.join(unannotated)}",
                            suggestion="添加参数类型注解提升可读性。",
                        ))
        return issues

    # --------------------------------------------------------------
    #  执行验证（调用 code_executor）
    # --------------------------------------------------------------
    @staticmethod
    def _exec_scan(pkg: CodePackage) -> List[ReviewIssue]:
        """执行代码验证：语法检查 + 导入测试。"""
        issues: List[ReviewIssue] = []
        files = [
            {"path": f.path, "content": f.content}
            for f in pkg.files if f.path.endswith(".py") and f.content.strip()
        ]
        if not files:
            return issues

        try:
            result = execute_code(files, timeout=15, run_entry=False)
            for r in result.results:
                if not r.syntax_ok:
                    issues.append(ReviewIssue(
                        file_path=r.path,
                        line_start=0,
                        severity=ReviewSeverity.CRITICAL,
                        rule_id="SYNTAX-ERROR",
                        message=f"语法错误: {r.syntax_error}",
                        suggestion="修复语法错误。",
                    ))
                elif not r.import_ok:
                    issues.append(ReviewIssue(
                        file_path=r.path,
                        line_start=0,
                        severity=ReviewSeverity.HIGH,
                        rule_id="IMPORT-ERROR",
                        message=f"导入失败: {r.import_error[:200]}",
                        suggestion="检查依赖和导入路径。",
                    ))
        except Exception as e:
            logger.warning("执行验证异常: %s", e)

        return issues

    # --------------------------------------------------------------
    #  主入口（0 Token）
    # --------------------------------------------------------------
    def run(self, state: PipelineState) -> ReviewReport:
        task_id = state.get("task_id", "")
        from app.graph.state import normalize_state
        state = normalize_state(state)
        req: RequirementSpec = state.get("requirement") or RequirementSpec()
        pkg: CodePackage = state.get("code_package") or CodePackage()
        if not pkg.files:
            return ReviewReport(
                verdict=ReviewVerdict.NEEDS_CHANGES,
                summary="代码包为空，无法评审。",
                issues=[],
                coverage_check={"covered": [], "missing": list(req.functional_requirements)},
            )

        t0 = time.perf_counter()

        # 1. AST 安全扫描
        ast_issues = self._static_ast_scan(pkg)

        # 2. 代码质量扫描
        quality_issues = self._quality_scan(pkg)

        # 3. 执行验证（语法 + 导入）
        exec_issues = self._exec_scan(pkg)

        # 4. 合并所有问题
        all_issues = ast_issues + exec_issues + quality_issues

        # 5. 判定 verdict
        has_critical = any(i.severity == ReviewSeverity.CRITICAL for i in all_issues)
        has_high = any(i.severity == ReviewSeverity.HIGH for i in all_issues)
        if has_critical:
            verdict = ReviewVerdict.REJECTED
        elif has_high:
            verdict = ReviewVerdict.NEEDS_CHANGES
        else:
            verdict = ReviewVerdict.APPROVED

        duration_ms = int((time.perf_counter() - t0) * 1000)

        # 统计
        n_critical = sum(1 for i in all_issues if i.severity == ReviewSeverity.CRITICAL)
        n_high = sum(1 for i in all_issues if i.severity == ReviewSeverity.HIGH)
        n_medium = sum(1 for i in all_issues if i.severity == ReviewSeverity.MEDIUM)
        n_low = sum(1 for i in all_issues if i.severity == ReviewSeverity.LOW)

        summary = (
            f"纯本地评审完成（0 Token）：共 {len(all_issues)} 个问题，"
            f"{n_critical} critical / {n_high} high / {n_medium} medium / {n_low} low。"
            f"耗时 {duration_ms}ms。"
        )

        # 记录日志（0 Token）
        db.add_stage_log(
            task_id=task_id,
            stage=PipelinePhase.CODE_REVIEW.value,
            prompt_text=req.title or req.goal,
            llm_output=f"[纯本地评审] verdict={verdict.value}, issues={len(all_issues)}",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            duration_ms=duration_ms,
        )
        db.update_task_phase(
            task_id,
            phase=PipelinePhase.CODE_REVIEW.value,
        )

        report = ReviewReport(
            verdict=verdict,
            summary=summary,
            issues=all_issues,
            coverage_check={
                "covered": [f.path for f in pkg.files],
                "missing": [] if verdict == ReviewVerdict.APPROVED else ["修复发现的问题"],
            },
        )
        logger.info("评审完成（本地）：%s，问题数=%d，0 Token", report.verdict.value, len(report.issues))
        return report


# ------------------------------------------------------------------ #
#  LangGraph 节点函数
# ------------------------------------------------------------------ #

def review_node(state: PipelineState) -> Dict[str, Any]:
    """LangGraph 节点：对 code_package 做评审。

    方案A：当 verdict=needs_changes 时，把 issues 作为 review_feedback 写入 state，
    供 code_gen 重试时针对性修复（而非盲目重跑）。
    """
    task_id = state.get("task_id", "")
    agent = ReviewAgent()
    report = agent.run(state)

    db.update_task_phase(
        task_id,
        phase=PipelinePhase.CODE_REVIEW.value,
        summary={"verdict": report.verdict.value, "issues": len(report.issues)},
    )
    status = ensure_status(state)
    status.phase = PipelinePhase.CODE_REVIEW
    status.touch()

    # 方案A：评审失败时携带 feedback，让 code_gen 重试有方向
    patch: Dict[str, Any] = {
        "review_report": report,
        "status": status,
    }
    if report.verdict == ReviewVerdict.NEEDS_CHANGES and report.issues:
        # 控制反馈数量上限，避免 prompt 膨胀
        # 优先级：critical > high > medium > low，最多保留 8 条
        prioritized = sorted(
            report.issues,
            key=lambda i: {
                ReviewSeverity.CRITICAL: 0,
                ReviewSeverity.HIGH: 1,
                ReviewSeverity.MEDIUM: 2,
                ReviewSeverity.LOW: 3,
            }.get(i.severity, 4),
        )
        patch["review_feedback"] = prioritized[:8]
        logger.info(
            "评审未通过，已写入 %d 条 feedback 供 code_gen 重试参考（共 %d 条 issues）",
            len(patch["review_feedback"]), len(report.issues),
        )
    else:
        # 评审通过或 rejected 时清空 feedback，避免残留影响后续
        patch["review_feedback"] = []
    return patch


__all__ = [
    "ReviewAgent",
    "review_node",
    "ReviewReport",
    "ReviewIssue",
    "ReviewSeverity",
    "ReviewVerdict",
]

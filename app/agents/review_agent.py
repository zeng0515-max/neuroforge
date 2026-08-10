"""代码评审 Agent：多维度审查 + 并行扫描 + LLM 结构化输出 + 自动修复建议。

本模块为评审系统的主入口，整合：
- review_rules.py: 规则引擎、AST 扫描器、评分与修复建议
- review_llm.py: LLM 结构化评审、系统提示词、Schema 校验

向后兼容：所有原有 import 路径和符号均保持不变。
"""

from __future__ import annotations

import ast
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from app.agents.agent_protocol import (
    AgentCapability,
    AgentProfile,
    AgentRole,
    AgentStatus,
)
from app.agents.review_llm import (
    CoverageCheckOutput,
    REVIEW_REPORT_SCHEMA,
    REVIEW_SYSTEM_PROMPT,
    ReviewIssueOutput,
    ReviewReportOutput,
    build_review_prompt,
    build_structured_report,
    run_llm_review,
    validate_review_report,
)
from app.agents.review_rules import (
    SCANNER_REGISTRY,
    BaseReviewScanner,
    CodeQualityScanner,
    CompatibilityScanner,
    FixSuggestion,
    ReviewDimension,
    REVIEW_DIMENSIONS,
    SecurityScanner,
    PerformanceScanner,
    compute_dimension_scores,
    generate_fix_suggestions,
)
from app.core.llm_adapter import llm
from app.core.memory_system import MemoryFacade, memory_system
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
    normalize_state,
)
from app.tools.ast_validator import validate_python_source
from app.tools.code_executor import execute_code

logger = logging.getLogger(__name__)


class ReviewAgent:
    """代码评审 Agent：多维度审查 + 并行扫描 + LLM 结构化输出 + 自动修复建议。

    审查维度：
    - quality:      代码质量（命名规范、复杂度、注释、类型注解）
    - security:     安全审查（注入攻击、路径穿越、敏感信息、危险函数）
    - performance:  性能审查（算法复杂度、资源管理、并发安全）
    - compatibility: 兼容性审查（Python 2/3、弃用 API、依赖版本）

    特性：
    - 支持并行审查（多文件同时扫描）
    - LLM 结构化评审（chat_structured）与纯本地规则双轨运行
    - JSON Schema 结构化报告验证
    - 自动修复建议生成
    - 记忆系统集成（审查经验沉淀）
    """

    agent_profile: AgentProfile = AgentProfile(
        name="review_agent",
        role=AgentRole.REVIEWER,
        capabilities=[
            AgentCapability.CODE_REVIEW,
            AgentCapability.ANALYSIS,
            AgentCapability.MEMORY_ACCESS,
        ],
        system_prompt="你是专业的代码评审专家，擅长从代码质量、安全性、性能和兼容性四个维度进行审查。",
        description="多维度代码评审 Agent，支持质量/安全/性能/兼容性审查",
    )

    def __init__(
        self,
        dimensions: Optional[List[str]] = None,
        max_workers: int = 4,
        enable_memory: bool = True,
        use_llm: bool = True,
    ) -> None:
        """初始化评审 Agent。

        Args:
            dimensions: 需要审查的维度列表，默认全维度
            max_workers: 并行审查最大线程数
            enable_memory: 是否启用记忆系统集成
            use_llm: 是否启用 LLM 评审（默认 True）
        """
        self.dimensions = dimensions or list(REVIEW_DIMENSIONS)
        self.max_workers = max(1, max_workers)
        self.enable_memory = enable_memory
        self.use_llm = use_llm
        self._memory: Optional[MemoryFacade] = memory_system if enable_memory else None

    # --------------------------------------------------------------
    #  本地静态 AST 规则（保持向后兼容）
    # --------------------------------------------------------------
    @staticmethod
    def _static_ast_scan(pkg: CodePackage) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for f in pkg.files:
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

    @staticmethod
    def _quality_scan(pkg: CodePackage) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for f in pkg.files:
            if not f.path.endswith(".py") or not f.content.strip():
                continue
            try:
                tree = ast.parse(f.content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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

    @staticmethod
    def _exec_scan(pkg: CodePackage) -> List[ReviewIssue]:
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
    #  多维度并行扫描
    # --------------------------------------------------------------
    def _run_parallel_scanners(self, pkg: CodePackage) -> List[ReviewIssue]:
        """并行运行所有维度的扫描器。"""
        all_issues: List[ReviewIssue] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures: Dict[str, Any] = {}
            for dim in self.dimensions:
                scanner_cls = SCANNER_REGISTRY.get(dim)
                if scanner_cls is None:
                    continue
                future = executor.submit(scanner_cls.scan, pkg)
                futures[dim] = future

            for dim, future in futures.items():
                try:
                    issues = future.result(timeout=30)
                    for issue in issues:
                        if not hasattr(issue, "dimension"):
                            object.__setattr__(issue, "dimension", dim)
                    all_issues.extend(issues)
                except Exception as e:
                    logger.warning("维度 %s 扫描失败: %s", dim, e)

        return all_issues

    # --------------------------------------------------------------
    #  LLM 评审（chat_structured 方案）
    # --------------------------------------------------------------
    def _run_llm_review(
        self,
        req: RequirementSpec,
        pkg: CodePackage,
        dimension_scores: Dict[str, int],
    ) -> Optional[ReviewReportOutput]:
        """通过 chat_structured 执行 LLM 评审。

        失败时返回 None，由调用方决定是否回退到纯规则评审。
        """
        return run_llm_review(llm, req, pkg, dimension_scores)

    # --------------------------------------------------------------
    #  主入口
    # --------------------------------------------------------------
    def run(self, state: PipelineState) -> ReviewReport:
        task_id = state.get("task_id", "")
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

        # 1. AST 静态安全扫描
        ast_issues = self._static_ast_scan(pkg)

        # 2. 多维度并行扫描
        dimension_issues = self._run_parallel_scanners(pkg)

        # 3. 执行验证
        exec_issues = self._exec_scan(pkg)

        # 4. 合并所有问题
        all_issues = ast_issues + dimension_issues + exec_issues

        # 5. 计算维度评分
        dimension_scores = compute_dimension_scores(all_issues, len(pkg.files))

        # 6. 生成自动修复建议
        fix_suggestions = generate_fix_suggestions(all_issues, pkg)

        # 7. LLM 结构化评审（可选）
        llm_report: Optional[ReviewReportOutput] = None
        if self.use_llm:
            llm_report = self._run_llm_review(req, pkg, dimension_scores)

        # 8. 判定 verdict
        has_critical = any(i.severity == ReviewSeverity.CRITICAL for i in all_issues)
        has_high = any(i.severity == ReviewSeverity.HIGH for i in all_issues)

        if llm_report is not None:
            verdict = _parse_verdict(llm_report.verdict)
            if llm_report.dimension_scores:
                for k, v in llm_report.dimension_scores.items():
                    if k in dimension_scores:
                        dimension_scores[k] = max(dimension_scores[k], v)
            if llm_report.issues:
                llm_issues = _convert_llm_issues(llm_report.issues)
                existing_rules = {(i.file_path, i.rule_id) for i in all_issues}
                for li in llm_issues:
                    if (li.file_path, li.rule_id) not in existing_rules:
                        all_issues.append(li)
        else:
            if has_critical:
                verdict = ReviewVerdict.REJECTED
            elif has_high:
                verdict = ReviewVerdict.NEEDS_CHANGES
            else:
                verdict = ReviewVerdict.APPROVED

        duration_ms = int((time.perf_counter() - t0) * 1000)

        n_critical = sum(1 for i in all_issues if i.severity == ReviewSeverity.CRITICAL)
        n_high = sum(1 for i in all_issues if i.severity == ReviewSeverity.HIGH)
        n_medium = sum(1 for i in all_issues if i.severity == ReviewSeverity.MEDIUM)
        n_low = sum(1 for i in all_issues if i.severity == ReviewSeverity.LOW)

        llm_info = f"LLM={'yes' if llm_report else 'no'}"
        summary = (
            f"多维度评审完成：共 {len(all_issues)} 个问题，"
            f"{n_critical} critical / {n_high} high / {n_medium} medium / {n_low} low。"
            f"维度评分：{', '.join(f'{k}={v}' for k, v in dimension_scores.items())}。"
            f"修复建议 {len(fix_suggestions)} 条。{llm_info}。耗时 {duration_ms}ms。"
        )

        db.add_stage_log(
            task_id=task_id,
            stage=PipelinePhase.CODE_REVIEW.value,
            prompt_text=req.title or req.goal,
            llm_output=f"[多维度评审] verdict={verdict.value}, issues={len(all_issues)}, "
                       f"fix_suggestions={len(fix_suggestions)}",
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

        if self._memory:
            try:
                self._memory.write(
                    "episodic",
                    "milestone",
                    {
                        "content": f"代码评审完成: verdict={verdict.value}, issues={len(all_issues)}",
                        "task_id": task_id,
                        "n_critical": n_critical,
                        "n_high": n_high,
                        "dimension_scores": dimension_scores,
                        "quality_score": self._compute_overall_score(dimension_scores),
                    },
                )
                if verdict != ReviewVerdict.REJECTED and not has_critical:
                    self._memory.write(
                        "semantic",
                        f"评审通过的代码包: {req.title or req.goal}",
                        metadata={
                            "source": "review_agent",
                            "task_id": task_id,
                            "verdict": verdict.value,
                            "score": self._compute_overall_score(dimension_scores),
                        },
                        quality_score=self._compute_overall_score(dimension_scores),
                    )
            except Exception as e:
                logger.warning("记忆系统写入失败（不影响主流程）: %s", e)

        structured = build_structured_report(report, dimension_scores, fix_suggestions)
        is_valid, schema_errors = validate_review_report(structured)
        if not is_valid:
            logger.warning("审查报告 JSON Schema 验证失败: %s", schema_errors)

        logger.info(
            "多维度评审完成：%s，问题数=%d，修复建议=%d，llm=%s，耗时=%dms",
            report.verdict.value, len(report.issues), len(fix_suggestions),
            llm_report is not None, duration_ms,
        )
        return report

    @staticmethod
    def _compute_overall_score(dimension_scores: Dict[str, int]) -> int:
        """计算总体质量分。"""
        if not dimension_scores:
            return 100
        return int(sum(dimension_scores.values()) / len(dimension_scores))


# ====================================================================== #
#  辅助函数
# ====================================================================== #

def _parse_verdict(raw: str) -> ReviewVerdict:
    """将 LLM 输出的 verdict 字符串转换为 ReviewVerdict 枚举。"""
    mapping = {
        "approved": ReviewVerdict.APPROVED,
        "needs_changes": ReviewVerdict.NEEDS_CHANGES,
        "need_fix": ReviewVerdict.NEEDS_CHANGES,
        "rejected": ReviewVerdict.REJECTED,
    }
    return mapping.get(raw, ReviewVerdict.NEEDS_CHANGES)


def _convert_llm_issues(issues: List[ReviewIssueOutput]) -> List[ReviewIssue]:
    """将 LLM 输出的 ReviewIssueOutput 转换为 ReviewIssue。"""
    severity_map = {
        "critical": ReviewSeverity.CRITICAL,
        "high": ReviewSeverity.HIGH,
        "medium": ReviewSeverity.MEDIUM,
        "low": ReviewSeverity.LOW,
    }
    result: List[ReviewIssue] = []
    for li in issues:
        issue = ReviewIssue(
            file_path=li.file_path,
            line_start=li.line_start,
            severity=severity_map.get(li.severity, ReviewSeverity.MEDIUM),
            rule_id=li.rule_id,
            message=li.message,
            suggestion=li.suggestion,
        )
        object.__setattr__(issue, "dimension", li.dimension)
        result.append(issue)
    return result


# ====================================================================== #
#  LangGraph 节点函数
# ====================================================================== #

def review_node(state: PipelineState) -> Dict[str, Any]:
    """LangGraph 节点：对 code_package 做多维度评审。

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

    patch: Dict[str, Any] = {
        "review_report": report,
        "status": status,
    }
    if report.verdict == ReviewVerdict.NEEDS_CHANGES and report.issues:
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
        patch["review_feedback"] = []
    return patch


# ====================================================================== #
#  向后兼容：所有公有符号重新导出
# ====================================================================== #

__all__ = [
    "ReviewAgent",
    "review_node",
    "ReviewReport",
    "ReviewIssue",
    "ReviewSeverity",
    "ReviewVerdict",
    "ReviewDimension",
    "REVIEW_DIMENSIONS",
    "BaseReviewScanner",
    "CodeQualityScanner",
    "SecurityScanner",
    "PerformanceScanner",
    "CompatibilityScanner",
    "FixSuggestion",
    "generate_fix_suggestions",
    "compute_dimension_scores",
    "validate_review_report",
    "REVIEW_REPORT_SCHEMA",
    "SCANNER_REGISTRY",
    "AgentProfile",
    "AgentRole",
    "AgentCapability",
    "ReviewReportOutput",
    "ReviewIssueOutput",
    "CoverageCheckOutput",
    "REVIEW_SYSTEM_PROMPT",
    "build_review_prompt",
    "build_structured_report",
    "run_llm_review",
]
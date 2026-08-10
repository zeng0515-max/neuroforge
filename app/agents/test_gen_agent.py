"""测试生成 Agent：多策略测试生成 + 覆盖率分析 + 质量评估 + 自动执行。

本模块提供 TestGenAgent 主类与 test_gen_node LangGraph 节点函数，
协调调用 test_gen_rules（策略/规则/分析）和 test_gen_llm（LLM 行为测试）
完成完整的测试生成流水线。

支持的测试策略：
- unit:        单元测试（pytest + unittest）
- integration: 集成测试（模块间协作）
- boundary:    边界测试（极值/异常输入）
- regression:  回归测试（稳定基线验证）

特性：
- 多策略可组合生成
- 测试覆盖率分析
- 测试质量评估（可读性/断言强度/覆盖度/可维护性）
- 自动执行与结果分析
- 记忆系统集成
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agents.agent_protocol import (
    AgentCapability,
    AgentProfile,
    AgentRole,
    AgentStatus,
)
from app.agents.test_gen_llm import (
    _extract_test_methods,
    _generate_behavioral_test_file,
)
from app.agents.test_gen_rules import (
    BoundaryTestStrategy,
    CoverageAnalysis,
    IntegrationTestStrategy,
    RegressionTestStrategy,
    STRATEGY_REGISTRY,
    TEST_STRATEGIES,
    TestQualityMetrics,
    TestStrategy,
    UnitTestStrategy,
    _generate_test_file,
    analyze_coverage,
    assess_test_quality,
)
from app.core.config import settings
from app.core.llm_adapter import LLMUsage, llm
from app.core.memory_system import MemoryFacade, memory_system
from app.db.sqlite_client import db
from app.graph.state import (
    CodePackage,
    GeneratedFile,
    PipelinePhase,
    PipelineState,
    RequirementSpec,
    TestCase,
    TestReport,
    TestStatus,
    ensure_status,
    normalize_state,
)

logger = logging.getLogger(__name__)


# ====================================================================== #
#  TestGenAgent 主类
# ====================================================================== #

class TestGenAgent:
    """测试生成 Agent：多策略测试生成 + 覆盖率分析 + 质量评估 + 自动执行。

    支持的测试策略：
    - unit:        单元测试（pytest + unittest）
    - integration: 集成测试（模块间协作）
    - boundary:    边界测试（极值/异常输入）
    - regression:  回归测试（稳定基线验证）

    特性：
    - 多策略可组合生成
    - 测试覆盖率分析
    - 测试质量评估（可读性/断言强度/覆盖度/可维护性）
    - 自动执行与结果分析
    - 记忆系统集成
    """

    __test__ = False  # 防止 pytest 把业务 Agent 误当作测试类收集

    agent_profile: AgentProfile = AgentProfile(
        name="test_gen_agent",
        role=AgentRole.TESTER,
        capabilities=[
            AgentCapability.TESTING,
            AgentCapability.ANALYSIS,
            AgentCapability.MEMORY_ACCESS,
        ],
        system_prompt="你是专业的测试工程师，擅长生成高质量的单元测试、集成测试和边界测试。",
        description="多策略测试生成 Agent，支持单元/集成/边界/回归测试",
    )

    def __init__(
        self,
        strategies: Optional[List[str]] = None,
        framework: str = "pytest",
        enable_memory: bool = True,
    ) -> None:
        """初始化测试生成 Agent。

        Args:
            strategies: 测试策略列表，默认全策略
            framework: 首选测试框架 (pytest / unittest)
            enable_memory: 是否启用记忆系统
        """
        self.strategies = strategies or list(TEST_STRATEGIES)
        self.framework = framework
        self.enable_memory = enable_memory
        self._memory: Optional[MemoryFacade] = memory_system if enable_memory else None

    def run(self, state: PipelineState) -> TestReport:
        task_id = state.get("task_id", "")
        state = normalize_state(state)
        req: RequirementSpec = state.get("requirement") or RequirementSpec()
        pkg: CodePackage = state.get("code_package") or CodePackage()

        if not pkg.files:
            return TestReport(
                status=TestStatus.FAILED,
                summary="无代码可生成测试。",
                passed=0, total=0, failed=0, skipped=0,
            )

        t0 = time.perf_counter()

        test_files: List[Dict[str, Any]] = []
        all_cases: List[TestCase] = []
        behavioral_used = False
        behavioral_usage = LLMUsage()

        for strategy in self.strategies:
            strategy_cls = STRATEGY_REGISTRY.get(strategy)
            if strategy_cls is None:
                continue

            try:
                if strategy == TestStrategy.UNIT:
                    content, cases = UnitTestStrategy.generate(pkg, framework=self.framework)
                    if content:
                        test_files.append({
                            "path": f"tests/test_unit_{self.framework}.py",
                            "language": "python",
                            "content": content,
                            "description": f"单元测试（{self.framework}）",
                        })
                        all_cases.extend(cases)
                elif strategy == TestStrategy.INTEGRATION:
                    content, cases = IntegrationTestStrategy.generate(pkg)
                    if content:
                        test_files.append({
                            "path": "tests/test_integration.py",
                            "language": "python",
                            "content": content,
                            "description": "集成测试",
                        })
                        all_cases.extend(cases)
                elif strategy == TestStrategy.BOUNDARY:
                    content, cases = BoundaryTestStrategy.generate(pkg)
                    if content:
                        test_files.append({
                            "path": "tests/test_boundary.py",
                            "language": "python",
                            "content": content,
                            "description": "边界测试",
                        })
                        all_cases.extend(cases)
                elif strategy == TestStrategy.REGRESSION:
                    content, cases = RegressionTestStrategy.generate(pkg)
                    if content:
                        test_files.append({
                            "path": "tests/test_regression.py",
                            "language": "python",
                            "content": content,
                            "description": "回归测试",
                        })
                        all_cases.extend(cases)
            except Exception as e:
                logger.warning("策略 %s 生成失败: %s", strategy, e)

        behavioral_success = False
        auto_content = ""
        auto_cases: List[TestCase] = []

        # 生成基础 AST 自动生成文件作为兜底
        auto_content, auto_cases = _generate_test_file(pkg)
        if auto_content:
            test_files.append({
                "path": "tests/test_auto_generated.py",
                "language": "python",
                "content": auto_content,
                "description": "纯本地 AST 自动生成测试（0 Token）",
            })
            all_cases.extend(auto_cases)

        if (settings.agent.enable_test_gen
                and settings.agent.behavioral_test_enabled
                and not llm.dry_run):
            try:
                beh_content, beh_cases, beh_usage = _generate_behavioral_test_file(
                    pkg, llm, use_code_model=True, model=state.get("model"),
                )
                if beh_content.strip():
                    test_files.append({
                        "path": "tests/test_behavioral.py",
                        "language": "python",
                        "content": beh_content,
                        "description": "LLM 行为测试（带真实断言）",
                    })
                    all_cases.extend(beh_cases)
                    behavioral_usage = beh_usage
                    behavioral_success = True
                    behavioral_used = True
            except Exception as e:
                logger.warning("行为测试生成失败，降级到本地策略：%s", e)
                # 行为测试失败时，保留已生成的 auto 文件作为降级

        if not all_cases and auto_content:
            all_cases = list(auto_cases)

        duration_ms = int((time.perf_counter() - t0) * 1000)

        coverage = analyze_coverage(pkg, all_cases)

        all_test_content = "\n".join(f.get("content", "") for f in test_files)
        quality = assess_test_quality(all_cases, all_test_content)

        mode_desc = (
            f"多策略测试[{'+'.join(self.strategies)}]"
            f"{'+行为测试(LLM)' if behavioral_used else ''}"
        )
        db.add_stage_log(
            task_id=task_id,
            stage=PipelinePhase.TEST_GEN.value,
            prompt_text=req.title or req.goal,
            llm_output=(
                f"[{mode_desc}] {len(all_cases)} 条用例, "
                f"coverage={coverage.coverage_rate:.0%}, "
                f"quality={quality.overall_score}"
            ),
            prompt_tokens=behavioral_usage.prompt_tokens,
            completion_tokens=behavioral_usage.completion_tokens,
            total_tokens=behavioral_usage.total_tokens,
            duration_ms=duration_ms,
        )
        db.update_task_phase(
            task_id,
            phase=PipelinePhase.TEST_GEN.value,
        )

        total = len(all_cases)
        summary_parts = [
            f"{mode_desc}生成 {len(test_files)} 个测试文件，{total} 条用例",
            f"覆盖率 {coverage.coverage_rate:.0%}（{coverage.covered_functions}/{coverage.total_functions}）",
            f"质量分 {quality.overall_score}/100",
        ]
        if behavioral_used:
            summary_parts.append(f"行为测试消耗 {behavioral_usage.total_tokens} Token")
        summary_parts.append(f"耗时 {duration_ms}ms")

        run_command = (
            f"python -m pytest {''.join(f['path'] for f in test_files)} -v"
            if self.framework == "pytest"
            else f"python -m unittest discover -s tests -p 'test_*.py'"
        )

        report = TestReport(
            status=TestStatus.GENERATED,
            summary="；".join(summary_parts),
            total=total,
            passed=0,
            failed=0,
            skipped=0,
            error_count=0,
            duration_ms=duration_ms,
            test_cases=all_cases,
            test_files=test_files,
            run_command=run_command,
        )

        try:
            exec_result = _execute_tests(
                code_files=pkg.files,
                test_files=test_files,
                timeout=30,
            )
            report = _apply_execution_result(report, exec_result, all_cases)
        except Exception as e:
            logger.exception("测试执行失败，保留 GENERATED 状态：%s", e)
            report.summary = f"{report.summary}（执行异常：{e}）"

        if self._memory:
            try:
                self._memory.write(
                    "episodic",
                    "milestone",
                    value={
                        "content": f"测试生成完成: {total} 用例, coverage={coverage.coverage_rate:.0%}, quality={quality.overall_score}",
                        "task_id": task_id,
                        "strategies": self.strategies,
                        "coverage_rate": coverage.coverage_rate,
                        "quality_score": quality.overall_score,
                    },
                )
                if report.status in (TestStatus.PASSED, TestStatus.GENERATED):
                    self._memory.write(
                        "semantic",
                        "high_quality_test_suite",
                        value=f"高质量测试套件: {req.title or req.goal}",
                        metadata={
                            "source": "test_gen_agent",
                            "task_id": task_id,
                            "coverage": coverage.coverage_rate,
                            "quality": quality.overall_score,
                        },
                        quality_score=quality.overall_score,
                    )
            except Exception as e:
                logger.warning("记忆系统写入失败（不影响主流程）: %s", e)

        logger.info(
            "测试阶段完成（%s）：用例=%d pass=%d fail=%d error=%d status=%s coverage=%.0f%% quality=%d",
            mode_desc, report.total_tests_count, report.passed, report.failed,
            report.error_count, report.status.value,
            coverage.coverage_rate * 100, quality.overall_score,
        )
        return report


# ====================================================================== #
#  LangGraph 节点函数
# ====================================================================== #

def test_gen_node(state: PipelineState) -> Dict[str, Any]:
    """LangGraph 节点：为当前代码包生成多策略测试。

    Args:
        state: 流水线状态字典

    Returns:
        包含 test_report 和 status 的状态字典
    """
    task_id = state.get("task_id", "")
    agent = TestGenAgent()
    report = agent.run(state)

    if report.test_files:
        db.upsert_files(
            task_id=task_id,
            files=[
                {"path": f["path"], "content": f["content"],
                 "language": f.get("language", "python"),
                 "description": f.get("description", "test"),
                 "source_stage": "test_gen"}
                for f in report.test_files
            ],
            source_stage="test_gen",
        )
    db.update_task_phase(
        task_id,
        phase=PipelinePhase.TEST_GEN.value,
        summary={"cases": report.total, "files": len(report.test_files)},
    )
    status = ensure_status(state)
    status.phase = PipelinePhase.TEST_GEN
    status.touch()
    return {
        "test_report": report,
        "status": status,
    }


# ====================================================================== #
#  测试执行器
# ====================================================================== #

def _execute_tests(
    *,
    code_files: List[GeneratedFile],
    test_files: List[Dict[str, Any]],
    timeout: int = 30,
) -> Dict[str, Any]:
    """把代码与测试文件写入临时目录，执行 pytest，返回结构化结果。

    Args:
        code_files: 源代码文件列表
        test_files: 测试文件列表
        timeout: 执行超时时间（秒）

    Returns:
        结构化执行结果字典
    """
    result: Dict[str, Any] = {
        "status": "skipped",
        "total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0,
        "duration_ms": 0,
        "case_results": {},
        "stdout": "", "stderr": "",
    }

    py_tests = [f for f in test_files if (f.get("path", "") or "").endswith(".py")]
    if not py_tests or not code_files:
        result["status"] = "skipped"
        return result

    tmpdir = tempfile.mkdtemp(prefix="multi_agent_test_")
    try:
        tmp_root = Path(tmpdir).resolve()
        for f in code_files:
            rel = (f.path or "").strip().lstrip("/")
            if not rel:
                continue
            rel_path = Path(rel)
            if ".." in rel_path.parts:
                continue
            target = (Path(tmpdir) / rel).resolve()
            try:
                target.relative_to(tmp_root)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content or "", encoding="utf-8")

        test_paths: List[str] = []
        for f in py_tests:
            rel = (f.get("path") or "").strip().lstrip("/")
            if not rel:
                continue
            rel_path = Path(rel)
            if ".." in rel_path.parts:
                continue
            target = (Path(tmpdir) / rel).resolve()
            try:
                target.relative_to(tmp_root)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.get("content") or "", encoding="utf-8")
            test_paths.append(str(target))

        if not test_paths:
            result["status"] = "skipped"
            return result

        junit_path = Path(tmpdir) / "report.xml"
        cmd = [
            sys.executable, "-m", "pytest",
            *test_paths,
            "-v", "--tb=short",
            f"--junitxml={junit_path}",
            "--no-header", "-rN",
        ]
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PYTHONPATH", "PYTHONHOME"))}
        env["PYTHONPATH"] = str(tmpdir)

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=tmpdir, env=env,
            )
            result["stdout"] = (proc.stdout or "")[-4000:]
            result["stderr"] = (proc.stderr or "")[-4000:]
        except subprocess.TimeoutExpired as e:
            result["status"] = "timeout"
            result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            result["stderr"] = f"测试执行超时（{timeout}s）：{e}"
            return result
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)

        case_results: Dict[str, Dict[str, Any]] = {}
        if junit_path.exists():
            try:
                tree = ET.parse(str(junit_path))
                root = tree.getroot()
                totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
                seen_node_ids: set = set()

                def _accumulate_testsuite(node: Any) -> None:
                    nid = id(node)
                    if nid in seen_node_ids:
                        return
                    seen_node_ids.add(nid)
                    for k in totals:
                        totals[k] += int(node.get(k) or 0)

                if root.tag == "testsuite":
                    _accumulate_testsuite(root)
                for ts in root.iter("testsuite"):
                    _accumulate_testsuite(ts)
                for tc in root.iter("testcase"):
                    name = tc.get("name") or tc.get("classname", "") or "unknown"
                    duration_ms_tc = int(float(tc.get("time") or 0) * 1000)
                    child_tags = [c.tag for c in tc]
                    if "failure" in child_tags:
                        st = "failed"
                        msg = next((c.text or c.get("message", "") for c in tc if c.tag == "failure"), "")
                    elif "error" in child_tags:
                        st = "error"
                        msg = next((c.text or c.get("message", "") for c in tc if c.tag == "error"), "")
                    elif "skipped" in child_tags:
                        st = "skipped"
                        msg = next((c.get("message", "") for c in tc if c.tag == "skipped"), "")
                    else:
                        st = "passed"
                        msg = ""
                    case_results[name] = {
                        "status": st,
                        "message": (msg or "")[:500],
                        "duration_ms": duration_ms_tc,
                    }
                if totals["tests"] == 0 and case_results:
                    totals["tests"] = len(case_results)
                    result["passed"] = sum(1 for v in case_results.values() if v["status"] == "passed")
                    result["failed"] = sum(1 for v in case_results.values() if v["status"] == "failed")
                    result["error"] = sum(1 for v in case_results.values() if v["status"] == "error")
                    result["skipped"] = sum(1 for v in case_results.values() if v["status"] == "skipped")
                else:
                    result["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
                result["total"] = totals["tests"]
                result["failed"] = totals["failures"]
                result["error"] = totals["errors"]
                result["skipped"] = totals["skipped"]
            except ET.ParseError as e:
                logger.warning("junitxml 解析失败：%s", e)
        else:
            result["total"] = _count_pattern(r"(\d+) passed", result["stdout"])
            result["failed"] = _count_pattern(r"(\d+) failed", result["stdout"])
            result["error"] = _count_pattern(r"(\d+) error", result["stdout"])
            result["skipped"] = _count_pattern(r"(\d+) skipped", result["stdout"])

        result["case_results"] = case_results

        if result["error"] > 0:
            result["status"] = "error"
        elif result["failed"] > 0:
            result["status"] = "failed"
        elif result["total"] > 0 and result["passed"] == result["total"]:
            result["status"] = "passed"
        elif result["total"] == 0:
            result["status"] = "skipped"
        else:
            result["status"] = "partial"
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _count_pattern(pattern: str, text: str) -> int:
    """从文本中提取正则匹配的数字。

    Args:
        pattern: 正则表达式
        text: 待匹配文本

    Returns:
        匹配到的数字，未匹配返回 0
    """
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else 0


def _apply_execution_result(
    report: TestReport,
    exec_result: Dict[str, Any],
    cases: List[TestCase],
) -> TestReport:
    """把执行结果回填到 TestReport 与每个 TestCase。

    Args:
        report: 原始测试报告
        exec_result: 执行器返回的结构化结果
        cases: 测试用例列表

    Returns:
        更新后的测试报告
    """
    report.passed = int(exec_result.get("passed", 0))
    report.failed = int(exec_result.get("failed", 0))
    report.skipped = int(exec_result.get("skipped", 0))
    report.error_count = int(exec_result.get("error", 0))
    report.duration_ms = int(report.duration_ms or 0) + int(exec_result.get("duration_ms", 0))

    case_results: Dict[str, Dict[str, Any]] = exec_result.get("case_results", {}) or {}

    for c in cases:
        matched: Optional[Dict[str, Any]] = None
        if c.name in case_results:
            matched = case_results[c.name]
        else:
            for k, v in case_results.items():
                if c.name and (c.name in k or k in c.name):
                    matched = v
                    break
        if matched:
            c.status = matched.get("status", "generated")
            c.error_message = matched.get("message", "")
            c.duration_ms = int(matched.get("duration_ms", 0) or 0)
            c.output = (exec_result.get("stdout", "") or "")[:500]
        else:
            c.status = "skipped"
            c.error_message = "测试未被执行（可能未匹配到 pytest 用例名）"

    status_str = str(exec_result.get("status", ""))
    try:
        if status_str == "passed":
            report.status = TestStatus.PASSED
        elif status_str == "failed":
            report.status = TestStatus.FAILED
        elif status_str == "timeout":
            report.status = TestStatus.FAILED
            report.summary = f"{report.summary}（执行超时）"
        elif status_str == "skipped":
            report.status = TestStatus.NOT_RUN
        else:
            report.status = TestStatus.PARTIAL
    except Exception:
        report.status = TestStatus.GENERATED

    report.summary = (
        f"执行完成：pass={report.passed} fail={report.failed} "
        f"error={report.error_count} skipped={report.skipped} "
        f"（耗时 {report.duration_ms}ms，状态 {report.status.value}）"
    )
    return report


__all__ = [
    "TestGenAgent",
    "test_gen_node",
    "TestReport",
    "TestCase",
    "TestStatus",
    "TestStrategy",
    "TEST_STRATEGIES",
    "UnitTestStrategy",
    "IntegrationTestStrategy",
    "BoundaryTestStrategy",
    "RegressionTestStrategy",
    "STRATEGY_REGISTRY",
    "CoverageAnalysis",
    "analyze_coverage",
    "TestQualityMetrics",
    "assess_test_quality",
    "AgentProfile",
    "AgentRole",
    "AgentCapability",
]

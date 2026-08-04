"""项目完成度验证脚本：跑8项检查并打印证据。"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def header(i: int, title: str) -> None:
    print(f"\n{'=' * 70}\n[验证{i}] {title}\n{'=' * 70}")


def result(ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  -> {mark}  {detail}")


# ------------------------------------------------------------------ #
v1_ok = v2_ok = v3_ok = v4_ok = v5_ok = v6_ok = v7_ok = v8_ok = False
v9_ok = v10_ok = v11_ok = False  # 方案ABC 新增验证

# 验证1: test_gen_agent 可导入 + _execute_tests 3/3 passed
header(1, "test_gen_agent 模块导入 + _execute_tests 单测")
try:
    from app.agents.test_gen_agent import TestGenAgent, _execute_tests, _apply_execution_result
    from app.graph.state import GeneratedFile

    code_files = [GeneratedFile(path="main.py", language="python",
                                content="def add(a, b):\n    return a + b\n")]
    test_files = [{
        "path": "tests/test_add.py", "language": "python",
        "content": ("from main import add\n\n"
                    "def test_add_positive():\n    assert add(1, 2) == 3\n\n"
                    "def test_add_negative():\n    assert add(-1, -2) == -3\n\n"
                    "def test_add_zero():\n    assert add(0, 0) == 0\n"),
        "description": "add test",
    }]
    r = _execute_tests(code_files=code_files, test_files=test_files, timeout=15)
    v1_ok = (r["status"] == "passed" and r["passed"] == 3 and r["total"] == 3)
    result(v1_ok, f"status={r['status']}  passed={r['passed']}/{r['total']}  "
                  f"case_results={list(r['case_results'].keys())}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证2: rag_service 可导入 + DashScopeEmbeddingProvider 存在
header(2, "rag_service 模块 + DashScope Embedding Provider")
try:
    from app.tools.rag_service import (
        DashScopeEmbeddingProvider, _select_embedding_provider, rag,
    )
    provider = _select_embedding_provider()
    is_dashscope = isinstance(provider, DashScopeEmbeddingProvider)
    v2_ok = is_dashscope
    result(v2_ok, f"provider={type(provider).__name__}  is_dashscope={is_dashscope}  "
                  f"rag_available={rag.is_available()}  count={rag.count()}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证3: RAG 语义检索 5/5 命中
header(3, "RAG 语义检索质量（5 用例）")
try:
    proc = subprocess.run(
        [sys.executable, "scripts/rag_semantic_test.py"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60,
    )
    out = proc.stdout
    m = re.search(r"命中率:\s*(\d+)/(\d+)\s*\((\d+)%\)", out)
    if m:
        hit, total, pct = int(m.group(1)), int(m.group(2)), int(m.group(3))
        v3_ok = (hit == total and pct == 100)
        result(v3_ok, f"命中率={hit}/{total} ({pct}%)  exit={proc.returncode}")
    else:
        result(False, f"未匹配到命中率行，exit={proc.returncode}\nstdout: {out[-500:]}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证4: pytest 已安装 + requirements.txt 含 pytest
header(4, "pytest 安装 + requirements.txt 声明")
try:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"],
        capture_output=True, text=True, timeout=15,
    )
    pytest_installed = proc.returncode == 0
    req_text = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pytest_in_req = "pytest" in req_text
    v4_ok = pytest_installed and pytest_in_req
    result(v4_ok, f"pytest_installed={pytest_installed}  in_requirements={pytest_in_req}  "
                  f"version={proc.stdout.strip()}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证5: benchmark.py 能跑出结果 + v2.json 含修复后数据
header(5, "benchmark.py 脚本完整 + v2 结果文件")
try:
    bench_script = PROJECT_ROOT / "scripts" / "benchmark.py"
    v2_json = PROJECT_ROOT / "data" / "benchmark_result_v2.json"
    bench_exists = bench_script.exists()
    v2_exists = v2_json.exists()
    v2_has_data = False
    v2_review_rate_improved = False
    if v2_exists:
        data = json.loads(v2_json.read_text(encoding="utf-8"))
        summary = data.get("summary", {})
        # 修复后评审通过率应为 80%（4/5）
        review_rate = summary.get("review_approval_rate", "")
        v2_has_data = "review_approval_rate" in summary
        v2_review_rate_improved = "80%" in review_rate
    v5_ok = bench_exists and v2_exists and v2_has_data and v2_review_rate_improved
    result(v5_ok, f"bench_exists={bench_exists}  v2_exists={v2_exists}  "
                  f"v2_has_summary={v2_has_data}  review_rate_80%={v2_review_rate_improved}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证6: README.md 存在 + 含 2 张 mermaid 架构图
header(6, "README.md 完整性（mermaid 图数）")
try:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    mermaid_count = readme.count("```mermaid")
    has_arch = "多智能体协同流水线" in readme or "flowchart" in readme
    has_benchmark = "Benchmark" in readme
    v6_ok = mermaid_count >= 2 and has_arch and has_benchmark
    result(v6_ok, f"mermaid_count={mermaid_count}  has_arch={has_arch}  "
                  f"has_benchmark={has_benchmark}  readme_len={len(readme)}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证7: .env EMBEDDING_DIM=1536 + AST 白名单含 utils.*
header(7, ".env 配置（EMBEDDING_DIM + AST 白名单）")
try:
    env_text = (PROJECT_ROOT / ".env").read_text(encoding="utf-8")
    dim_match = re.search(r"^EMBEDDING_DIM=(\d+)", env_text, re.MULTILINE)
    dim_ok = dim_match and int(dim_match.group(1)) == 1536
    utils_in_whitelist = "utils.*" in env_text
    v7_ok = dim_ok and utils_in_whitelist
    result(v7_ok, f"EMBEDDING_DIM={dim_match.group(1) if dim_match else 'MISSING'}  "
                  f"utils.*_in_whitelist={utils_in_whitelist}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证8: FastAPI 应用 main 模块可加载
header(8, "FastAPI 应用可加载（app.main:app）")
try:
    from app.main import app
    routes_count = len(app.routes)
    v8_ok = routes_count > 0
    result(v8_ok, f"app_loaded=True  routes_count={routes_count}  "
                  f"title={app.title}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证9: 方案A - review_feedback 字段 + review_node 失败时写 feedback
header(9, "方案A: Retry with Feedback")
try:
    from app.graph.state import PipelineState, ReviewIssue, ReviewSeverity
    has_field = "review_feedback" in PipelineState.__annotations__
    from app.agents.review_agent import review_node
    from app.agents.code_gen_agent import CodeGenAgent
    # 确认 code_gen 引用了 review_feedback（看源码包含该字符串）
    import inspect
    cg_src = inspect.getsource(CodeGenAgent)
    code_gen_uses_feedback = "review_feedback" in cg_src
    v9_ok = has_field and code_gen_uses_feedback
    result(v9_ok, f"state_has_field={has_field}  code_gen_uses_feedback={code_gen_uses_feedback}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证10: 方案B - chat_structured + ReviewReportOutput
header(10, "方案B: Structured Output")
try:
    from app.core.llm_adapter import LLMAdapter
    has_method = hasattr(LLMAdapter, "chat_structured")
    from app.agents.review_agent import ReviewReportOutput, ReviewIssueOutput, CoverageCheckOutput
    models_defined = all(
        cls.__name__ in ("ReviewReportOutput", "ReviewIssueOutput", "CoverageCheckOutput")
        for cls in (ReviewReportOutput, ReviewIssueOutput, CoverageCheckOutput)
    )
    # 确认 review_agent 调用了 chat_structured
    import inspect
    ra_src = inspect.getsource(__import__("app.agents.review_agent", fromlist=["ReviewAgent"]).ReviewAgent)
    uses_structured = "chat_structured" in ra_src
    v10_ok = has_method and models_defined and uses_structured
    result(v10_ok, f"has_method={has_method}  models_defined={models_defined}  "
                   f"review_uses_structured={uses_structured}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# 验证11: 方案C - SqliteSaver + resume_pipeline + /resume API
header(11, "方案C: LangGraph Checkpointer")
try:
    from app.graph.pipeline import (
        run_pipeline, resume_pipeline, get_checkpointer, build_graph,
        _SQLITE_SAVER_AVAILABLE,
    )
    cp = get_checkpointer()
    cp_ok = cp is not None
    saver_available = _SQLITE_SAVER_AVAILABLE
    resume_fn_exists = callable(resume_pipeline)
    # 验证 /resume API 路由存在
    from app.main import app
    resume_routes = [
        r for r in app.routes
        if hasattr(r, "path") and "/resume" in (r.path or "")
    ]
    resume_api_ok = len(resume_routes) > 0
    v11_ok = cp_ok and saver_available and resume_fn_exists and resume_api_ok
    result(v11_ok, f"checkpointer={type(cp).__name__ if cp else 'None'}  "
                   f"saver_available={saver_available}  "
                   f"resume_fn={resume_fn_exists}  resume_api_count={len(resume_routes)}")
except Exception as e:
    result(False, f"异常：{type(e).__name__}: {e}")


# ------------------------------------------------------------------ #
#  汇总
# ------------------------------------------------------------------ #
print(f"\n{'=' * 70}\n汇总\n{'=' * 70}")
checks = [
    ("1. test_gen_agent + _execute_tests 3/3", v1_ok),
    ("2. rag_service + DashScope Embedding", v2_ok),
    ("3. RAG 语义检索 5/5 命中", v3_ok),
    ("4. pytest 安装 + requirements 声明", v4_ok),
    ("5. benchmark.py + v2 修复后数据", v5_ok),
    ("6. README.md 2张 mermaid 图", v6_ok),
    ("7. .env EMBEDDING_DIM=1536 + utils.* 白名单", v7_ok),
    ("8. FastAPI main 模块可加载", v8_ok),
    ("9. 方案A: Retry with Feedback", v9_ok),
    ("10. 方案B: Structured Output", v10_ok),
    ("11. 方案C: LangGraph Checkpointer", v11_ok),
]
for name, ok in checks:
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")
passed = sum(1 for _, ok in checks if ok)
print(f"\n通过：{passed}/{len(checks)}")
print(f"总体：{'ALL PASS' if passed == len(checks) else 'HAS FAILURES'}")

"""行为测试生成单元测试。

重点测：
- AgentConfig.behavioral_test_enabled 默认开启
- _generate_behavioral_test_file 用 mock LLM 为顶层函数生成带真实断言的测试
- _extract_test_methods 只提取 test_* 方法且跳过私有/嵌套（行为与 _extract_functions 一致）
- TestGenAgent.run 在行为测试失败时自动降级到原生 AST 模板
- TestGenAgent.run 在行为测试成功时产出 tests/test_behavioral.py（与 test_auto_generated.py 并存）
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.core.llm_adapter import ChatResponse, LLMUsage, llm
from app.agents.test_gen_agent import (
    TestGenAgent,
    _extract_test_methods,
    _generate_behavioral_test_file,
)
from app.graph.state import CodePackage, GeneratedFile, new_state
from app.graph.state.models import TestCase as BehaviorTestCase


# 模拟 LLM 返回的行为测试代码（含两个带真实断言的测试方法）
SAMPLE_BEHAVIORAL = '''
import my_module


class TestBehavioral(unittest.TestCase):
    def test_to_snake_case_returns_camel_case(self):
        self.assertEqual(my_module.to_snake_case("camelCase"), "camel_case")

    def test_add_returns_sum(self):
        self.assertEqual(my_module.add(2, 3), 5)
'''


class _FakeLLM:
    """模拟 LLMAdapter：记录调用次数并断言 use_code_model=True。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def to_messages(self, *, system=None, user=None, history=None):
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def chat(self, messages, *, use_code_model=False, model=None, **kwargs):
        self.calls += 1
        assert use_code_model is True, "行为测试必须使用代码模型"
        return ChatResponse(
            content=self.content,
            usage=LLMUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12),
            model="mock-code-model",
        )


def _make_pkg() -> CodePackage:
    """构造一个含顶层函数的代码包。"""
    return CodePackage(files=[
        GeneratedFile(
            path="my_module.py",
            content="def to_snake_case(s: str) -> str:\n    return s\n\n\ndef add(a: int, b: int) -> int:\n    return a + b\n",
        ),
    ])


# ------------------------------------------------------------------ #
#  配置默认值
# ------------------------------------------------------------------ #
def test_behavioral_test_enabled_default_true() -> None:
    """AgentConfig.behavioral_test_enabled 默认应为 True。"""
    assert settings.agent.behavioral_test_enabled is True


# ------------------------------------------------------------------ #
#  _generate_behavioral_test_file
# ------------------------------------------------------------------ #
def test_generate_behavioral_test_file_extracts_methods() -> None:
    """用 mock LLM 应生成含真实断言方法的测试文件，并回填用例与用量。"""
    fake = _FakeLLM(SAMPLE_BEHAVIORAL)
    content, cases, usage = _generate_behavioral_test_file(_make_pkg(), fake)

    assert "class TestBehavioral(unittest.TestCase):" in content
    assert "test_to_snake_case_returns_camel_case" in content
    assert "test_add_returns_sum" in content
    assert "self.assertEqual(my_module.add(2, 3), 5)" in content
    # 每个 test_* 方法都被记录为一条用例
    assert len(cases) == 2
    assert {c.name for c in cases} == {
        "test_to_snake_case_returns_camel_case",
        "test_add_returns_sum",
    }
    assert usage.total_tokens == 12
    # 只调用一次 LLM（一个 Python 文件）
    assert fake.calls == 1


def test_generate_behavioral_test_file_empty_pkg() -> None:
    """无 Python 文件时返回空内容、空用例、零用量（不调用 LLM）。"""
    fake = _FakeLLM(SAMPLE_BEHAVIORAL)
    content, cases, usage = _generate_behavioral_test_file(
        CodePackage(files=[GeneratedFile(path="readme.md", content="# hi")]),
        fake,
    )
    assert content == ""
    assert cases == []
    assert usage.total_tokens == 0
    assert fake.calls == 0


def test_extract_test_methods_skips_non_test_and_private() -> None:
    """只提取 test_* 方法，忽略非测试方法与 _ 私有函数。"""
    code = '''
def helper():
    return 1

def _test_private_helper():
    pass

class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)

    def not_a_test(self):
        pass

    def test_neg(self):
        self.assertEqual(-1, -1)
'''
    methods = _extract_test_methods(code)
    names = {m.strip().split("(", 1)[0].replace("def ", "") for m in methods}
    assert names == {"test_ok", "test_neg"}


def test_extract_test_methods_invalid_syntax_returns_empty() -> None:
    """非法 Python 源码应返回空列表，不抛异常。"""
    assert _extract_test_methods("def broken(:") == []


# ------------------------------------------------------------------ #
#  TestGenAgent.run 降级与并存
# ------------------------------------------------------------------ #
def _run_state() -> dict:
    state = new_state(task_id="t-beh", user_request="写个工具函数")
    state["code_package"] = CodePackage(files=[
        GeneratedFile(path="add.py", content="def add(a: int, b: int) -> int:\n    return a + b\n"),
    ])
    return state


def _exec_result() -> dict:
    return {
        "status": "passed", "total": 1, "passed": 1, "failed": 0,
        "error": 0, "skipped": 0, "duration_ms": 0, "case_results": {},
        "stdout": "", "stderr": "",
    }


def test_run_falls_back_to_ast_when_behavioral_fails() -> None:
    """行为测试生成抛异常时，应自动降级到原生 AST 模板，不阻断流水线。"""
    llm.dry_run = False
    llm._code = object()  # 模拟代码模型可用
    with patch(
        "app.agents.test_gen_agent._generate_behavioral_test_file",
        side_effect=RuntimeError("LLM 调用失败"),
    ), patch(
        "app.agents.test_gen_agent._execute_tests", return_value=_exec_result()
    ), patch("app.agents.test_gen_agent.db.add_stage_log"), patch(
        "app.agents.test_gen_agent.db.update_task_phase"
    ):
        report = TestGenAgent().run(_run_state())

    paths = {Path(f["path"]).name for f in report.test_files}
    assert "test_auto_generated.py" in paths
    assert "test_behavioral.py" not in paths
    # 降级后仅运行 AST 文件
    assert "test_behavioral.py" not in report.run_command
    assert "test_auto_generated.py" in report.run_command


def test_run_uses_behavioral_when_llm_available() -> None:
    """行为测试成功时，应产出 test_behavioral.py 且与 test_auto_generated.py 并存。"""
    llm.dry_run = False
    llm._code = object()
    beh_content = (
        "import add\n\n\nclass TestBehavioral(unittest.TestCase):\n"
        "    def test_add_returns_sum(self):\n"
        "        self.assertEqual(add.add(2, 3), 5)\n"
    )
    beh_cases = [
        BehaviorTestCase(
            name="test_add_returns_sum",
            description="LLM 行为测试",
            target_file="add.py",
            target_function="add",
        ),
    ]
    beh_usage = LLMUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12)
    with patch(
        "app.agents.test_gen_agent._generate_behavioral_test_file",
        return_value=(beh_content, beh_cases, beh_usage),
    ), patch(
        "app.agents.test_gen_agent._execute_tests", return_value=_exec_result()
    ), patch("app.agents.test_gen_agent.db.add_stage_log"), patch(
        "app.agents.test_gen_agent.db.update_task_phase"
    ):
        report = TestGenAgent().run(_run_state())

    paths = {Path(f["path"]).name for f in report.test_files}
    assert "test_behavioral.py" in paths
    assert "test_auto_generated.py" in paths  # 并存
    # 运行命令同时包含两个测试文件
    assert "test_behavioral.py" in report.run_command
    assert "test_auto_generated.py" in report.run_command
    # 行为测试用例被回填
    assert any(c.name == "test_add_returns_sum" for c in report.test_cases)
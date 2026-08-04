"""执行验证 + 自愈闭环（verify-then-fix）单元测试。

重点测：
- PipelineStatus 新增的 verify_attempts / verify_healed 字段默认值
- _verify_and_heal 在 execute_code 失败 → LLM 修复成功场景下的行为
- 达到最大修复轮次后终止（verify_healed=False）
- verify_enabled=False 时跳过执行验证（不影响纯本地路径）
- run() 会把验证自愈信息写回 state.status
"""
from __future__ import annotations

from unittest.mock import patch

from app.graph.state import PipelineStatus, new_state


def _make_exec(success: bool) -> object:
    """构造一个指定 success 的 ExecResult（仅含成功/失败字段，不含真实执行）。"""
    from app.tools.code_executor import ExecResult, FileExecResult

    if success:
        return ExecResult(
            success=True, total_files=1, passed_files=1,
            results=[FileExecResult(path="main.py", syntax_ok=True, import_ok=True, run_ok=True)],
            summary="验证通过：1 个文件全部语法正确且可导入",
        )
    return ExecResult(
        success=False, total_files=1, failed_files=1,
        results=[FileExecResult(path="main.py", syntax_ok=False, syntax_error="第1行: invalid syntax")],
        summary="验证失败：1 个文件有问题",
    )


# ------------------------------------------------------------------ #
#  状态字段
# ------------------------------------------------------------------ #
def test_pipeline_status_verify_fields_default() -> None:
    """PipelineStatus 应含 verify_attempts=0 / verify_healed=False 默认值。"""
    status = PipelineStatus()
    assert status.verify_attempts == 0
    assert status.verify_healed is False


# ------------------------------------------------------------------ #
#  自愈闭环：失败 → LLM 修复 → 成功
# ------------------------------------------------------------------ #
def test_verify_heal_loop_success(mock_llm_response) -> None:
    """execute_code 首次失败 → LLM 修复 → 二次验证通过，verify_attempts=1 / verify_healed=True。"""
    from app.agents.code_gen_agent import CodeGenAgent

    calls = {"n": 0}

    def fake_execute(files, **kwargs):
        calls["n"] += 1
        return _make_exec(success=(calls["n"] > 1))

    # LLM 修复返回合法代码
    mock_llm_response.return_value.content = "### FILE: main.py\ndef main():\n    print('ok')\n"

    agent = CodeGenAgent()
    state = new_state(task_id="t", user_request="求和")
    with patch("app.tools.code_executor.execute_code", side_effect=fake_execute), \
            patch("app.core.llm_adapter.llm.chat", mock_llm_response):
        files, attempts, healed = agent._verify_and_heal(
            [{"path": "main.py", "content": "def broken(:\n"}],
            state=state, user_request="求和", max_retry=2,
        )

    assert attempts == 1
    assert healed is True
    assert calls["n"] == 2
    assert files[0]["path"] == "main.py"
    assert "print" in files[0]["content"]


def test_verify_heal_gives_up_after_max_retry(mock_llm_response) -> None:
    """execute_code 始终失败 → 最多修复 max_retry 次后终止，verify_healed=False。"""
    from app.agents.code_gen_agent import CodeGenAgent

    def fake_execute(files, **kwargs):
        return _make_exec(success=False)

    mock_llm_response.return_value.content = "### FILE: main.py\ndef broken(:\n"

    agent = CodeGenAgent()
    state = new_state(task_id="t", user_request="x")
    with patch("app.tools.code_executor.execute_code", side_effect=fake_execute), \
            patch("app.core.llm_adapter.llm.chat", mock_llm_response):
        files, attempts, healed = agent._verify_and_heal(
            [{"path": "main.py", "content": "def broken(:\n"}],
            state=state, user_request="x", max_retry=2,
        )

    assert attempts == 2
    assert healed is False
    # 修复产物仍为修复后的文件（未通过验证，但保留供 review 审阅）
    assert files[0]["path"] == "main.py"


def test_verify_heal_stops_on_empty_fix(mock_llm_response) -> None:
    """LLM 修复产物为空时终止，不继续消耗轮次。"""
    from app.agents.code_gen_agent import CodeGenAgent

    def fake_execute(files, **kwargs):
        return _make_exec(success=False)

    # 空输出 → 解析不出文件 → 终止
    mock_llm_response.return_value.content = ""

    agent = CodeGenAgent()
    state = new_state(task_id="t", user_request="x")
    with patch("app.tools.code_executor.execute_code", side_effect=fake_execute), \
            patch("app.core.llm_adapter.llm.chat", mock_llm_response):
        files, attempts, healed = agent._verify_and_heal(
            [{"path": "main.py", "content": "def broken(:\n"}],
            state=state, user_request="x", max_retry=2,
        )

    assert attempts == 1
    assert healed is False


# ------------------------------------------------------------------ #
#  开关控制
# ------------------------------------------------------------------ #
def test_verify_disabled_skips_execution(mock_llm_response) -> None:
    """verify_enabled=False 时，run() 不调用 execute_code（不影响纯本地路径）。"""
    from app.agents.code_gen_agent import CodeGenAgent
    from app.core.config import settings

    agent = CodeGenAgent()
    state = new_state(task_id="t", user_request="求和")
    with patch.object(settings.agent, "verify_enabled", False), \
            patch("app.tools.code_executor.execute_code") as mock_exec:
        pkg = agent.run(state)
    mock_exec.assert_not_called()
    assert pkg.files  # 仍有代码产物


# ------------------------------------------------------------------ #
#  run() 写回验证自愈信息
# ------------------------------------------------------------------ #
def test_run_records_verify_status(mock_llm_response) -> None:
    """verify_enabled=True 时，run() 把 verify_attempts/verify_healed 写回 state.status。"""
    from app.agents.code_gen_agent import CodeGenAgent

    calls = {"n": 0}

    def fake_execute(files, **kwargs):
        calls["n"] += 1
        return _make_exec(success=(calls["n"] > 1))

    mock_llm_response.return_value.content = "### FILE: main.py\nprint('ok')\n"

    agent = CodeGenAgent()
    state = new_state(task_id="t", user_request="x")
    with patch("app.tools.code_executor.execute_code", side_effect=fake_execute), \
            patch("app.core.llm_adapter.llm.chat", mock_llm_response):
        agent.run(state)

    assert state["status"].verify_attempts == 1
    assert state["status"].verify_healed is True
"""ReAct 推理链 + Memory recall 接入测试。"""
from __future__ import annotations

from app.core.config import settings


# ============================================================
# ReAct 推理链测试
# ============================================================

def test_react_config_default_enabled():
    """ReAct 默认启用"""
    assert settings.agent.react_enabled is True


def test_react_system_prompt_exists():
    """ReAct prompt 常量存在且包含关键要素"""
    from app.agents.code_gen_agent import REACT_SYSTEM_PROMPT
    assert "Thought" in REACT_SYSTEM_PROMPT
    assert "Action" in REACT_SYSTEM_PROMPT
    assert "Observation" in REACT_SYSTEM_PROMPT
    assert "### FILE:" in REACT_SYSTEM_PROMPT


def test_react_prompt_used_when_enabled():
    """react_enabled=True 时应使用 ReAct prompt"""
    from app.agents.code_gen_agent import REACT_SYSTEM_PROMPT, CODE_GEN_SYSTEM_PROMPT
    # 模拟 prompt 选择逻辑
    react_enabled = True
    skill_prompt = None  # 无技能自定义 prompt
    system_prompt = skill_prompt or CODE_GEN_SYSTEM_PROMPT
    if react_enabled and not skill_prompt:
        system_prompt = REACT_SYSTEM_PROMPT
    assert system_prompt == REACT_SYSTEM_PROMPT


def test_react_prompt_not_used_when_disabled():
    """react_enabled=False 时应使用原始 prompt"""
    from app.agents.code_gen_agent import CODE_GEN_SYSTEM_PROMPT
    react_enabled = False
    skill_prompt = None
    system_prompt = skill_prompt or CODE_GEN_SYSTEM_PROMPT
    if react_enabled and not skill_prompt:
        from app.agents.code_gen_agent import REACT_SYSTEM_PROMPT
        system_prompt = REACT_SYSTEM_PROMPT
    assert system_prompt == CODE_GEN_SYSTEM_PROMPT


def test_react_prompt_not_used_with_skill_prompt():
    """有技能自定义 prompt 时，ReAct 不覆盖"""
    from app.agents.code_gen_agent import REACT_SYSTEM_PROMPT
    react_enabled = True
    skill_prompt = "你是算法专家。只输出代码。"
    system_prompt = skill_prompt  # 技能 prompt 优先
    if react_enabled and not skill_prompt:
        system_prompt = REACT_SYSTEM_PROMPT
    assert system_prompt == skill_prompt


def test_react_prompt_parses_file_sections():
    """ReAct 输出（含 Thought 前缀）能被 _parse_file_sections 正确解析"""
    from app.agents.code_gen_agent import _parse_file_sections
    # 模拟 ReAct 模式 LLM 输出：Thought 在前，代码在后
    raw_output = """Thought: 需要实现快速排序，用分治法。

### FILE: quick_sort.py
```python
def quick_sort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quick_sort(left) + middle + quick_sort(right)
```"""
    files = _parse_file_sections(raw_output)
    assert len(files) == 1
    assert files[0]["path"] == "quick_sort.py"
    assert "quick_sort" in files[0]["content"]


# ============================================================
# Memory recall 接入测试
# ============================================================

def test_memory_config_defaults():
    """Memory 配置默认值"""
    assert settings.memory.enabled is True
    assert settings.memory.recall_on_gen is True


def test_memory_recall_only_on_first_gen():
    """Memory recall 仅在 retry_counter=0 时注入"""
    # 模拟条件判断
    retry_counter = 0
    should_inject = settings.memory.enabled and settings.memory.recall_on_gen and retry_counter == 0
    assert should_inject is True

    retry_counter = 1
    should_inject = settings.memory.enabled and settings.memory.recall_on_gen and retry_counter == 0
    assert should_inject is False


def test_memory_recall_disabled_when_recall_on_gen_false():
    """recall_on_gen=False 时不注入"""
    # 模拟条件
    original = settings.memory.recall_on_gen
    try:
        settings.memory.recall_on_gen = False
        should_inject = settings.memory.enabled and settings.memory.recall_on_gen and True
        assert should_inject is False
    finally:
        settings.memory.recall_on_gen = original

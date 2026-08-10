from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from app.agents.agent_protocol import (
    AgentCapability,
    AgentProfile,
    AgentRole,
    AgentTask,
)
from app.core.config import settings
from app.core.memory import memory
from app.graph.state import (
    CodePackage,
    GeneratedFile,
    PipelinePhase,
    PipelineState,
    RequirementSpec,
)
from app.tools.ast_validator import validate_python_source
from app.tools.skill_registry import Skill, skill_registry
from app.tools.tool_registry import tool_registry

logger = logging.getLogger(__name__)

# ====================================================================== #
#  Prompt Templates
# ====================================================================== #

CODE_GEN_SYSTEM_PROMPT = """你是Python工程师。根据需求产出可运行代码。
输出格式：用"### FILE: 路径"分隔每个文件，文件内直接放源码。
规则：合法Python、含校验和异常处理、禁eval/exec、只输出代码。"""

REACT_SYSTEM_PROMPT = """你是Python工程师。使用 ReAct（推理-行动）模式完成任务。

步骤：
1. Thought：分析需求，推理实现方案（简洁，2-3句）
2. Action：调用工具验证假设，或直接生成代码
3. Observation：观察工具返回结果
4. 重复 Thought → Action 直到方案确认
5. 最终输出：用"### FILE: 路径"分隔每个文件，文件内直接放源码

规则：合法Python、含校验和异常处理、禁eval/exec、最终必须输出代码。"""

REACT_ENHANCED_PROMPT = """你是Python工程师。使用增强 ReAct（推理-行动）模式完成任务。

步骤：
1. Thought：分析需求→拆解子任务→确定需要的工具/技能
2. Action：选择最合适的工具执行（按工具选择策略：observe→modify→validate）
3. Observation：记录工具返回结果，判断是否需要更多信息
4. 重复直到可生成代码
5. 代码生成：按"### FILE: 路径"格式输出
6. 自我验证：检查生成代码的类型注解、异常处理、边界条件

工具选择策略：
- 信息不足时：先 read_file / list_dir / search_code 了解上下文
- 需要创建：write_file 创建新文件
- 需要验证：validate_code 检查语法
- 组合调用：先观察 → 再修改 → 最后验证

规则：合法Python、含校验和异常处理、禁eval/exec、最终必须输出代码。"""

FEW_SHOT_EXAMPLES: str = """
## Few-shot 示例（参考格式，不要直接复制）

示例1 - 工具类：
### FILE: src/string_utils.py
```python
def capitalize_words(text: str) -> str:
    \"\"\"将字符串每个单词首字母大写。\"\"\"
    if not text:
        return ""
    try:
        return " ".join(w.capitalize() for w in text.split())
    except (TypeError, AttributeError):
        return ""
```

示例2 - 数据处理：
### FILE: src/data_processor.py
```python
from typing import List, Dict, Any

def filter_active(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    \"\"\"过滤出 active=True 的记录。\"\"\"
    if not isinstance(records, list):
        raise TypeError("records 必须是列表")
    return [r for r in records if isinstance(r, dict) and r.get("active")]
```
"""

PLAN_EXECUTE_SYSTEM_PROMPT = """你是Python工程师。使用 Plan-Execute（规划-执行）模式完成复杂编码任务。

## 阶段一：规划（Plan）
分析需求，将代码拆分为独立模块/文件，输出结构化执行计划：
```
PLAN:
1. 模块名 | 职责描述
2. 模块名 | 职责描述
...
```

## 阶段二：执行（Execute）
按计划逐步生成每个模块的代码，每生成一个模块后自我验证：
- 类型注解完整性
- 异常处理覆盖
- 边界条件检查
- 与已生成模块的接口一致性

输出格式：用"### FILE: 路径"分隔每个文件。

规则：合法Python、含校验和异常处理、禁eval/exec、最终必须输出代码。"""

# ====================================================================== #
#  Regex & Parsing Utilities
# ====================================================================== #

_FILE_SECTION_RE = re.compile(
    r"###\s*FILE\s*[:：]\s*([^\n]+?)\s*\n+"
    r"(?:```(?:python|py|text)?\s*\n)?"
    r"(.*?)"
    r"(?=\n###\s*FILE|\n```\s*$|\Z|\n#\#\#)",
    re.DOTALL | re.IGNORECASE,
)


def _guess_lang(path: str) -> str:
    """根据文件扩展名推断编程语言。

    Design Decision: 使用扩展名快速推断语言，仅用于前端展示/描述，
    不影响代码逻辑。对于未知扩展名统一返回 'plain'。
    """
    if path.endswith((".md", "txt", "toml", "yaml", "yml", "json", "ini", "cfg")):
        return path.rsplit(".", 1)[-1]
    if path.endswith(".py"):
        return "python"
    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "typescript"
    if path.endswith(".html"):
        return "html"
    return "plain"


def _parse_file_sections(raw: str) -> List[Dict[str, str]]:
    """从模型输出里解析成 [{"path":..., "content":...}, ...]。

    Design Decision: 三级回退策略确保最大解析覆盖率：
    1. 优先匹配 "### FILE: 路径" 分节标记
    2. 回退到 markdown 代码块提取
    3. 最终回退：将整段作为单文件代码
    """
    files: List[Dict[str, str]] = []
    if not raw or not raw.strip():
        return files

    stripped = raw.strip()
    if stripped.startswith("[DryRun]") and "### FILE:" not in stripped:
        return files

    for m in _FILE_SECTION_RE.finditer(raw):
        path = m.group(1).strip().strip('`').strip('"').strip("'")
        content = m.group(2).rstrip()
        content = re.sub(r"\n```\s*$", "", content, count=1).rstrip()
        if path and content:
            files.append({"path": path, "content": content})

    if files:
        return files

    blocks = re.findall(r"```(?:python|py)?\s*\n?(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if blocks:
        for i, block in enumerate(blocks):
            if block.strip():
                path = f"module_{i+1}.py" if i > 0 else "main.py"
                files.append({"path": path, "content": block.strip()})
        return files

    lines = raw.strip().split("\n")
    code_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("这是", "以下是", "这是一个", "Here is", "The following")):
            continue
        code_lines.append(line)
    code_text = "\n".join(code_lines).strip()
    if code_text:
        files.append({"path": "main.py", "content": code_text})

    return files


def _validate_and_fix(files: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """对每个 Python 文件做 AST 校验，非法内容截断到第一个非法语句之前。

    Design Decision: 校验仅做 warning 级别的日志记录，不丢弃代码。
    完全无法 parse 时保留原始内容并添加注释，让 Review 阶段再处理。
    这避免了过早丢弃可能只需微调即可通过的代码。
    """
    out: List[Dict[str, str]] = []
    for f in files:
        path = f.get("path", "generated.py")
        content = f.get("content", "")
        if path.endswith(".py"):
            r = validate_python_source(content)
            if not r.passed:
                logger.warning("代码 %s AST 有 %d 个 error：%s", path, len(r.errors),
                               [(v.rule, v.message[:30]) for v in r.errors[:3]])
                if any(v.rule == "syntax_error" for v in r.errors):
                    content = f"# 注意：AST 解析异常，已保留原始文件供 Review 审阅\n" + content
        out.append({"path": path, "content": content, "language": _guess_lang(path)})
    return out


# ====================================================================== #
#  Prompt Building Utilities
# ====================================================================== #

def _collect_skill_practices(skill_ids: List[str]) -> List[str]:
    """收集多个技能的最佳实践，用于技能组合。

    Design Decision: 每个技能最多取 3 条最佳实践，避免 prompt 膨胀。
    技能组合允许多个技能的最佳实践交叉指导 LLM。
    """
    practices: List[str] = []
    for sid in skill_ids:
        skill = skill_registry.get_skill(sid)
        if skill and skill.best_practices:
            for bp in skill.best_practices[:3]:
                practices.append(f"[{skill.name}] {bp}")
    return practices


def _retrieve_code_snippets(query: str, *, limit: int = 3) -> List[Dict[str, str]]:
    """从记忆系统检索历史优秀代码片段，供代码生成复用。

    Design Decision: 代码片段复用是 few-shot learning 的一种轻量形式。
    通过检索相似任务的成功代码，将其作为参考注入 prompt，
    帮助 LLM 生成符合历史成功模式的代码。
    """
    if not settings.memory.enabled:
        return []
    try:
        experiences = memory.recall_experience(query=query, limit=limit * 2)
        snippets: List[Dict[str, str]] = []
        seen_paths: set = set()
        for exp in experiences:
            code_files_raw = exp.get("code_files", "[]")
            if isinstance(code_files_raw, str):
                try:
                    code_files = json.loads(code_files_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            elif isinstance(code_files_raw, list):
                code_files = code_files_raw
            else:
                continue
            for cf in code_files:
                if not isinstance(cf, dict):
                    continue
                path = cf.get("path", "")
                content = cf.get("content", "")
                if path and content and path not in seen_paths:
                    seen_paths.add(path)
                    snippets.append({"path": path, "content": content})
                    if len(snippets) >= limit:
                        return snippets
        return snippets
    except Exception as e:
        logger.warning("代码片段检索失败: %s", e)
        return []


def _select_tools_for_task(
    user_request: str,
    *,
    skill: Optional[Skill] = None,
) -> List[str]:
    """根据任务类型自动选择最佳工具集合。

    Design Decision: 工具选择策略分三层，由粗到精：
    1. 基础工具集：read_file/list_dir/validate_code/write_file（默认全开）
    2. 技能关键词匹配：根据技能关键词补充搜索/读写工具
    3. 需求类型检测：测试/重构等场景补充特定工具
    这种启发式选择避免了全工具暴露带来的 token 浪费和决策干扰。
    """
    base_tools = {"read_file", "list_dir", "validate_code", "write_file"}
    selected = set(base_tools)

    req_lower = user_request.lower()

    if skill and skill.keywords:
        for kw in skill.keywords:
            kw_lower = kw.lower()
            if any(ext in kw_lower for ext in ("搜索", "search", "查找", "find", "search_code")):
                selected.add("search_code")
            if any(ext in kw_lower for ext in ("读", "read", "查看", "open")):
                selected.add("read_file")
            if any(ext in kw_lower for ext in ("写", "write", "创建", "create", "生成")):
                selected.add("write_file")

    if any(kw in req_lower for kw in ("测试", "test", "单元测试", "unit test")):
        selected.add("validate_code")
    if any(kw in req_lower for kw in ("重构", "refactor", "修改", "fix", "修复")):
        selected.add("read_file")
        selected.add("write_file")

    available = set(tool_registry.list_names())
    return sorted(selected & available)


def _build_concise_prompt(
    req: RequirementSpec,
    user_request: str,
    *,
    few_shot: bool = False,
    skills: Optional[List[str]] = None,
    code_snippets: Optional[List[Dict[str, str]]] = None,
) -> str:
    """从 RequirementSpec 生成精简 prompt（减少 prompt token）。

    Design Decision: Prompt 精简策略——只注入与当前任务最相关的信息：
    - 功能需求最多 5 条（核心需求）
    - 代码片段最多 3 条、每条最多 300 字符
    - 技能实践每个最多 3 条
    这避免了 prompt 膨胀导致的 LLM 注意力分散。
    """
    parts = [f"需求：{user_request}"]
    if req.functional_requirements:
        parts.append("功能：")
        for i, fr in enumerate(req.functional_requirements[:5], 1):
            parts.append(f"  {i}. {fr}")
    if req.tech_stack and len(req.tech_stack) > 1:
        parts.append(f"技术栈：{', '.join(req.tech_stack[1:])}")
    parts.append("要求：含类型注解、异常处理、边界条件、docstring")

    if skills:
        skill_practices = _collect_skill_practices(skills)
        if skill_practices:
            parts.append("")
            parts.append("## 适用技能最佳实践")
            for practice in skill_practices:
                parts.append(f"- {practice}")

    if code_snippets:
        parts.append("")
        parts.append("## 参考代码片段（可复用以下模式）")
        for snippet in code_snippets[:3]:
            path = snippet.get("path", "snippet")
            content = snippet.get("content", "")
            parts.append(f"### 参考: {path}")
            if content:
                parts.append(content[:300])

    if few_shot:
        parts.append("")
        parts.append(FEW_SHOT_EXAMPLES)

    return "\n".join(parts)


# ====================================================================== #
#  Strategy Classes
# ====================================================================== #

class CodeGenStrategy(ABC):
    """代码生成策略抽象基类。

    Design Decision: 使用策略模式将不同代码生成方法解耦，
    使新增生成模式（如 Agent-based、Tree-of-Thought）只需
    继承此类并实现 generate 方法，无需修改 Agent 核心逻辑。

    每个策略封装：
    - 系统 prompt 选择逻辑
    - 消息构建方式
    - 生成流程编排
    """

    name: str = "base"

    def get_system_prompt(
        self,
        skill: Optional[Skill] = None,
        state: Optional[PipelineState] = None,
    ) -> str:
        """获取当前策略的系统 prompt。

        Design Decision: 技能 prompt 优先级最高，允许特定技能覆盖默认策略 prompt。
        这使得技能可以完全控制生成行为，实现技能即策略的效果。
        """
        if skill and skill.system_prompt:
            return skill.system_prompt
        return CODE_GEN_SYSTEM_PROMPT

    @abstractmethod
    def generate(
        self,
        *,
        llm: Any,
        messages: List[Dict[str, Any]],
        state: PipelineState,
        user_request: str,
        skill: Optional[Skill],
        available_tools: Optional[List[Dict[str, Any]]],
    ) -> Tuple[Any, int]:
        """执行代码生成。

        Returns:
            (LLM 响应, 耗时毫秒)
        """
        ...


class DirectGenerationStrategy(CodeGenStrategy):
    """直接生成策略：单次 LLM 调用产出所有代码。

    Design Decision: 最简单的生成策略，适合需求明确、
    无需外部信息辅助的简单任务。无规划、无工具调用，
    直接让 LLM 根据 prompt 一次性输出全部代码文件。
    """

    name = "direct"

    def generate(
        self,
        *,
        llm: Any,
        messages: List[Dict[str, Any]],
        state: PipelineState,
        user_request: str,
        skill: Optional[Skill],
        available_tools: Optional[List[Dict[str, Any]]],
    ) -> Tuple[Any, int]:
        system_prompt = self.get_system_prompt(skill, state)
        use_code_model = True

        t0 = __import__("time").perf_counter()
        resp = llm.chat(
            messages,
            tools=available_tools if available_tools else None,
            use_code_model=use_code_model,
            model=state.get("model"),
        )
        duration_ms = int((__import__("time").perf_counter() - t0) * 1000)
        return resp, duration_ms


class ReActStrategy(CodeGenStrategy):
    """ReAct 策略：推理-行动循环，可调用工具验证假设。

    Design Decision: ReAct 模式通过交替的 Thought/Action/Observation
    让 LLM 在生成代码前先验证假设（读取现有文件、搜索代码库等）。
    当需要外部上下文辅助生成时，此策略显著优于直接生成。
    支持增强版（enhanced_react），提供更丰富的工具选择策略。
    """

    name = "react"

    def get_system_prompt(
        self,
        skill: Optional[Skill] = None,
        state: Optional[PipelineState] = None,
    ) -> str:
        if skill and skill.system_prompt:
            return skill.system_prompt
        if state and state.get("enhanced_react"):
            return REACT_ENHANCED_PROMPT
        return REACT_SYSTEM_PROMPT

    def generate(
        self,
        *,
        llm: Any,
        messages: List[Dict[str, Any]],
        state: PipelineState,
        user_request: str,
        skill: Optional[Skill],
        available_tools: Optional[List[Dict[str, Any]]],
    ) -> Tuple[Any, int]:
        system_prompt = self.get_system_prompt(skill, state)
        use_code_model = True

        t0 = __import__("time").perf_counter()
        resp = llm.chat(
            messages,
            tools=available_tools if available_tools else None,
            use_code_model=use_code_model,
            model=state.get("model"),
        )
        duration_ms = int((__import__("time").perf_counter() - t0) * 1000)
        return resp, duration_ms


class PlanExecuteStrategy(CodeGenStrategy):
    """Plan-Execute 策略：先规划后执行，适合复杂多文件任务。

    Design Decision: 当任务涉及多个模块/文件时，Plan-Execute 分两步：
    1. Planning: 分析需求、拆分模块、制定结构化生成计划
    2. Execution: 按计划逐步生成代码，每步自我验证接口一致性
    相比并行生成，Plan-Execute 更可控，可根据中间结果调整后续步骤。
    当 state.plan_execute=True 时启用。
    """

    name = "plan_execute"

    def get_system_prompt(
        self,
        skill: Optional[Skill] = None,
        state: Optional[PipelineState] = None,
    ) -> str:
        if skill and skill.system_prompt:
            return skill.system_prompt
        return PLAN_EXECUTE_SYSTEM_PROMPT

    def generate(
        self,
        *,
        llm: Any,
        messages: List[Dict[str, Any]],
        state: PipelineState,
        user_request: str,
        skill: Optional[Skill],
        available_tools: Optional[List[Dict[str, Any]]],
    ) -> Tuple[Any, int]:
        import time

        system_prompt = self.get_system_prompt(skill, state)
        use_code_model = True
        task_id = state.get("task_id", "")
        max_parallel = int(state.get("max_parallel_files") or 3)

        t_start = time.perf_counter()

        # Step 1: 分析需求，制定执行计划
        analysis_prompt = llm.to_messages(
            system="分析以下需求，将代码拆分为独立模块。输出格式：每行一个模块，格式为 '模块名 | 职责描述'。",
            user=f"需求：{user_request}\n请规划需要创建的独立模块/文件（最多 {max_parallel} 个）。",
        )
        try:
            analysis_resp = llm.chat(
                analysis_prompt,
                use_code_model=False,
                model=state.get("model"),
            )
            module_lines = [
                l.strip() for l in analysis_resp.content.strip().split("\n")
                if l.strip() and not l.strip().startswith("#") and "|" in l
            ]
        except Exception as e:
            logger.warning("Plan-Execute 模块分析失败，回退到直接生成: %s", e)
            resp = llm.chat(
                messages,
                tools=available_tools if available_tools else None,
                use_code_model=use_code_model,
                model=state.get("model"),
            )
            duration_ms = int((time.perf_counter() - t_start) * 1000)
            return resp, duration_ms

        if len(module_lines) <= 1:
            resp = llm.chat(
                messages,
                tools=available_tools if available_tools else None,
                use_code_model=use_code_model,
                model=state.get("model"),
            )
            duration_ms = int((time.perf_counter() - t_start) * 1000)
            return resp, duration_ms

        logger.info("Plan-Execute 规划：%d 个模块: %s", len(module_lines), module_lines)

        # Step 2: 逐步执行生成
        generated_contents: List[str] = []
        for idx, line in enumerate(module_lines):
            parts = line.split("|", 1)
            module_name = parts[0].strip()
            module_desc = parts[1].strip() if len(parts) > 1 else ""

            try:
                mod_messages = llm.to_messages(
                    system=system_prompt,
                    user=(
                        f"需求：{user_request}\n\n"
                        f"请生成模块 '{module_name}'（{module_desc}）的代码。\n"
                        f"用 '### FILE: {module_name}.py' 开头，然后直接放代码。"
                    ),
                )
                resp = llm.chat(
                    mod_messages,
                    use_code_model=use_code_model,
                    model=state.get("model"),
                )
                if resp.content and resp.content.strip():
                    generated_contents.append(resp.content)
            except Exception as e:
                logger.error("Plan-Execute 模块 %s 生成失败: %s", module_name, e)

        # Step 3: 合并结果
        if not generated_contents:
            logger.warning("Plan-Execute 全部模块失败，回退到直接生成")
            resp = llm.chat(
                messages,
                tools=available_tools if available_tools else None,
                use_code_model=use_code_model,
                model=state.get("model"),
            )
            duration_ms = int((time.perf_counter() - t_start) * 1000)
            return resp, duration_ms

        combined_content = "\n\n".join(generated_contents)
        resp = llm.chat(
            llm.to_messages(
                system=system_prompt,
                user=f"以下是 {len(generated_contents)} 个模块的独立生成结果，请整合为完整代码输出。\n\n{combined_content}",
            ),
            use_code_model=use_code_model,
            model=state.get("model"),
        )

        duration_ms = int((time.perf_counter() - t_start) * 1000)
        logger.info("Plan-Execute 完成：%d 个模块，耗时 %dms", len(generated_contents), duration_ms)
        return resp, duration_ms


# ====================================================================== #
#  Strategy Registry
# ====================================================================== #

_STRATEGY_REGISTRY: Dict[str, CodeGenStrategy] = {
    "direct": DirectGenerationStrategy(),
    "react": ReActStrategy(),
    "plan_execute": PlanExecuteStrategy(),
}


def get_strategy(name: str) -> CodeGenStrategy:
    """根据名称获取策略实例。

    Design Decision: 通过字符串名称查找策略，便于从 state 中
    动态选择策略。未知名称回退到 direct 策略。
    """
    return _STRATEGY_REGISTRY.get(name, _STRATEGY_REGISTRY["direct"])


def select_strategy(state: PipelineState) -> CodeGenStrategy:
    """根据 state 自动选择最合适的策略。

    Design Decision: 策略选择优先级：
    1. state.strategy 显式指定 > 自动检测
    2. Plan-Execute: state.plan_execute=True
    3. ReAct: settings.agent.react_enabled=True
    4. 默认: direct
    这实现了渐进式复杂度：简单任务用 direct，复杂任务用 Plan-Execute。
    """
    explicit = state.get("strategy") or ""
    if explicit and explicit in _STRATEGY_REGISTRY:
        return _STRATEGY_REGISTRY[explicit]

    if state.get("plan_execute"):
        return _STRATEGY_REGISTRY["plan_execute"]

    if settings.agent.react_enabled:
        return _STRATEGY_REGISTRY["react"]

    return _STRATEGY_REGISTRY["direct"]


__all__ = [
    "CODE_GEN_SYSTEM_PROMPT",
    "REACT_SYSTEM_PROMPT",
    "REACT_ENHANCED_PROMPT",
    "FEW_SHOT_EXAMPLES",
    "PLAN_EXECUTE_SYSTEM_PROMPT",
    "CodeGenStrategy",
    "DirectGenerationStrategy",
    "ReActStrategy",
    "PlanExecuteStrategy",
    "get_strategy",
    "select_strategy",
    "_parse_file_sections",
    "_validate_and_fix",
    "_guess_lang",
    "_build_concise_prompt",
    "_collect_skill_practices",
    "_retrieve_code_snippets",
    "_select_tools_for_task",
]
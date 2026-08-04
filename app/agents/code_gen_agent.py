from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List

from app.core.llm_adapter import LLMAdapter, llm
from app.core.config import settings
from app.core.events import EventType, event_bus
from app.core.observability import metrics_collector
from app.core.memory import memory
from app.db.sqlite_client import db
from app.graph.state import (
    CodePackage,
    GeneratedFile,
    PipelinePhase,
    PipelineState,
    PipelineStatus,
    RequirementSpec,
    ReviewReport,
    ensure_status,
)
from app.tools.ast_validator import extract_first_python_code_block, validate_python_source
from app.tools.skill_registry import skill_registry
from app.tools.tool_registry import tool_registry
from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger(__name__)


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


_FILE_SECTION_RE = re.compile(
    r"###\s*FILE\s*[:：]\s*([^\n]+?)\s*\n+"
    r"(?:```(?:python|py|text)?\s*\n)?"
    r"(.*?)"
    r"(?=\n###\s*FILE|\n```\s*$|\Z|\n#\#\#)",
    re.DOTALL | re.IGNORECASE,
)


def _build_concise_prompt(req: RequirementSpec, user_request: str) -> str:
    """从 RequirementSpec 生成精简 prompt（减少 prompt token）。"""
    parts = [f"需求：{user_request}"]
    if req.functional_requirements:
        parts.append("功能：")
        for i, fr in enumerate(req.functional_requirements[:5], 1):
            parts.append(f"  {i}. {fr}")
    if req.tech_stack and len(req.tech_stack) > 1:
        parts.append(f"技术栈：{', '.join(req.tech_stack[1:])}")
    parts.append("要求：含类型注解、异常处理、边界条件、docstring")
    return "\n".join(parts)


def _parse_file_sections(raw: str) -> List[Dict[str, str]]:
    """从模型输出里解析成 [{"path":..., "content":...}, ...]。"""
    files: List[Dict[str, str]] = []
    if not raw or not raw.strip():
        return files

    # DryRun 检测：如果内容以 [DryRun] 开头且没有 FILE 标记，返回空列表让外层处理
    stripped = raw.strip()
    if stripped.startswith("[DryRun]") and "### FILE:" not in stripped:
        return files

    # 先尝试 FILE 分节
    for m in _FILE_SECTION_RE.finditer(raw):
        path = m.group(1).strip().strip('`').strip('"').strip("'")
        content = m.group(2).rstrip()
        # 去掉可能残留的结尾 ```
        content = re.sub(r"\n```\s*$", "", content, count=1).rstrip()
        if path and content:
            files.append({"path": path, "content": content})

    if files:
        return files

    # fallback 1: 从 markdown 代码块提取
    blocks = re.findall(r"```(?:python|py)?\s*\n?(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if blocks:
        for i, block in enumerate(blocks):
            if block.strip():
                path = f"module_{i+1}.py" if i > 0 else "main.py"
                files.append({"path": path, "content": block.strip()})
        return files

    # fallback 2: 把整段当代码（去掉解释性文字）
    # 如果看起来像 Python 代码（有 import/def/class 等关键字）
    lines = raw.strip().split("\n")
    code_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过明显的解释性文字
        if stripped.startswith(("这是", "以下是", "这是一个", "Here is", "The following")):
            continue
        code_lines.append(line)
    code_text = "\n".join(code_lines).strip()
    if code_text:
        files.append({"path": "main.py", "content": code_text})

    return files


def _validate_and_fix(files: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """对每个 Python 文件做 AST 校验，非法内容截断到第一个非法语句之前。"""
    out: List[Dict[str, str]] = []
    for f in files:
        path = f.get("path", "generated.py")
        content = f.get("content", "")
        if path.endswith(".py"):
            r = validate_python_source(content)
            if not r.passed:
                # 打印 warnings 但只回滚 errors
                logger.warning("代码 %s AST 有 %d 个 error：%s", path, len(r.errors),
                               [(v.rule, v.message[:30]) for v in r.errors[:3]])
                # 不直接扔掉；让 Review 阶段再处理。只当完全无法 parse 时用空内容。
                if any(v.rule == "syntax_error" for v in r.errors):
                    content = f"# 注意：AST 解析异常，已保留原始文件供 Review 审阅\n" + content
        out.append({"path": path, "content": content, "language": _guess_lang(path)})
    return out


def _guess_lang(path: str) -> str:
    if path.endswith((".md", "txt", "toml", "yaml", "yml", "json", "ini", "cfg")):
        return path.rsplit(".", 1)[-1]
    if path.endswith(".py"):
        return "python"
    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "typescript"
    if path.endswith(".html"):
        return "html"
    return "plain"


class CodeGenAgent:
    """代码生成 Agent：根据 RequirementSpec / Review 反馈产出 CodePackage。"""

    def __init__(self, *, llm_adapter: LLMAdapter = llm, use_code_model: bool = True) -> None:
        self.llm = llm_adapter
        self.use_code_model = use_code_model

    def _verify_and_heal(
        self,
        files: List[Dict[str, str]],
        *,
        state: PipelineState,
        user_request: str,
        max_retry: int,
    ) -> tuple[List[Dict[str, str]], int, bool]:
        """执行验证 + 自愈闭环：验证失败则把结构化错误回喂 LLM 修复，最多 max_retry 次。

        触发条件：execute_code 的 syntax_ok / import_ok / run_ok 任一为 False。
        终止条件：验证通过、达到 max_retry 上限、或修复产物为空。
        返回 (最终文件列表, 实际修复轮次, 是否最终修复成功)。
        自愈循环结束后，最终代码仍由外层走原 review 流程，不改变 review→code_gen 重试逻辑。
        """
        from app.tools.code_executor import execute_code

        attempts = 0
        healed = False
        current = files
        task_id = state.get("task_id", "")
        result = execute_code(current)
        while not result.success and attempts < max_retry:
            attempts += 1
            # 发出验证失败事件（供 Dashboard 显示）
            event_bus.emit(
                EventType.NODE_FAILED,
                data={
                    "node": "code_verify",
                    "node_name": "代码验证",
                    "phase": "code_gen",
                    "attempt": attempts,
                    "max_retry": max_retry,
                    "summary": result.summary,
                },
                task_id=task_id,
            )
            fix_prompt = self._build_fix_prompt(current, result, user_request)
            try:
                resp = self.llm.chat(
                    self.llm.to_messages(
                        system=CODE_GEN_SYSTEM_PROMPT,
                        user=fix_prompt,
                    ),
                    use_code_model=self.use_code_model,
                    model=state.get("model"),
                )
            except Exception as e:
                logger.warning("执行验证自愈 LLM 调用失败，终止修复：%s", e)
                break
            fixed = _validate_and_fix(_parse_file_sections(resp.content))
            if not fixed or all(not f.get("content", "").strip() for f in fixed):
                logger.warning("执行验证自愈产物为空，终止修复")
                break
            current = fixed
            result = execute_code(current)
            if result.success:
                healed = True
                break

        if attempts:
            logger.info(
                "执行验证自愈完成：attempts=%d, healed=%s, summary=%s",
                attempts, healed, result.summary,
            )
        # 发出最终验证结果事件
        event_bus.emit(
            EventType.NODE_COMPLETED,
            data={
                "node": "code_verify",
                "node_name": "代码验证完成",
                "phase": "code_gen",
                "success": result.success,
                "healed": healed,
                "attempts": attempts,
                "summary": result.summary,
                "verdict": "通过" if result.success else "失败",
            },
            task_id=task_id,
        )
        return current, attempts, healed

    @staticmethod
    def _build_fix_prompt(
        files: List[Dict[str, str]],
        result: Any,
        user_request: str,
    ) -> str:
        """把执行验证失败的结构化错误（文件路径 + 错误类型 + 错误信息）组装成修复 prompt。"""
        error_lines: List[str] = []
        for r in result.results:
            if r.syntax_ok and r.import_ok:
                continue
            error_lines.append(f"- 文件: {r.path}")
            if not r.syntax_ok:
                error_lines.append(f"  错误类型: syntax_error")
                error_lines.append(f"  错误信息: {r.syntax_error}")
            if not r.import_ok:
                error_lines.append(f"  错误类型: import_error")
                error_lines.append(f"  错误信息: {r.import_error}")
            if not r.run_ok:
                error_lines.append(f"  错误类型: run_error")
                error_lines.append(f"  错误信息: {r.run_stderr}")
        if not error_lines:
            error_lines.append(f"  验证失败汇总: {result.summary or '-'}")

        parts = [
            f"需求：{user_request}",
            "以下代码执行验证未通过，请修复后重新输出全部文件（用 '### FILE: 路径' 分隔）。",
            "错误信息：",
            "\n".join(error_lines),
        ]
        return "\n".join(parts)

    def run(self, state: PipelineState) -> CodePackage:
        task_id = state.get("task_id", "")
        # LangGraph 节点之间 state 字段可能被展开为 dict，先归一化
        from app.graph.state import normalize_state, ReviewIssue
        original_state = state  # 保留原始引用，用于写回验证自愈信息
        state = normalize_state(state)
        req: RequirementSpec = state.get("requirement") or RequirementSpec()
        review: ReviewReport = state.get("review_report") or ReviewReport()
        retry_counter = int(state.get("retry_counter") or 0)

        # 方案A：优先用 state.review_feedback（已优先级排序+限8条），
        # 没有时回退到 review.issues（兼容旧 state）
        feedback_issues: List[ReviewIssue] = state.get("review_feedback") or []
        if not feedback_issues and review.issues and retry_counter > 0:
            feedback_issues = review.issues

        # 2. 技能匹配（0 Token）：优先用 skill_id，否则自动匹配
        skill_id = state.get("skill_id") or ""
        user_request = state.get("user_request", "") or req.goal or ""
        skill = skill_registry.get_skill(skill_id) if skill_id else skill_registry.match_skill(user_request)
        system_prompt = skill.system_prompt or CODE_GEN_SYSTEM_PROMPT
        # ReAct 模式：启用时替换为 ReAct 推理链 prompt
        if settings.agent.react_enabled and not skill.system_prompt:
            system_prompt = REACT_SYSTEM_PROMPT
        logger.info("代码生成使用技能: %s (%s), ReAct: %s", skill.id, skill.name, settings.agent.react_enabled)

        # 3. 组装精简 prompt（不用 JSON、不用 RAG，节约 Token）
        user_parts = [_build_concise_prompt(req, user_request)]

        # 长期记忆：检索相关历史经验，注入 prompt（仅首次生成，重试时不注入）
        if settings.memory.enabled and settings.memory.recall_on_gen and retry_counter == 0:
            try:
                experiences = memory.recall_experience(query=user_request, limit=3)
                if experiences:
                    exp_lines = []
                    for exp in experiences:
                        if exp.get("source") == "sqlite":
                            verdict = exp.get("review_verdict", "")
                            score = exp.get("review_score", 0)
                            test_status = exp.get("test_status", "")
                            exp_lines.append(
                                f"- 相似任务: verdict={verdict}, score={score}, test={test_status}"
                            )
                            if exp.get("error_message"):
                                exp_lines.append(f"  历史错误: {exp['error_message'][:120]}")
                    if exp_lines:
                        user_parts += ["", "## 历史经验参考（避免重复犯错）", "\n".join(exp_lines)]
            except Exception as e:
                logger.warning("记忆检索失败（不影响主流程）: %s", e)

        if retry_counter > 0 and feedback_issues:
            # 方案A：用结构化 feedback 引导针对性修复
            feedback_lines = []
            for i, issue in enumerate(feedback_issues, 1):
                sev = getattr(issue, "severity", "medium")
                if hasattr(sev, "value"):
                    sev = sev.value
                feedback_lines.append(
                    f"{i}. [{sev.upper()}] {issue.file_path}:{issue.line_start} "
                    f"({issue.rule_id}) {issue.message} → {issue.suggestion}"
                )
            user_parts += [
                "",
                "## 修复以下问题（保留未涉及文件）",
                "\n".join(feedback_lines),
            ]

        messages = self.llm.to_messages(
            system=system_prompt,
            user="\n".join(user_parts),
        )

        # 3. 调用模型（使用代码专用模型）
        # Bug S3：优先使用 state 中 per-task 指定的模型，避免全局 switch_model 串扰
        t0 = time.perf_counter()
        # 获取可用工具
        available_tools = tool_registry.to_openai_tools()
        try:
            resp = self.llm.chat(
                messages,
                tools=available_tools if available_tools else None,
                use_code_model=self.use_code_model,
                model=state.get("model"),
            )
            duration_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            logger.exception("代码生成 LLM 调用失败：%s", e)
            db.add_stage_log(
                task_id=task_id, stage=PipelinePhase.CODE_GEN.value,
                llm_output="", error=str(e), duration_ms=duration_ms,
            )
            return CodePackage()

        # === Function Calling 工具调用回环 ===
        # 如果 LLM 请求调用工具，执行后把结果喂回去，最多 max_tool_rounds 轮
        max_tool_rounds = 3  # 限制最大工具调用轮次，防止无限循环
        conversation = list(messages)  # 复制对话历史

        for _ in range(max_tool_rounds):
            if not resp.tool_calls:
                break  # 没有工具调用，退出回环

            # 将 LLM 的工具调用请求加入对话
            ai_msg = AIMessage(content=resp.content, tool_calls=resp.tool_calls)
            conversation.append(ai_msg)

            # 逐个执行工具调用
            for tc in resp.tool_calls:
                # 兼容 dict 和 dataclass/TypedDict 两种格式
                tool_name = tc["name"] if isinstance(tc, dict) else tc.name
                tool_args = tc["args"] if isinstance(tc, dict) else tc.args
                # ToolCall 的 id 用于 ToolMessage 关联；缺失时用 tool_name 兜底
                if isinstance(tc, dict):
                    tc_id = tc.get("id", tool_name)
                else:
                    tc_id = getattr(tc, "id", None) or tool_name
                logger.info("工具调用: %s(%s)", tool_name, list(tool_args.keys()) if isinstance(tool_args, dict) else tool_args)
                try:
                    tool_result = tool_registry.call(tool_name, tool_args)
                    if isinstance(tool_result, str):
                        result_text = tool_result
                    else:
                        result_text = json.dumps(tool_result, ensure_ascii=False)
                except Exception as e:
                    result_text = f"工具执行错误: {e}"
                conversation.append(ToolMessage(content=result_text, tool_call_id=tc_id))

            # 带着工具结果再次调用 LLM
            resp = self.llm.chat(
                conversation,
                tools=available_tools if available_tools else None,
                use_code_model=self.use_code_model,
                model=state.get("model"),
            )

        # 4. 解析 + 校验
        files_raw = _parse_file_sections(resp.content)

        # DryRun 处理：如果 LLM 返回 DryRun 响应且解析为空，生成通用模板
        if resp.model == "dry-run" and not files_raw:
            logger.warning("检测到 DryRun 响应，生成占位代码...")
            files_raw = [{
                "path": "main.py",
                "content": '''# [DryRun 占位代码] API Key 未配置或额度已用尽
# 请配置有效 API Key 后重新执行以获得真实代码生成

"""
NeuroForge 多智能体协同代码生成系统
当前处于 DryRun 模式（演示模式）
"""


def main():
    """程序入口"""
    print("Hello from NeuroForge DryRun mode!")
    print("=" * 50)
    print("提示：当前未配置有效 API Key，代码为占位模板。")
    print("请在 Dashboard 配置有效的 API Key 以获得真实代码生成。")
    print("=" * 50)


if __name__ == "__main__":
    main()
'''
            }]
            # 发送 DryRun 事件到 Dashboard
            try:
                event_bus.emit(
                    EventType.NODE_FAILED,
                    data={
                        "node": "llm_dryrun",
                        "node_name": "代码生成",
                        "error_type": "dry_run",
                        "message": "当前处于 DryRun 模式（API Key 未配置或额度已用尽），已生成占位代码。请配置 API Key 后重试。",
                        "phase": "code_gen",
                    },
                    task_id=task_id,
                )
            except Exception:
                pass

        # 空文件检测：如果解析出全是空内容，重试一次（用更简单的格式要求）
        if not files_raw or all(not f.get("content","").strip() for f in files_raw):
            logger.warning("首次代码生成解析为空，raw 长度=%d，重试一次...", len(resp.content))
            # 重试：用更明确的提示
            retry_messages = self.llm.to_messages(
                system="直接输出 Python 代码，用 ### FILE: 路径 分隔文件。不要解释。",
                user="\n".join(user_parts) + "\n\n请直接输出代码，不要任何解释文字。",
                history=state.get("messages") or None,
            )
            try:
                resp2 = self.llm.chat(
                    retry_messages,
                    use_code_model=self.use_code_model,
                    model=state.get("model"),
                )
                files_raw = _parse_file_sections(resp2.content)
            except Exception:
                pass

        files_valid = _validate_and_fix(files_raw)

        # 如果还是空，至少生成一个占位文件
        if not files_valid or all(not f.get("content","").strip() for f in files_valid):
            logger.error("代码生成仍然为空，raw output 片段: %s", resp.content[:500])
            files_valid = [{"path": "generated.py", "content": "# 代码生成失败：LLM 返回空内容\n# 原始输出:\n" + resp.content[:2000], "language": "python"}]

        # 5. 执行验证 + 自愈闭环（可选）：验证失败则回喂 LLM 修复，最多 verify_max_retry 次
        if settings.agent.verify_enabled:
            files_valid, verify_attempts, verify_healed = self._verify_and_heal(
                files_valid,
                state=state,
                user_request=user_request,
                max_retry=settings.agent.verify_max_retry,
            )
            # 写回验证自愈信息到原始 state（normalize 会复制 dict，需回写原引用），供评估环节使用
            status = ensure_status(original_state)
            status.verify_attempts = verify_attempts
            status.verify_healed = verify_healed
            original_state["status"] = status

        # 6. 持久化到 DB
        db.add_stage_log(
            task_id=task_id,
            stage=PipelinePhase.CODE_GEN.value,
            prompt_text=req.title or req.goal,
            llm_output=resp.content,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            total_tokens=resp.usage.total_tokens,
            duration_ms=duration_ms,
        )
        db.update_task_phase(
            task_id,
            phase=PipelinePhase.CODE_GEN.value,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
        )

        package = CodePackage(
            files=[
                GeneratedFile(
                    path=f["path"],
                    language=f.get("language", _guess_lang(f["path"])),
                    content=f["content"],
                    description=_guess_lang(f["path"]) + " 文件",
                )
                for f in files_valid
            ],
            entry_point=(files_valid[0]["path"] if files_valid else ""),
            install_steps=["pip install -r requirements.txt" if any("requirements.txt" in f["path"] for f in files_valid) else "pip install <需要的依赖>"],
            raw_llm_output=resp.content,
        )
        logger.info("代码生成完成：%d 个文件，入口=%s", len(package.files), package.entry_point)
        return package


# ------------------------------------------------------------------ #
#  LangGraph 节点函数
# ------------------------------------------------------------------ #

def code_gen_node(state: PipelineState) -> Dict[str, Any]:
    """LangGraph 节点：根据需求（或评审反馈）生成/修订代码包。"""
    task_id = state.get("task_id", "")
    agent = CodeGenAgent()
    pkg = agent.run(state)

    # 文件持久化（DB 中）
    db.upsert_files(
        task_id=task_id,
        files=[
            {"path": f.path, "content": f.content,
             "language": f.language, "description": f.description, "source_stage": "code_gen"}
            for f in pkg.files
        ],
        source_stage="code_gen",
    )
    db.update_task_phase(
        task_id,
        phase=PipelinePhase.CODE_GEN.value,
        summary={"file_count": len(pkg.files), "entry": pkg.entry_point},
    )
    status = ensure_status(state)
    status.phase = PipelinePhase.CODE_GEN
    status.touch()
    return {
        "code_package": pkg,
        "status": status,
    }


__all__ = [
    "CodeGenAgent",
    "code_gen_node",
    "CodePackage",
    "GeneratedFile",
]

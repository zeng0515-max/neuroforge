from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.events import EventType, event_bus
from app.db.sqlite_client import db
from app.graph.state import (
    CodePackage,
    GeneratedFile,
    PipelinePhase,
    PipelineState,
    ensure_status,
)
from app.tools.skill_registry import Skill
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.code_gen_strategies import (
    CODE_GEN_SYSTEM_PROMPT,
    REACT_ENHANCED_PROMPT,
    _guess_lang,
    _parse_file_sections,
    _validate_and_fix,
)

logger = logging.getLogger(__name__)


# ====================================================================== #
#  Fix Prompt Builder
# ====================================================================== #

def _build_fix_prompt(
    files: List[Dict[str, str]],
    result: Any,
    user_request: str,
) -> str:
    """把执行验证失败的结构化错误组装成修复 prompt。

    Design Decision: 将验证错误格式化为 LLM 可理解的结构化文本，
    包含文件路径、错误类型（syntax/import/run）和错误信息。
    这种结构化反馈比原始 traceback 更能引导 LLM 精准修复。
    """
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


# ====================================================================== #
#  Verify & Heal Loop
# ====================================================================== #

def _verify_and_heal(
    llm: Any,
    files: List[Dict[str, str]],
    *,
    state: PipelineState,
    user_request: str,
    max_retry: int,
    use_code_model: bool = True,
) -> Tuple[List[Dict[str, str]], int, bool]:
    """执行验证 + 自愈闭环：验证失败则把结构化错误回喂 LLM 修复。

    Design Decision: 自愈闭环采用"验证→修复→再验证"的循环，
    最多 max_retry 次。触发条件为 execute_code 的 syntax_ok/import_ok/run_ok
    任一为 False。终止条件：验证通过、达到上限、或修复产物为空。
    自愈循环结束后，最终代码仍由外层走原 review 流程，不改变 review→code_gen
    重试逻辑。这是内层修复循环，不影响外层质量保证流程。
    """
    from app.tools.code_executor import execute_code

    attempts = 0
    healed = False
    current = files
    task_id = state.get("task_id", "")
    result = execute_code(current)
    while not result.success and attempts < max_retry:
        attempts += 1
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
        fix_prompt = _build_fix_prompt(current, result, user_request)
        try:
            resp = llm.chat(
                llm.to_messages(
                    system=CODE_GEN_SYSTEM_PROMPT,
                    user=fix_prompt,
                ),
                use_code_model=use_code_model,
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


# ====================================================================== #
#  Iterative Optimization
# ====================================================================== #

def _iterative_optimize(
    llm: Any,
    files: List[Dict[str, str]],
    *,
    user_request: str,
    state: PipelineState,
    max_rounds: int = 2,
    use_code_model: bool = True,
) -> List[Dict[str, str]]:
    """迭代式代码优化：生成→验证→优化循环。

    Design Decision: 迭代优化是可选的后处理步骤，在首次生成通过后执行。
    通过 execute_code 验证发现问题，用 _build_fix_prompt 组装修复指令，
    调用 LLM 改进。最多 max_rounds 轮，避免无限循环。
    仅在 iterative_optimize=True 且首次生成通过时启用。
    """
    try:
        from app.tools.code_executor import execute_code
    except ImportError:
        logger.warning("code_executor 不可用，跳过迭代优化")
        return files

    task_id = state.get("task_id", "")
    current = files

    for round_num in range(1, max_rounds + 1):
        result = execute_code(current)
        if result.success:
            logger.info("迭代优化[轮%d]: 验证通过，无需进一步优化", round_num)
            break

        logger.info(
            "迭代优化[轮%d]: 验证失败，开始修复。错误: %s",
            round_num, result.summary,
        )

        fix_prompt = _build_fix_prompt(current, result, user_request)
        try:
            system_prompt = (
                REACT_ENHANCED_PROMPT
                if settings.agent.react_enabled
                else CODE_GEN_SYSTEM_PROMPT
            )
            resp = llm.chat(
                llm.to_messages(
                    system=system_prompt,
                    user=fix_prompt,
                ),
                use_code_model=use_code_model,
                model=state.get("model"),
            )
            fixed = _validate_and_fix(_parse_file_sections(resp.content))
            if fixed and any(f.get("content", "").strip() for f in fixed):
                current = fixed
            else:
                logger.warning("迭代优化[轮%d]: 修复产物为空，保留原代码", round_num)
                break
        except Exception as e:
            logger.warning("迭代优化[轮%d] LLM 调用失败: %s", round_num, e)
            break

    return current


# ====================================================================== #
#  Parallel Generation
# ====================================================================== #

def _run_parallel_generation(
    llm: Any,
    messages: List[Dict[str, Any]],
    state: PipelineState,
    user_request: str,
    skill: Skill,
    available_tools: List[Dict[str, Any]],
    use_code_model: bool = True,
) -> Tuple[Any, int]:
    """并行代码生成：将任务拆分为多个子任务并行执行。

    Design Decision: 并行生成采用"分析→并行生成→合并"三阶段：
    1. 分析：轻量 LLM 调用，分析需要生成的模块（非代码模型，节省 token）
    2. 并行：ThreadPoolExecutor 并发生成各模块，最多 3 个并行
    3. 合并：将各模块结果合并后再让 LLM 整合为统一输出
    当并行全部失败时，自动回退到单文件模式。
    """
    t_start = time.perf_counter()
    task_id = state.get("task_id", "")
    max_parallel = int(state.get("max_parallel_files") or 3)

    # Step 1: 让 LLM 分析需要生成的模块
    analysis_prompt = llm.to_messages(
        system="分析以下需求，将代码拆分为独立模块。只输出模块名列表（每行一个），不要其他文字。",
        user=f"需求：{user_request}\n请列出需要创建的独立模块/文件（最多 {max_parallel} 个）。",
    )
    try:
        analysis_resp = llm.chat(
            analysis_prompt,
            use_code_model=False,
            model=state.get("model"),
        )
        module_lines = [
            l.strip() for l in analysis_resp.content.strip().split("\n")
            if l.strip() and not l.strip().startswith("#")
        ]
    except Exception as e:
        logger.warning("并行生成模块分析失败，回退到单文件模式: %s", e)
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

    # Step 2: 并行生成各模块
    logger.info("并行生成 %d 个模块: %s", len(module_lines), module_lines)
    system_prompt = skill.system_prompt or CODE_GEN_SYSTEM_PROMPT
    if settings.agent.react_enabled and not skill.system_prompt:
        system_prompt = REACT_ENHANCED_PROMPT

    results: List[Optional[str]] = [None] * len(module_lines)

    def _generate_module(index: int, module_name: str) -> Tuple[int, Optional[str]]:
        try:
            mod_messages = llm.to_messages(
                system=system_prompt,
                user=(
                    f"需求：{user_request}\n\n"
                    f"请生成模块 '{module_name}' 的代码。\n"
                    f"用 '### FILE: {module_name}.py' 开头，然后直接放代码。"
                ),
            )
            resp = llm.chat(
                mod_messages,
                use_code_model=use_code_model,
                model=state.get("model"),
            )
            return index, resp.content
        except Exception as e:
            logger.error("模块 %s 生成失败: %s", module_name, e)
            return index, None

    with ThreadPoolExecutor(max_workers=min(len(module_lines), 3)) as pool:
        futures = [
            pool.submit(_generate_module, i, name)
            for i, name in enumerate(module_lines)
        ]
        for future in as_completed(futures):
            try:
                idx, content = future.result()
                results[idx] = content
            except Exception as e:
                logger.error("并行任务异常: %s", e)

    # Step 3: 合并结果
    combined_parts: List[str] = []
    for content in results:
        if content:
            combined_parts.append(content)

    if not combined_parts:
        logger.warning("并行生成全部失败，回退到单文件模式")
        resp = llm.chat(
            messages,
            tools=available_tools if available_tools else None,
            use_code_model=use_code_model,
            model=state.get("model"),
        )
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        return resp, duration_ms

    combined_content = "\n\n".join(combined_parts)
    resp = llm.chat(
        llm.to_messages(
            system=system_prompt,
            user=f"以下是 {len(module_lines)} 个模块的独立生成结果，请作为整体返回。\n\n{combined_content}",
        ),
        use_code_model=use_code_model,
        model=state.get("model"),
    )

    duration_ms = int((time.perf_counter() - t_start) * 1000)
    logger.info("并行生成完成：%d 个模块，耗时 %dms", len(module_lines), duration_ms)
    return resp, duration_ms


# ====================================================================== #
#  Function Calling Tool Loop
# ====================================================================== #

def _run_tool_loop(
    llm: Any,
    messages: List[Dict[str, Any]],
    resp: Any,
    *,
    available_tools: Optional[List[Dict[str, Any]]],
    state: PipelineState,
    use_code_model: bool = True,
) -> Any:
    """Function Calling 工具调用回环（增强版：支持更多轮次和性能日志）。

    Design Decision: 工具回环最多 max_tool_rounds 次（默认 3），
    每轮处理所有 tool_calls 并将结果追加到对话历史。
    超时保护：达到上限后停止工具调用，直接使用最后响应。
    这防止了 LLM 陷入无限工具调用循环。
    """
    max_tool_rounds = int(state.get("max_tool_rounds") or 3)
    conversation = list(messages)

    for round_idx in range(max_tool_rounds):
        if not resp.tool_calls:
            break

        ai_msg = AIMessage(content=resp.content, tool_calls=resp.tool_calls)
        conversation.append(ai_msg)

        for tc in resp.tool_calls:
            tool_name = tc["name"] if isinstance(tc, dict) else tc.name
            tool_args = tc["args"] if isinstance(tc, dict) else tc.args
            if isinstance(tc, dict):
                tc_id = tc.get("id", tool_name)
            else:
                tc_id = getattr(tc, "id", None) or tool_name

            logger.info(
                "工具调用[轮%d]: %s(%s)",
                round_idx, tool_name,
                list(tool_args.keys()) if isinstance(tool_args, dict) else tool_args,
            )
            t_tool = time.perf_counter()
            try:
                from app.tools.tool_registry import ToolPermissionLevel, tool_registry

                with tool_registry.permission_scope(ToolPermissionLevel.MODIFY):
                    tool_result = tool_registry.call(tool_name, tool_args)
                result_text = (
                    tool_result
                    if isinstance(tool_result, str)
                    else json.dumps(tool_result, ensure_ascii=False)
                )
            except Exception as e:
                result_text = f"工具执行错误: {e}"
                logger.warning("工具 %s 执行错误: %s", tool_name, e)
            tool_elapsed_ms = int((time.perf_counter() - t_tool) * 1000)
            logger.info("工具 %s 耗时 %dms", tool_name, tool_elapsed_ms)
            conversation.append(ToolMessage(content=result_text, tool_call_id=tc_id))

        resp = llm.chat(
            conversation,
            tools=available_tools if available_tools else None,
            use_code_model=use_code_model,
            model=state.get("model"),
        )

    return resp


# ====================================================================== #
#  DryRun Handler
# ====================================================================== #

def _handle_dryrun_response(
    resp: Any,
    task_id: str,
) -> List[Dict[str, str]]:
    """处理 DryRun 响应：生成占位代码并发出事件。

    Design Decision: DryRun 模式用于 API Key 未配置或额度耗尽时的演示场景。
    生成占位代码让流水线可以继续走完所有阶段（review、test 等），
    同时通过事件通知前端显示当前处于演示模式。
    """
    if resp.model != "dry-run":
        return []

    logger.warning("检测到 DryRun 响应，生成占位代码...")
    files_raw = [{
        "path": "main.py",
        "content": (
            "# [DryRun 占位代码] API Key 未配置或额度已用尽\n"
            "# 请配置有效 API Key 后重新执行以获得真实代码生成\n\n"
            '"""\nNeuroForge 多智能体协同代码生成系统\n当前处于 DryRun 模式（演示模式）\n"""\n\n\n'
            "def main():\n"
            '    """程序入口"""\n'
            '    print("Hello from NeuroForge DryRun mode!")\n'
            '    print("=" * 50)\n'
            '    print("提示：当前未配置有效 API Key，代码为占位模板。")\n'
            '    print("请在 Dashboard 配置有效的 API Key 以获得真实代码生成。")\n'
            '    print("=" * 50)\n\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
    }]
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
    return files_raw


# ====================================================================== #
#  CodeGenLLMOrchestrator
# ====================================================================== #

class CodeGenLLMOrchestrator:
    """LLM 代码生成编排器：统一管理 LLM 调用、工具循环、并行生成等交互逻辑。

    Design Decision: 将 LLM 交互逻辑从 CodeGenAgent 中分离，遵循单一职责原则。
    编排器负责：
    - 直接 LLM 调用（带工具循环）
    - 并行生成
    - 验证 + 自愈闭环
    - 迭代优化
    - DryRun 处理
    Agent 负责业务流程编排（状态管理、持久化、策略选择）。
    """

    def __init__(self, llm: Any, use_code_model: bool = True) -> None:
        self.llm = llm
        self.use_code_model = use_code_model

    def call_with_tools(
        self,
        messages: List[Dict[str, Any]],
        *,
        state: PipelineState,
        available_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[Any, int]:
        """带工具调用的 LLM 调用。

        Design Decision: 工具调用流程：
        1. 首次 LLM 调用（带 tools 参数）
        2. 如果响应有 tool_calls，进入工具回环
        3. 回环内依次执行工具、追加结果、再次调用 LLM
        4. 直到无 tool_calls 或达到上限
        """
        t0 = time.perf_counter()
        resp = self.llm.chat(
            messages,
            tools=available_tools if available_tools else None,
            use_code_model=self.use_code_model,
            model=state.get("model"),
        )
        resp = _run_tool_loop(
            self.llm, messages, resp,
            available_tools=available_tools,
            state=state,
            use_code_model=self.use_code_model,
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return resp, duration_ms

    def parallel_generation(
        self,
        messages: List[Dict[str, Any]],
        *,
        state: PipelineState,
        user_request: str,
        skill: Skill,
        available_tools: List[Dict[str, Any]],
    ) -> Tuple[Any, int]:
        """执行并行代码生成。"""
        return _run_parallel_generation(
            self.llm, messages, state, user_request, skill, available_tools,
            use_code_model=self.use_code_model,
        )

    def verify_and_heal(
        self,
        files: List[Dict[str, str]],
        *,
        state: PipelineState,
        user_request: str,
        max_retry: int,
    ) -> Tuple[List[Dict[str, str]], int, bool]:
        """执行验证 + 自愈闭环。"""
        return _verify_and_heal(
            self.llm, files,
            state=state, user_request=user_request,
            max_retry=max_retry,
            use_code_model=self.use_code_model,
        )

    def iterative_optimize(
        self,
        files: List[Dict[str, str]],
        *,
        user_request: str,
        state: PipelineState,
        max_rounds: int = 2,
    ) -> List[Dict[str, str]]:
        """执行迭代式代码优化。"""
        return _iterative_optimize(
            self.llm, files,
            user_request=user_request, state=state,
            max_rounds=max_rounds,
            use_code_model=self.use_code_model,
        )


__all__ = [
    "_build_fix_prompt",
    "_verify_and_heal",
    "_iterative_optimize",
    "_run_parallel_generation",
    "_run_tool_loop",
    "_handle_dryrun_response",
    "CodeGenLLMOrchestrator",
]

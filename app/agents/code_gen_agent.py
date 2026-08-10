from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.agents.agent_protocol import (
    AgentCapability,
    AgentProfile,
    AgentRole,
    AgentTask,
)
from app.agents.code_gen_llm import (
    CodeGenLLMOrchestrator,
    _build_fix_prompt as _llm_build_fix_prompt,
    _handle_dryrun_response,
    _iterative_optimize as _llm_iterative_optimize,
    _run_parallel_generation as _llm_run_parallel,
    _verify_and_heal as _llm_verify_and_heal,
)
from app.agents.code_gen_strategies import (
    CODE_GEN_SYSTEM_PROMPT,
    FEW_SHOT_EXAMPLES,
    PLAN_EXECUTE_SYSTEM_PROMPT,
    REACT_ENHANCED_PROMPT,
    REACT_SYSTEM_PROMPT,
    CodeGenStrategy,
    DirectGenerationStrategy,
    PlanExecuteStrategy,
    ReActStrategy,
    _build_concise_prompt,
    _collect_skill_practices,
    _guess_lang,
    _parse_file_sections,
    _retrieve_code_snippets,
    _select_tools_for_task,
    _validate_and_fix,
    select_strategy,
)
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
    normalize_state,
)
from app.tools.ast_validator import extract_first_python_code_block, validate_python_source
from app.tools.skill_registry import Skill, skill_registry
from app.tools.tool_registry import ToolPermissionLevel, tool_registry

logger = logging.getLogger(__name__)

# 模块级别名：_build_fix_prompt 从 code_gen_llm 抽取后仍保留公开导出
_build_fix_prompt = _llm_build_fix_prompt


# ====================================================================== #
#  CodeGenAgent
# ====================================================================== #

class CodeGenAgent:
    """代码生成 Agent：根据 RequirementSpec / Review 反馈产出 CodePackage。

    Design Decision: 采用策略模式 + 编排器分离架构：
    - Strategies（code_gen_strategies.py）：负责 prompt 构建和生成方法选择
      · DirectGenerationStrategy: 简单直接生成
      · ReActStrategy: 推理-行动循环
      · PlanExecuteStrategy: 先规划后执行（新增）
    - Orchestrator（code_gen_llm.py）：负责 LLM 调用、工具循环、并行生成等交互
    - Agent（本类）：负责任务编排、状态管理、持久化

    这种分离使新增生成策略（如 Tree-of-Thought、Agent-based）
    只需继承 CodeGenStrategy，无需修改 Agent 核心逻辑。

    增强特性：
    - agent_profile: 与新架构协议集成
    - to_executor(): 适配 AgentExecutor 调用
    - 并行代码生成、代码片段复用、迭代式优化
    - Few-shot 示例、技能组合、工具选择策略
    - Plan-Execute 模式支持
    """

    agent_profile: AgentProfile

    def __init__(
        self,
        *,
        llm_adapter: LLMAdapter = llm,
        use_code_model: bool = True,
        agent_profile: Optional[AgentProfile] = None,
    ) -> None:
        self.llm = llm_adapter
        self.use_code_model = use_code_model
        self.agent_profile = agent_profile or self._build_default_profile()
        self.orchestrator = CodeGenLLMOrchestrator(llm_adapter, use_code_model)

    @staticmethod
    def _build_default_profile() -> AgentProfile:
        """构建默认的 AgentProfile。"""
        return AgentProfile(
            name="code_gen_agent",
            role=AgentRole.CODER,
            capabilities=[
                AgentCapability.CODE_GENERATION,
                AgentCapability.MEMORY_ACCESS,
            ],
            system_prompt=CODE_GEN_SYSTEM_PROMPT,
            description="代码生成智能体：根据需求产出可运行的 Python 代码",
            metadata={
                "supported_languages": ["python"],
                "generation_modes": ["single", "parallel", "iterative", "plan_execute"],
            },
        )

    def to_executor(self) -> "AgentExecutorAdapter":
        """创建 AgentExecutor 适配器，用于新架构调用。"""
        return AgentExecutorAdapter(self)

    def execute_task(
        self,
        task: AgentTask,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """通过 AgentExecutor 协议执行任务。

        Args:
            task: AgentTask 实例
            context: 执行上下文

        Returns:
            执行结果字典
        """
        state = context.get("state") or context
        if not isinstance(state, dict):
            state = dict(context)
        state.setdefault("user_request", task.description)
        state.setdefault("task_id", task.task_id)
        try:
            package = self.run(state)
            return {
                "success": True,
                "code_package": package,
                "file_count": len(package.files),
                "entry_point": package.entry_point,
            }
        except Exception as e:
            logger.error("execute_task 失败: %s", e)
            return {
                "success": False,
                "error": str(e),
                "file_count": 0,
                "entry_point": "",
            }

    # ------------------------------------------------------------------ #
    #  Backward-compatibility methods (delegated to submodules)
    # ------------------------------------------------------------------ #

    def _verify_and_heal(
        self,
        files: List[Dict[str, str]],
        *,
        state: PipelineState,
        user_request: str,
        max_retry: int,
    ) -> Tuple[List[Dict[str, str]], int, bool]:
        """执行验证 + 自愈闭环（向后兼容方法）。

        Design Decision: 原始实现迁移至 code_gen_llm._verify_and_heal，
        此处保留为委托方法，确保现有测试和外部调用不受影响。
        """
        return _llm_verify_and_heal(
            self.llm, files,
            state=state, user_request=user_request,
            max_retry=max_retry,
            use_code_model=self.use_code_model,
        )

    @staticmethod
    def _build_fix_prompt(
        files: List[Dict[str, str]],
        result: Any,
        user_request: str,
    ) -> str:
        """把执行验证失败的结构化错误组装成修复 prompt（向后兼容）。"""
        return _llm_build_fix_prompt(files, result, user_request)

    def _run_parallel_generation(
        self,
        messages: List[Dict[str, Any]],
        state: PipelineState,
        user_request: str,
        skill: Skill,
        available_tools: List[Dict[str, Any]],
    ) -> Tuple[Any, int]:
        """并行代码生成（向后兼容方法）。"""
        return _llm_run_parallel(
            self.llm, messages, state, user_request, skill, available_tools,
            use_code_model=self.use_code_model,
        )

    def _iterative_optimize(
        self,
        files: List[Dict[str, str]],
        *,
        user_request: str,
        state: PipelineState,
        max_rounds: int = 2,
    ) -> List[Dict[str, str]]:
        """迭代式代码优化（向后兼容方法）。"""
        return _llm_iterative_optimize(
            self.llm, files,
            user_request=user_request, state=state,
            max_rounds=max_rounds,
            use_code_model=self.use_code_model,
        )

    def run(self, state: PipelineState) -> CodePackage:
        """运行代码生成主流程。

        Design Decision: 主流程编排分为以下阶段：
        1. 状态规范化 + 需求解析
        2. 技能匹配 + Prompt 组装
        3. 策略选择 + 代码生成（支持 direct/react/plan_execute）
        4. 工具回环处理
        5. 解析 + 校验 + DryRun 处理
        6. 迭代优化（可选）
        7. 验证 + 自愈闭环（可选）
        8. 持久化 + CodePackage 构建

        每个阶段的逻辑已模块化到 strategies 和 llm 子模块中。
        """
        task_id = state.get("task_id", "")
        t_total_start = time.perf_counter()

        original_state = state
        state = normalize_state(state)
        req: RequirementSpec = state.get("requirement") or RequirementSpec()
        review: ReviewReport = state.get("review_report") or ReviewReport()
        retry_counter = int(state.get("retry_counter") or 0)

        feedback_issues: List = state.get("review_feedback") or []
        if not feedback_issues and review.issues and retry_counter > 0:
            feedback_issues = review.issues

        # === 技能匹配 ===
        skill_id = state.get("skill_id") or ""
        user_request = state.get("user_request", "") or req.goal or ""
        skill = skill_registry.get_skill(skill_id) if skill_id else skill_registry.match_skill(user_request)

        # === 策略选择 ===
        strategy = select_strategy(state)

        logger.info(
            "代码生成使用技能: %s (%s), 策略: %s, ReAct: %s, PlanExecute: %s",
            skill.id, skill.name,
            strategy.name,
            settings.agent.react_enabled,
            state.get("plan_execute", False),
        )

        # === 代码片段复用（仅首次生成时） ===
        code_snippets: List[Dict[str, str]] = []
        if settings.memory.enabled and settings.memory.recall_on_gen and retry_counter == 0:
            code_snippets = _retrieve_code_snippets(user_request, limit=3)
            if code_snippets:
                logger.info("检索到 %d 条历史代码片段用于复用", len(code_snippets))

        # === Few-shot / 技能组合参数 ===
        use_few_shot: bool = bool(state.get("few_shot", True))
        combine_skills: Optional[List[str]] = state.get("combine_skills") or None

        # === 组装 prompt ===
        user_parts = [_build_concise_prompt(
            req, user_request,
            few_shot=use_few_shot,
            skills=combine_skills,
            code_snippets=code_snippets if retry_counter == 0 else None,
        )]

        # 长期经验（原有逻辑保留）
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

        system_prompt = strategy.get_system_prompt(skill=skill, state=state)
        messages = self.llm.to_messages(
            system=system_prompt,
            user="\n".join(user_parts),
        )

        # === 智能工具选择 ===
        tool_names = _select_tools_for_task(user_request, skill=skill)
        available_tools = (
            tool_registry.to_openai_tools(names=tool_names)
            if tool_names
            else tool_registry.to_openai_tools()
        )

        # === 调用模型 ===
        t0 = time.perf_counter()
        use_parallel: bool = bool(state.get("parallel_generation", False))
        iterative_optimize: bool = bool(state.get("iterative_optimize", False))

        try:
            if use_parallel and retry_counter == 0:
                resp, duration_ms = self.orchestrator.parallel_generation(
                    messages, state=state, user_request=user_request,
                    skill=skill, available_tools=available_tools,
                )
            elif strategy.name == "plan_execute" and retry_counter == 0:
                resp, duration_ms = strategy.generate(
                    llm=self.llm,
                    messages=messages,
                    state=state,
                    user_request=user_request,
                    skill=skill,
                    available_tools=available_tools,
                )
            else:
                resp, duration_ms = self.orchestrator.call_with_tools(
                    messages, state=state, available_tools=available_tools,
                )
        except Exception as e:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            logger.exception("代码生成 LLM 调用失败：%s", e)
            db.add_stage_log(
                task_id=task_id, stage=PipelinePhase.CODE_GEN.value,
                llm_output="", error=str(e), duration_ms=duration_ms,
            )
            return CodePackage()

        # === 解析 + 校验 ===
        files_raw = _parse_file_sections(resp.content)

        # DryRun 处理
        dryrun_files = _handle_dryrun_response(resp, task_id)
        if dryrun_files:
            files_raw = dryrun_files

        # 空文件检测 + 重试
        if not files_raw or all(not f.get("content", "").strip() for f in files_raw):
            logger.warning("首次代码生成解析为空，raw 长度=%d，重试一次...", len(resp.content))
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

        if not files_valid or all(not f.get("content", "").strip() for f in files_valid):
            logger.error("代码生成仍然为空，raw output 片段: %s", resp.content[:500])
            files_valid = [{
                "path": "generated.py",
                "content": "# 代码生成失败：LLM 返回空内容\n# 原始输出:\n" + resp.content[:2000],
                "language": "python",
            }]

        # === 迭代式优化（可选）===
        if iterative_optimize and retry_counter == 0 and files_valid:
            files_valid = self.orchestrator.iterative_optimize(
                files_valid, user_request=user_request, state=state,
            )

        # === 执行验证 + 自愈闭环 ===
        if settings.agent.verify_enabled:
            files_valid, verify_attempts, verify_healed = self.orchestrator.verify_and_heal(
                files_valid,
                state=state,
                user_request=user_request,
                max_retry=settings.agent.verify_max_retry,
            )
            status = ensure_status(original_state)
            status.verify_attempts = verify_attempts
            status.verify_healed = verify_healed
            original_state["status"] = status

        # === 持久化 ===
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

        total_duration_ms = int((time.perf_counter() - t_total_start) * 1000)
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
            install_steps=[
                "pip install -r requirements.txt"
                if any("requirements.txt" in f["path"] for f in files_valid)
                else "pip install <需要的依赖>"
            ],
            raw_llm_output=resp.content,
        )
        logger.info(
            "代码生成完成：%d 个文件，入口=%s，总耗时=%dms",
            len(package.files), package.entry_point, total_duration_ms,
        )
        return package


# ====================================================================== #
#  AgentExecutorAdapter —— 新架构适配器
# ====================================================================== #

class AgentExecutorAdapter:
    """将 CodeGenAgent 适配到 AgentExecutor 协议。

    Design Decision: 适配器模式使 CodeGenAgent 可以被 AgentPool /
    AgentCollaborationManager 直接调用，实现多智能体协同。
    提供 can_handle 能力判断，支持精确匹配、关键词映射和角色匹配。
    """

    def __init__(self, agent: CodeGenAgent) -> None:
        self._agent = agent

    @property
    def agent_profile(self) -> AgentProfile:
        return self._agent.agent_profile

    @property
    def agent(self) -> CodeGenAgent:
        return self._agent

    def execute(
        self,
        task: AgentTask,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行 AgentTask。

        Args:
            task: 要执行的 AgentTask
            context: 执行上下文

        Returns:
            执行结果字典
        """
        result = self._agent.execute_task(task, context)
        return result

    def can_handle(self, task_type: str) -> bool:
        """判断是否能处理指定类型的任务。

        匹配逻辑（按优先级）：
        1. 精确匹配：检查任务描述中是否包含自身 capabilities 的 value
        2. 关键词映射：通过内置的中英文关键词映射表匹配
        3. 角色匹配：检查任务描述中是否包含自身 role 的 value
        """
        type_lower = task_type.lower()
        for cap in self._agent.agent_profile.capabilities:
            cap_value = cap.value.lower() if hasattr(cap, "value") else str(cap).lower()
            if cap_value in type_lower or type_lower in cap_value:
                return True

        _CAPABILITY_KEYWORDS: Dict[str, AgentCapability] = {
            "代码生成": AgentCapability.CODE_GENERATION,
            "编码": AgentCapability.CODE_GENERATION,
            "生成代码": AgentCapability.CODE_GENERATION,
            "code generation": AgentCapability.CODE_GENERATION,
            "generate code": AgentCapability.CODE_GENERATION,
            "coding": AgentCapability.CODE_GENERATION,
        }
        for keyword, cap in _CAPABILITY_KEYWORDS.items():
            if keyword.lower() in type_lower and cap in self._agent.agent_profile.capabilities:
                return True

        my_role_value = self._agent.agent_profile.role.value.lower()
        if my_role_value in type_lower or type_lower in my_role_value:
            return True

        return False


# ====================================================================== #
#  LangGraph 节点函数
# ====================================================================== #

def code_gen_node(state: PipelineState) -> Dict[str, Any]:
    """LangGraph 节点：根据需求（或评审反馈）生成/修订代码包。

    支持的可选增强参数（通过 state 传入）：
    - few_shot: bool，是否注入 Few-shot 示例（默认 True）
    - combine_skills: List[str]，技能组合 ID 列表
    - parallel_generation: bool，是否启用并行生成
    - iterative_optimize: bool，是否启用迭代优化
    - enhanced_react: bool，是否使用增强 ReAct 模式
    - plan_execute: bool，是否使用 Plan-Execute 模式
    - strategy: str，显式指定策略（direct/react/plan_execute）
    - max_tool_rounds: int，最大工具调用轮次

    Design Decision: 节点函数保持无状态特性，每次调用创建新的
    CodeGenAgent 实例。这确保了 LangGraph 节点的幂等性，
    避免并发任务间的状态串扰。
    """
    task_id = state.get("task_id", "")
    agent = CodeGenAgent()
    pkg = agent.run(state)

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


# ====================================================================== #
#  Public API Functions
# ====================================================================== #

def build_concise_prompt(
    req: RequirementSpec,
    user_request: str,
    *,
    few_shot: bool = False,
    skills: Optional[List[str]] = None,
    code_snippets: Optional[List[Dict[str, str]]] = None,
) -> str:
    """公共 API: 从 RequirementSpec 生成精简 prompt。"""
    return _build_concise_prompt(
        req, user_request,
        few_shot=few_shot,
        skills=skills,
        code_snippets=code_snippets,
    )


def select_tools_for_task(
    user_request: str,
    *,
    skill: Optional[Skill] = None,
) -> List[str]:
    """公共 API: 根据任务类型自动选择最佳工具集合。"""
    return _select_tools_for_task(user_request, skill=skill)


def retrieve_code_snippets(query: str, *, limit: int = 3) -> List[Dict[str, str]]:
    """公共 API: 从记忆系统检索历史优秀代码片段。"""
    return _retrieve_code_snippets(query, limit=limit)


# ====================================================================== #
#  Re-exports (backward compatibility)
# ====================================================================== #

__all__ = [
    # Agent classes
    "CodeGenAgent",
    "AgentExecutorAdapter",
    "code_gen_node",
    # Public models
    "CodePackage",
    "GeneratedFile",
    # Prompt constants
    "CODE_GEN_SYSTEM_PROMPT",
    "REACT_SYSTEM_PROMPT",
    "REACT_ENHANCED_PROMPT",
    "PLAN_EXECUTE_SYSTEM_PROMPT",
    "FEW_SHOT_EXAMPLES",
    # Strategy classes (from code_gen_strategies)
    "CodeGenStrategy",
    "DirectGenerationStrategy",
    "ReActStrategy",
    "PlanExecuteStrategy",
    # Orchestrator (from code_gen_llm)
    "CodeGenLLMOrchestrator",
    # Public API functions
    "build_concise_prompt",
    "select_tools_for_task",
    "retrieve_code_snippets",
    # Internal utilities (re-exported for test compatibility)
    "_parse_file_sections",
    "_validate_and_fix",
    "_guess_lang",
    "_build_fix_prompt",
]

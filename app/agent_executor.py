"""
agent_executor: 企业级智能体执行层核心实现。

本模块连接编排引擎（Orchestrator）与现有业务逻辑（Agent 实现、工具注册、
记忆系统、事件总线），提供统一的智能体执行抽象。

核心组件：
- AgentTaskResult: 执行结果数据类
- AgentExecutor: 执行器基类（含工具调用回环、Function Calling、钩子方法）
- ArchitectExecutor / CoderExecutor / ReviewerExecutor / TesterExecutor / OrchestratorExecutor: 具体执行器
- AgentPool: 智能体池（注册 / 获取 / 释放 / 匹配）
- AgentCollaborationManager: 协作管理器（广播 / 链式 / 并行 / 投票）

与现有模块的衔接：
- app.agents.agent_protocol  → AgentProfile / AgentTask / AgentRole / AgentCapability
- app.core.llm_adapter      → LLMAdapter / llm 单例
- app.core.memory_system    → MemoryFacade / memory_system 单例
- app.core.events           → EventBus / event_bus 单例
- app.tools.tool_registry   → ToolRegistry / tool_registry 单例
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage

from app.agents.agent_protocol import (
    AgentCapability,
    AgentProfile,
    AgentRole,
    AgentTask,
)
from app.core.events import EventType, event_bus
from app.core.llm_adapter import LLMAdapter, ChatResponse, llm
from app.core.memory_system import MemoryFacade, memory_system
from app.tools.tool_registry import tool_registry

logger = logging.getLogger(__name__)


# ====================================================================== #
#  AgentTaskResult —— 执行结果数据类
# ====================================================================== #


@dataclass
class AgentTaskResult:
    """智能体任务执行结果。

    Attributes:
        task_id:        关联的任务 ID
        success:        是否执行成功
        output:         执行输出（文本或结构化内容）
        artifacts:      产出物字典（如生成的文件、审查报告等）
        metrics:        执行指标（token 用量、耗时等）
        error:          错误信息（失败时填充）
        execution_time: 执行耗时（秒）
    """

    task_id: str = ""
    success: bool = False
    output: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    execution_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "task_id": self.task_id,
            "success": self.success,
            "output": self.output,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "error": self.error,
            "execution_time": round(self.execution_time, 3),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentTaskResult:
        """从字典反序列化。"""
        return cls(
            task_id=data.get("task_id", ""),
            success=data.get("success", False),
            output=data.get("output", ""),
            artifacts=data.get("artifacts", {}),
            metrics=data.get("metrics", {}),
            error=data.get("error", ""),
            execution_time=data.get("execution_time", 0.0),
        )


# ====================================================================== #
#  AgentExecutor —— 执行器基类
# ====================================================================== #


class AgentExecutor:
    """智能体执行器基类。

    提供智能体任务执行的核心框架，包含：
    - LLM 调用与 Function Calling
    - 工具调用回环（Tool Calling Loop）
    - 执行前/后钩子（before_execute / after_execute）
    - 事件总线通知
    - 记忆系统读写

    子类需重写 `_execute_core` 以实现具体业务逻辑。

    Attributes:
        agent_profile: 智能体画像
        llm_adapter:   LLM 适配器实例
        memory_facade: 记忆系统门面
        event_bus:     事件总线实例
        tool_registry: 工具注册表实例
        max_tool_rounds: 最大工具调用轮次（防止无限循环）
    """

    def __init__(
        self,
        agent_profile: AgentProfile,
        llm_adapter: Optional[LLMAdapter] = None,
        memory_facade: Optional[MemoryFacade] = None,
        max_tool_rounds: int = 3,
    ) -> None:
        self.agent_profile = agent_profile
        self.llm_adapter = llm_adapter or llm
        self.memory_facade = memory_facade or memory_system
        self.event_bus = event_bus
        self.tool_registry = tool_registry
        self.max_tool_rounds = max_tool_rounds
        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )

    # ------------------------------------------------------------------ #
    #  核心方法
    # ------------------------------------------------------------------ #

    def execute_task(self, task: AgentTask, context: Dict[str, Any]) -> AgentTaskResult:
        """执行智能体任务。

        完整执行流程：
        1. 触发 TASK_STARTED 事件
        2. 调用 before_execute 钩子
        3. 检查是否可处理该任务
        4. 调用 _execute_core 执行核心逻辑
        5. 调用 after_execute 钩子
        6. 触发完成/失败事件
        7. 记录情景记忆

        Args:
            task:    待执行的 AgentTask 实例
            context: 执行上下文（包含 user_request、历史结果等）

        Returns:
            AgentTaskResult 执行结果
        """
        start_time = time.monotonic()
        self.event_bus.emit(
            EventType.TASK_STARTED,
            data={
                "task_id": task.task_id,
                "agent": self.agent_profile.name,
                "role": self.agent_profile.role.value,
                "description": task.description,
            },
            task_id=task.task_id,
        )

        self.before_execute(task, context)

        if not self.can_handle(task.description):
            error_msg = (
                f"执行器 {self.agent_profile.name} 无法处理任务: {task.description[:100]}"
            )
            self._logger.warning(error_msg)
            result = AgentTaskResult(
                task_id=task.task_id,
                success=False,
                error=error_msg,
                execution_time=time.monotonic() - start_time,
            )
            self.after_execute(result, context)
            self._emit_failure(task, result)
            return result

        try:
            self._logger.info(
                "开始执行任务: task_id=%s, agent=%s, desc=%s",
                task.task_id, self.agent_profile.name, task.description[:80],
            )
            result = self._execute_core(task, context)
            result.execution_time = time.monotonic() - start_time
            result.task_id = task.task_id

            self.after_execute(result, context)

            if result.success:
                self._emit_success(task, result)
                self._record_episodic(task, result)
            else:
                self._emit_failure(task, result)

            return result

        except Exception as exc:
            execution_time = time.monotonic() - start_time
            self._logger.exception(
                "任务执行异常: task_id=%s, error=%s", task.task_id, exc,
            )
            result = AgentTaskResult(
                task_id=task.task_id,
                success=False,
                error=str(exc),
                execution_time=execution_time,
            )
            self.after_execute(result, context)
            self._emit_failure(task, result)
            return result

    def can_handle(self, task_type: str) -> bool:
        """判断当前执行器是否能处理指定类型的任务。

        匹配逻辑（按优先级）：
        1. 精确匹配：检查任务描述中是否包含自身 capabilities 的 value
        2. 关键词映射：通过内置的中英文关键词映射表匹配
        3. 角色匹配：检查任务描述中是否包含自身 role 的 value 或对应中文关键词

        Args:
            task_type: 任务类型描述或关键词

        Returns:
            True 表示可处理
        """
        type_lower = task_type.lower()
        my_cap_values: Set[str] = set()
        for cap in self.agent_profile.capabilities:
            cap_value = cap.value.lower() if hasattr(cap, "value") else str(cap).lower()
            my_cap_values.add(cap_value)
            if cap_value in type_lower or type_lower in cap_value:
                return True

        my_role_value = self.agent_profile.role.value.lower()
        if my_role_value in type_lower or type_lower in my_role_value:
            return True

        for keyword, cap in self._CAPABILITY_KEYWORDS.items():
            kw_lower = keyword.lower()
            if kw_lower in type_lower or type_lower in kw_lower:
                if cap in self.agent_profile.capabilities:
                    return True

        for keyword, role in self._ROLE_KEYWORDS.items():
            kw_lower = keyword.lower()
            if kw_lower in type_lower or type_lower in kw_lower:
                if role == self.agent_profile.role:
                    return True

        return False

    def get_system_prompt(self) -> str:
        """获取当前执行器的系统提示词。

        优先使用 AgentProfile 中配置的 system_prompt，
        否则返回默认的角色提示。

        Returns:
            系统提示词字符串
        """
        if self.agent_profile.system_prompt:
            return self.agent_profile.system_prompt
        role = self.agent_profile.role.value
        name = self.agent_profile.name
        return f"你是{name}，一个专业的{role}角色智能体。请根据用户需求完成任务。"

    # ------------------------------------------------------------------ #
    #  钩子方法（子类或外部调用者可覆盖）
    # ------------------------------------------------------------------ #

    # 能力关键词映射表：将中英文关键词映射到 AgentCapability
    _CAPABILITY_KEYWORDS: Dict[str, AgentCapability] = {
        "code_generation": AgentCapability.CODE_GENERATION,
        "代码生成": AgentCapability.CODE_GENERATION,
        "编码": AgentCapability.CODE_GENERATION,
        "写代码": AgentCapability.CODE_GENERATION,
        "code_review": AgentCapability.CODE_REVIEW,
        "代码审查": AgentCapability.CODE_REVIEW,
        "代码评审": AgentCapability.CODE_REVIEW,
        "审查": AgentCapability.CODE_REVIEW,
        "testing": AgentCapability.TESTING,
        "测试": AgentCapability.TESTING,
        "test": AgentCapability.TESTING,
        "analysis": AgentCapability.ANALYSIS,
        "分析": AgentCapability.ANALYSIS,
        "需求": AgentCapability.ANALYSIS,
        "planning": AgentCapability.PLANNING,
        "规划": AgentCapability.PLANNING,
        "编排": AgentCapability.PLANNING,
        "任务分配": AgentCapability.PLANNING,
        "communication": AgentCapability.COMMUNICATION,
        "协作": AgentCapability.COMMUNICATION,
        "沟通": AgentCapability.COMMUNICATION,
    }

    # 角色关键词映射表：将中英文关键词映射到 AgentRole
    _ROLE_KEYWORDS: Dict[str, AgentRole] = {
        "architect": AgentRole.ARCHITECT,
        "架构师": AgentRole.ARCHITECT,
        "需求分析": AgentRole.ARCHITECT,
        "coder": AgentRole.CODER,
        "编码者": AgentRole.CODER,
        "developer": AgentRole.CODER,
        "reviewer": AgentRole.REVIEWER,
        "评审者": AgentRole.REVIEWER,
        "审查": AgentRole.REVIEWER,
        "tester": AgentRole.TESTER,
        "测试者": AgentRole.TESTER,
        "测试工程师": AgentRole.TESTER,
        "orchestrator": AgentRole.ORCHESTRATOR,
        "编排者": AgentRole.ORCHESTRATOR,
        "协调": AgentRole.ORCHESTRATOR,
    }

    def before_execute(self, task: AgentTask, context: Dict[str, Any]) -> None:
        """执行前钩子。

        默认行为：将任务信息写入工作记忆。
        子类可重写以注入额外的上下文准备逻辑。

        Args:
            task:    即将执行的任务
            context: 执行上下文
        """
        self.memory_facade.write(
            "working",
            f"task_{task.task_id}_start",
            {"description": task.description, "assigned_to": task.assigned_to},
        )
        self._logger.debug("before_execute: task_id=%s", task.task_id)

    def after_execute(self, result: AgentTaskResult, context: Dict[str, Any]) -> None:
        """执行后钩子。

        默认行为：将执行结果写入工作记忆。
        子类可重写以注入额外的后处理逻辑（如结果持久化、状态通知等）。

        Args:
            result:  执行结果
            context: 执行上下文
        """
        self.memory_facade.write(
            "working",
            f"task_{result.task_id}_end",
            {
                "success": result.success,
                "execution_time": result.execution_time,
                "error": result.error,
            },
        )
        self._logger.debug(
            "after_execute: task_id=%s, success=%s", result.task_id, result.success,
        )

    # ------------------------------------------------------------------ #
    #  工具调用回环（Function Calling）
    # ------------------------------------------------------------------ #

    def run_tool_loop(
        self,
        messages: List[BaseMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        use_code_model: bool = False,
        model: Optional[str] = None,
    ) -> ChatResponse:
        """执行工具调用回环（ReAct / Function Calling 模式）。

        流程：
        1. 调用 LLM（带工具定义）
        2. 若返回 tool_calls，则依次执行工具
        3. 将工具结果作为 ToolMessage 追加到对话
        4. 再次调用 LLM，重复直到无工具调用或达到上限

        Args:
            messages:      初始消息列表
            tools:          工具定义列表（OpenAI Function Calling 格式）
            use_code_model: 是否使用代码专用模型
            model:          指定模型（可选）

        Returns:
            最终的 ChatResponse
        """
        conversation = list(messages)
        available_tools = tools or self.tool_registry.to_openai_tools()

        resp = self.llm_adapter.chat(
            conversation,
            tools=available_tools if available_tools else None,
            use_code_model=use_code_model,
            model=model,
        )

        for _ in range(self.max_tool_rounds):
            if not resp.tool_calls:
                break

            ai_msg = AIMessage(content=resp.content, tool_calls=resp.tool_calls)
            conversation.append(ai_msg)

            for tc in resp.tool_calls:
                tool_name = tc["name"] if isinstance(tc, dict) else tc.name
                tool_args = tc["args"] if isinstance(tc, dict) else tc.args
                tc_id = (
                    tc.get("id", tool_name)
                    if isinstance(tc, dict)
                    else getattr(tc, "id", None) or tool_name
                )

                self._logger.info(
                    "工具调用: %s(%s)",
                    tool_name,
                    list(tool_args.keys()) if isinstance(tool_args, dict) else tool_args,
                )

                try:
                    tool_result = self.tool_registry.call(tool_name, tool_args)
                    result_text = (
                        tool_result
                        if isinstance(tool_result, str)
                        else json.dumps(tool_result, ensure_ascii=False)
                    )
                except Exception as e:
                    result_text = f"工具执行错误: {e}"
                    self._logger.warning("工具 %s 执行失败: %s", tool_name, e)

                conversation.append(ToolMessage(content=result_text, tool_call_id=tc_id))

            resp = self.llm_adapter.chat(
                conversation,
                tools=available_tools if available_tools else None,
                use_code_model=use_code_model,
                model=model,
            )

        return resp

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _execute_core(
        self, task: AgentTask, context: Dict[str, Any]
    ) -> AgentTaskResult:
        """核心执行逻辑（子类必须重写）。

        Args:
            task:    待执行的任务
            context: 执行上下文

        Returns:
            AgentTaskResult 执行结果

        Raises:
            NotImplementedError: 子类未实现时抛出
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 必须实现 _execute_core 方法"
        )

    def _build_messages(
        self,
        task: AgentTask,
        context: Dict[str, Any],
        extra_system: str = "",
    ) -> List[BaseMessage]:
        """构建 LLM 消息列表。

        Args:
            task:         当前任务
            context:      执行上下文
            extra_system: 额外的系统提示补充

        Returns:
            消息列表
        """
        system_parts = [self.get_system_prompt()]
        if extra_system:
            system_parts.append(extra_system)

        user_parts = [task.description]
        if context:
            context_str = json.dumps(context, ensure_ascii=False, default=str)
            user_parts.append(f"\n\n上下文信息:\n{context_str[:2000]}")

        return self.llm_adapter.to_messages(
            system="\n\n".join(system_parts),
            user="\n".join(user_parts),
        )

    def _emit_success(self, task: AgentTask, result: AgentTaskResult) -> None:
        """发出执行成功事件。"""
        self.event_bus.emit(
            EventType.NODE_COMPLETED,
            data={
                "task_id": task.task_id,
                "agent": self.agent_profile.name,
                "execution_time": result.execution_time,
                "metrics": result.metrics,
            },
            task_id=task.task_id,
        )

    def _emit_failure(self, task: AgentTask, result: AgentTaskResult) -> None:
        """发出执行失败事件。"""
        self.event_bus.emit(
            EventType.NODE_FAILED,
            data={
                "task_id": task.task_id,
                "agent": self.agent_profile.name,
                "error": result.error,
                "execution_time": result.execution_time,
            },
            task_id=task.task_id,
        )

    def _record_episodic(self, task: AgentTask, result: AgentTaskResult) -> None:
        """记录情景记忆事件。"""
        try:
            self.memory_facade.write(
                "episodic",
                "milestone" if result.success else "error",
                {
                    "task_id": task.task_id,
                    "agent": self.agent_profile.name,
                    "success": result.success,
                    "description": task.description[:200],
                    "error": result.error[:500] if result.error else "",
                    "execution_time": result.execution_time,
                },
            )
        except Exception as e:
            self._logger.debug("情景记忆记录失败（不影响主流程）: %s", e)


# ====================================================================== #
#  具体执行器子类
# ====================================================================== #


class ArchitectExecutor(AgentExecutor):
    """需求分析师 / 架构师执行器。

    负责需求拆解、技术选型、方案设计。接收原始需求描述，
    输出结构化的需求规格说明和技术方案。

    适用场景：
    - 将模糊的用户需求转化为明确的功能规格
    - 根据需求推荐技术栈和架构模式
    - 设计模块划分和接口定义
    """

    def __init__(self, **kwargs: Any) -> None:
        profile = AgentProfile(
            name=kwargs.pop("name", "architect_executor"),
            role=AgentRole.ARCHITECT,
            capabilities=[
                AgentCapability.ANALYSIS,
                AgentCapability.PLANNING,
                AgentCapability.COMMUNICATION,
            ],
            system_prompt=kwargs.pop(
                "system_prompt",
                (
                    "你是一位资深的软件架构师。你的职责是：\n"
                    "1. 将用户的模糊需求拆解为清晰的功能规格说明\n"
                    "2. 根据需求特点推荐合适的技术栈和架构模式\n"
                    "3. 设计合理的模块划分与接口定义\n"
                    "4. 识别潜在风险并给出应对建议\n\n"
                    "请输出结构化的需求分析结果，包含：功能需求列表、技术选型建议、"
                    "模块划分方案、接口设计要点、风险评估。"
                ),
            ),
            description="需求分析师：需求拆解、技术选型、方案设计",
        )
        super().__init__(agent_profile=profile, **kwargs)

    def _execute_core(
        self, task: AgentTask, context: Dict[str, Any]
    ) -> AgentTaskResult:
        """执行需求分析与方案设计。"""
        messages = self._build_messages(
            task,
            context,
            extra_system="请以结构化 JSON 格式输出需求分析结果。",
        )

        try:
            resp = self.run_tool_loop(messages, use_code_model=False)

            artifacts: Dict[str, Any] = {
                "analysis_result": resp.content,
                "raw_response": resp.raw,
            }

            return AgentTaskResult(
                task_id=task.task_id,
                success=True,
                output=resp.content,
                artifacts=artifacts,
                metrics={
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                    "model": resp.model,
                },
            )
        except Exception as e:
            return AgentTaskResult(
                task_id=task.task_id,
                success=False,
                error=f"架构分析失败: {e}",
            )


class CoderExecutor(AgentExecutor):
    """代码生成师执行器。

    负责代码生成、文件操作、代码验证。接收需求规格说明，
    输出可运行的代码文件包。

    适用场景：
    - 根据需求生成 Python 代码
    - 多文件代码生成与组织
    - 代码语法验证与修复
    """

    def __init__(self, **kwargs: Any) -> None:
        profile = AgentProfile(
            name=kwargs.pop("name", "coder_executor"),
            role=AgentRole.CODER,
            capabilities=[
                AgentCapability.CODE_GENERATION,
                AgentCapability.ANALYSIS,
            ],
            system_prompt=kwargs.pop(
                "system_prompt",
                (
                    "你是一位资深的 Python 工程师。你的职责是根据需求产出可运行代码。\n"
                    "输出格式：用 ### FILE: 路径 分隔每个文件，文件内直接放源码。\n"
                    "规则：\n"
                    "1. 生成合法的 Python 代码，包含类型注解和 docstring\n"
                    "2. 实现异常处理和边界条件检查\n"
                    "3. 禁止使用 eval/exec 等危险函数\n"
                    "4. 只输出代码，不要额外解释\n"
                    "5. 使用标准库优先，第三方库需在文件顶部注释说明"
                ),
            ),
            description="代码生成师：代码生成、文件操作、代码验证",
        )
        super().__init__(agent_profile=profile, **kwargs)

    def _execute_core(
        self, task: AgentTask, context: Dict[str, Any]
    ) -> AgentTaskResult:
        """执行代码生成。"""
        messages = self._build_messages(
            task,
            context,
            extra_system=(
                "请直接输出代码，用 ### FILE: 路径 分隔文件。\n"
                "确保代码可运行，包含必要的导入和异常处理。"
            ),
        )

        try:
            resp = self.run_tool_loop(messages, use_code_model=True)

            artifacts: Dict[str, Any] = {
                "generated_code": resp.content,
                "raw_response": resp.raw,
            }

            return AgentTaskResult(
                task_id=task.task_id,
                success=True,
                output=resp.content,
                artifacts=artifacts,
                metrics={
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                    "model": resp.model,
                },
            )
        except Exception as e:
            return AgentTaskResult(
                task_id=task.task_id,
                success=False,
                error=f"代码生成失败: {e}",
            )


class ReviewerExecutor(AgentExecutor):
    """代码审查师执行器。

    负责代码审查、质量检查、安全扫描。接收生成的代码，
    输出结构化的审查报告和改进建议。

    适用场景：
    - 代码质量审查（命名、结构、复杂度）
    - 安全漏洞扫描（注入、越权、敏感信息泄露）
    - 最佳实践检查（类型注解、文档字符串、异常处理）
    """

    def __init__(self, **kwargs: Any) -> None:
        profile = AgentProfile(
            name=kwargs.pop("name", "reviewer_executor"),
            role=AgentRole.REVIEWER,
            capabilities=[
                AgentCapability.CODE_REVIEW,
                AgentCapability.ANALYSIS,
            ],
            system_prompt=kwargs.pop(
                "system_prompt",
                (
                    "你是一位严谨的代码审查专家。请对提交的代码进行全面审查，包括：\n"
                    "1. **安全性**：检查是否存在 SQL 注入、命令注入、路径遍历等漏洞\n"
                    "2. **代码质量**：检查命名规范、函数长度、嵌套层级、重复代码\n"
                    "3. **最佳实践**：检查类型注解、文档字符串、异常处理、资源管理\n"
                    "4. **性能**：检查潜在的性能问题（N+1 查询、不必要的拷贝等）\n\n"
                    "请输出结构化的审查报告，包含：问题列表（严重程度、文件位置、具体描述、修复建议）、"
                    "总体评价、通过/驳回判定。"
                ),
            ),
            description="代码审查师：代码审查、质量检查、安全扫描",
        )
        super().__init__(agent_profile=profile, **kwargs)

    def _execute_core(
        self, task: AgentTask, context: Dict[str, Any]
    ) -> AgentTaskResult:
        """执行代码审查。"""
        messages = self._build_messages(
            task,
            context,
            extra_system=(
                "请以结构化 JSON 格式输出审查报告。"
                "对每个问题标注严重程度（critical/high/medium/low）。"
            ),
        )

        try:
            resp = self.run_tool_loop(messages, use_code_model=False)

            artifacts: Dict[str, Any] = {
                "review_report": resp.content,
                "raw_response": resp.raw,
            }

            return AgentTaskResult(
                task_id=task.task_id,
                success=True,
                output=resp.content,
                artifacts=artifacts,
                metrics={
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                    "model": resp.model,
                },
            )
        except Exception as e:
            return AgentTaskResult(
                task_id=task.task_id,
                success=False,
                error=f"代码审查失败: {e}",
            )


class TesterExecutor(AgentExecutor):
    """测试工程师执行器。

    负责测试用例生成、测试执行、覆盖率分析。接收需求和代码，
    输出测试用例和执行结果。

    适用场景：
    - 根据需求和代码生成单元测试用例
    - 自动执行测试并分析覆盖率
    - 边界条件和异常场景测试设计
    """

    def __init__(self, **kwargs: Any) -> None:
        profile = AgentProfile(
            name=kwargs.pop("name", "tester_executor"),
            role=AgentRole.TESTER,
            capabilities=[
                AgentCapability.TESTING,
                AgentCapability.ANALYSIS,
            ],
            system_prompt=kwargs.pop(
                "system_prompt",
                (
                    "你是一位专业的测试工程师。请根据需求描述和代码生成全面的测试用例。\n"
                    "测试用例应覆盖：\n"
                    "1. **正常路径**：验证核心功能的正确性\n"
                    "2. **边界条件**：极值、空值、特殊字符\n"
                    "3. **异常场景**：无效输入、资源不足、网络故障\n"
                    "4. **性能测试**：大数据量、高并发场景\n\n"
                    "请输出 Python pytest 格式的测试代码。"
                ),
            ),
            description="测试工程师：测试用例生成、测试执行、覆盖率分析",
        )
        super().__init__(agent_profile=profile, **kwargs)

    def _execute_core(
        self, task: AgentTask, context: Dict[str, Any]
    ) -> AgentTaskResult:
        """执行测试用例生成。"""
        messages = self._build_messages(
            task,
            context,
            extra_system=(
                "请输出 Python pytest 测试代码，用 ### FILE: 路径 分隔文件。"
                "确保测试可独立运行，包含 fixtures 和 parametrize。"
            ),
        )

        try:
            resp = self.run_tool_loop(messages, use_code_model=True)

            artifacts: Dict[str, Any] = {
                "test_code": resp.content,
                "raw_response": resp.raw,
            }

            return AgentTaskResult(
                task_id=task.task_id,
                success=True,
                output=resp.content,
                artifacts=artifacts,
                metrics={
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                    "model": resp.model,
                },
            )
        except Exception as e:
            return AgentTaskResult(
                task_id=task.task_id,
                success=False,
                error=f"测试生成失败: {e}",
            )


class OrchestratorExecutor(AgentExecutor):
    """编排协调师执行器。

    负责任务分配、进度监控、冲突解决。作为多智能体系统的"大脑"，
    协调其他执行器完成复杂任务。

    适用场景：
    - 将复杂需求拆解为子任务并分配给合适的执行器
    - 监控各执行器的执行进度
    - 在执行器间解决冲突并优化执行顺序
    - 汇总各执行器结果形成最终交付物
    """

    def __init__(self, **kwargs: Any) -> None:
        profile = AgentProfile(
            name=kwargs.pop("name", "orchestrator_executor"),
            role=AgentRole.ORCHESTRATOR,
            capabilities=[
                AgentCapability.PLANNING,
                AgentCapability.COMMUNICATION,
                AgentCapability.ANALYSIS,
            ],
            system_prompt=kwargs.pop(
                "system_prompt",
                (
                    "你是一位智能体编排协调师。你的职责是：\n"
                    "1. 将复杂需求拆解为可独立执行的子任务\n"
                    "2. 为每个子任务匹配合适的角色（架构师/编码师/审查师/测试师）\n"
                    "3. 安排子任务的执行顺序（考虑依赖关系）\n"
                    "4. 解决子任务之间的冲突与依赖\n"
                    "5. 汇总各子任务结果形成完整的交付物\n\n"
                    "请输出结构化的任务分配计划，每个子任务包含：任务描述、"
                    "分配角色、依赖关系、优先级。"
                ),
            ),
            description="编排协调师：任务分配、进度监控、冲突解决",
        )
        super().__init__(agent_profile=profile, **kwargs)

    def _execute_core(
        self, task: AgentTask, context: Dict[str, Any]
    ) -> AgentTaskResult:
        """执行编排协调。"""
        messages = self._build_messages(
            task,
            context,
            extra_system=(
                "请以结构化 JSON 输出任务分配计划。"
                "每个子任务字段：task_id, description, role(architect/coder/reviewer/tester), "
                "dependencies, priority。"
            ),
        )

        try:
            resp = self.run_tool_loop(messages, use_code_model=False)

            artifacts: Dict[str, Any] = {
                "orchestration_plan": resp.content,
                "raw_response": resp.raw,
            }

            return AgentTaskResult(
                task_id=task.task_id,
                success=True,
                output=resp.content,
                artifacts=artifacts,
                metrics={
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                    "model": resp.model,
                },
            )
        except Exception as e:
            return AgentTaskResult(
                task_id=task.task_id,
                success=False,
                error=f"编排协调失败: {e}",
            )


# ====================================================================== #
#  AgentPool —— 智能体池
# ====================================================================== #


class AgentPool:
    """智能体池：管理多个 AgentExecutor 实例。

    提供执行器的注册、获取、释放、能力匹配等功能。
    支持按角色/能力自动选择最佳执行器。

    Example::

        pool = AgentPool()
        pool.register(ArchitectExecutor())
        pool.register(CoderExecutor())

        executor = pool.acquire(AgentRole.CODER)
        if executor:
            result = executor.execute_task(task, context)
            pool.release(executor)

    Attributes:
        _executors: 已注册的执行器字典 {name: AgentExecutor}
        _role_index: 角色到执行器名称的倒排索引
    """

    def __init__(self) -> None:
        self._executors: Dict[str, AgentExecutor] = {}
        self._role_index: Dict[str, Set[str]] = {}
        self._logger = logging.getLogger(f"{__name__}.AgentPool")

    def register(self, executor: AgentExecutor) -> str:
        """注册一个执行器到池中。

        若同名执行器已存在，将被替换。

        Args:
            executor: AgentExecutor 实例

        Returns:
            执行器名称（即注册 key）
        """
        name = executor.agent_profile.name
        self._executors[name] = executor

        role = executor.agent_profile.role.value
        if role not in self._role_index:
            self._role_index[role] = set()
        self._role_index[role].add(name)

        self._logger.info(
            "注册执行器: name=%s, role=%s, capabilities=%s",
            name, role,
            [c.value for c in executor.agent_profile.capabilities],
        )
        return name

    def unregister(self, name: str) -> Optional[AgentExecutor]:
        """从池中注销一个执行器。

        Args:
            name: 执行器名称

        Returns:
            被注销的执行器，不存在返回 None
        """
        executor = self._executors.pop(name, None)
        if executor:
            role = executor.agent_profile.role.value
            if role in self._role_index and name in self._role_index[role]:
                self._role_index[role].discard(name)
                if not self._role_index[role]:
                    del self._role_index[role]
            self._logger.info("注销执行器: name=%s", name)
        return executor

    def acquire(
        self,
        role: Optional[AgentRole] = None,
        capability: Optional[AgentCapability] = None,
        task_type: Optional[str] = None,
    ) -> Optional[AgentExecutor]:
        """获取最佳匹配的执行器。

        匹配优先级：
        1. 若指定 role → 返回该角色下的任意执行器
        2. 若指定 capability → 返回具备该能力的执行器
        3. 若指定 task_type → 遍历所有执行器，返回 can_handle 返回 True 的第一个
        4. 无参数 → 返回第一个可用的执行器

        Args:
            role:        目标角色
            capability:  目标能力
            task_type:   任务类型描述

        Returns:
            匹配的 AgentExecutor，未找到返回 None
        """
        if role is not None:
            executors = self._role_index.get(role.value, set())
            if executors:
                name = next(iter(executors))
                self._logger.debug("按角色获取: role=%s → %s", role.value, name)
                return self._executors.get(name)

        if capability is not None:
            cap_value = capability.value if hasattr(capability, "value") else str(capability)
            for name, executor in self._executors.items():
                for cap in executor.agent_profile.capabilities:
                    cap_v = cap.value if hasattr(cap, "value") else str(cap)
                    if cap_v == cap_value:
                        self._logger.debug(
                            "按能力获取: capability=%s → %s", cap_value, name,
                        )
                        return executor

        if task_type is not None:
            for name, executor in self._executors.items():
                if executor.can_handle(task_type):
                    self._logger.debug(
                        "按任务类型获取: task_type=%s → %s", task_type, name,
                    )
                    return executor

        if self._executors:
            name = next(iter(self._executors))
            self._logger.debug("获取默认执行器: %s", name)
            return self._executors.get(name)

        self._logger.warning("AgentPool 为空，无法获取执行器")
        return None

    def release(self, executor: AgentExecutor) -> None:
        """释放执行器（当前实现为占位，后续可扩展为连接池复用）。

        Args:
            executor: 要释放的执行器
        """
        self._logger.debug("释放执行器: %s", executor.agent_profile.name)

    def get(self, name: str) -> Optional[AgentExecutor]:
        """按名称获取执行器。

        Args:
            name: 执行器名称

        Returns:
            AgentExecutor 或 None
        """
        return self._executors.get(name)

    def list_available(self) -> List[Dict[str, Any]]:
        """列出池中所有可用执行器的信息。

        Returns:
            执行器信息列表，每项包含 name / role / capabilities / description
        """
        return [
            {
                "name": prof.name,
                "role": prof.role.value,
                "capabilities": [c.value for c in prof.capabilities],
                "description": prof.description,
            }
            for prof in (e.agent_profile for e in self._executors.values())
        ]

    @property
    def count(self) -> int:
        """池中执行器数量。"""
        return len(self._executors)

    def __len__(self) -> int:
        return len(self._executors)

    def __contains__(self, name: str) -> bool:
        return name in self._executors

    def __repr__(self) -> str:
        roles: Dict[str, int] = {}
        for e in self._executors.values():
            r = e.agent_profile.role.value
            roles[r] = roles.get(r, 0) + 1
        return f"AgentPool(executors={len(self._executors)}, roles={roles})"


# ====================================================================== #
#  AgentCollaborationManager —— 协作管理器
# ====================================================================== #


class AgentCollaborationManager:
    """智能体协作管理器。

    提供多智能体之间的协作机制：
    - broadcast_task:   广播任务到多个角色
    - chain_execution:  链式执行（A 的输出作为 B 的输入）
    - parallel_execution: 并行执行（多个执行器同时处理不同任务）
    - vote_decision:    投票决策（多个执行器对同一问题投票）

    Attributes:
        agent_pool: 关联的智能体池
    """

    def __init__(self, agent_pool: Optional[AgentPool] = None) -> None:
        self.agent_pool = agent_pool or AgentPool()
        self._logger = logging.getLogger(
            f"{__name__}.AgentCollaborationManager"
        )

    def broadcast_task(
        self,
        task: AgentTask,
        roles: List[AgentRole],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, AgentTaskResult]:
        """广播任务到多个角色执行器。

        任务将同时发送给所有指定角色的执行器，各自独立执行。

        Args:
            task:    要广播的任务
            roles:   目标角色列表
            context: 共享上下文

        Returns:
            {执行器名称: AgentTaskResult} 结果字典
        """
        context = context or {}
        results: Dict[str, AgentTaskResult] = {}

        for role in roles:
            executor = self.agent_pool.acquire(role=role)
            if executor is None:
                self._logger.warning(
                    "广播失败：角色 %s 无可用执行器", role.value,
                )
                results[role.value] = AgentTaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=f"角色 {role.value} 无可用执行器",
                )
                continue

            self._logger.info(
                "广播任务: task=%s → role=%s(%s)",
                task.task_id, role.value, executor.agent_profile.name,
            )
            result = executor.execute_task(task, context)
            results[executor.agent_profile.name] = result
            self.agent_pool.release(executor)

        return results

    def chain_execution(
        self,
        tasks: List[Tuple[AgentTask, AgentRole]],
        initial_context: Optional[Dict[str, Any]] = None,
        pass_output: bool = True,
    ) -> List[AgentTaskResult]:
        """链式执行：前一个执行器的输出作为后一个执行器的输入。

        每个元组为 (task, role)，按顺序依次执行。
        若 pass_output 为 True，前一步的 output 将追加到下一步的 context 中。

        Args:
            tasks:           (AgentTask, AgentRole) 元组列表，按执行顺序排列
            initial_context: 初始上下文
            pass_output:     是否将前一步输出传递给下一步

        Returns:
            各步骤的 AgentTaskResult 列表
        """
        context = initial_context or {}
        results: List[AgentTaskResult] = []

        for i, (task, role) in enumerate(tasks):
            executor = self.agent_pool.acquire(role=role)
            if executor is None:
                self._logger.error(
                    "链式执行第 %d 步失败：角色 %s 无可用执行器", i, role.value,
                )
                results.append(AgentTaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=f"角色 {role.value} 无可用执行器",
                ))
                break

            self._logger.info(
                "链式执行第 %d/%d 步: task=%s → %s",
                i + 1, len(tasks), task.task_id, executor.agent_profile.name,
            )

            result = executor.execute_task(task, context)
            results.append(result)
            self.agent_pool.release(executor)

            if result.success and pass_output:
                context[f"step_{i}_output"] = result.output
                context[f"step_{i}_artifacts"] = result.artifacts
            elif not result.success:
                self._logger.error(
                    "链式执行在第 %d 步失败，终止后续步骤: %s", i + 1, result.error,
                )
                break

        return results

    def parallel_execution(
        self,
        tasks: List[Tuple[AgentTask, AgentRole]],
        shared_context: Optional[Dict[str, Any]] = None,
        max_workers: int = 4,
    ) -> List[AgentTaskResult]:
        """并行执行：多个任务同时在不同执行器上运行。

        使用线程池实现真正的并行执行。

        Args:
            tasks:          (AgentTask, AgentRole) 元组列表
            shared_context: 共享上下文（各任务独立副本）
            max_workers:    最大并行线程数

        Returns:
            各任务的 AgentTaskResult 列表（按输入顺序排列）
        """
        shared_context = shared_context or {}
        results: List[Optional[AgentTaskResult]] = [None] * len(tasks)

        def _run_single(index: int, task: AgentTask, role: AgentRole) -> None:
            executor = self.agent_pool.acquire(role=role)
            if executor is None:
                results[index] = AgentTaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=f"角色 {role.value} 无可用执行器",
                )
                return

            try:
                ctx = dict(shared_context)
                ctx["_parallel_index"] = index
                result = executor.execute_task(task, ctx)
                results[index] = result
            except Exception as e:
                results[index] = AgentTaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=f"并行执行异常: {e}",
                )
            finally:
                self.agent_pool.release(executor)

        self._logger.info(
            "并行执行 %d 个任务 (max_workers=%d)", len(tasks), max_workers,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_run_single, i, task, role): i
                for i, (task, role) in enumerate(tasks)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    future.result()
                except Exception as e:
                    self._logger.error(
                        "并行任务 %d 未预期的异常: %s", idx, e,
                    )
                    results[idx] = AgentTaskResult(
                        task_id=tasks[idx][0].task_id,
                        success=False,
                        error=f"未预期的异常: {e}",
                    )

        return [r for r in results if r is not None]

    def vote_decision(
        self,
        options: List[str],
        voters: List[AgentRole],
        question: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """投票决策：多个执行器对同一问题进行投票。

        每个执行器独立评估所有选项并给出排名。
        采用 Borda Count 投票机制（排名越低得分越高）。

        Args:
            options: 候选选项列表
            voters:  投票者角色列表
            question: 决策问题描述
            context:  决策上下文

        Returns:
            决策结果字典，包含：
            - winner: 胜出选项
            - rankings: 各选项得分排名
            - voter_results: 各投票者的投票详情
            - consensus_score: 共识度（0-1，越高表示越一致）
        """
        context = context or {}
        voter_results: Dict[str, Dict[str, Any]] = {}
        scores: Dict[str, float] = {opt: 0.0 for opt in options}

        for role in voters:
            executor = self.agent_pool.acquire(role=role)
            if executor is None:
                self._logger.warning(
                    "投票者角色 %s 无可用执行器，跳过", role.value,
                )
                continue

            try:
                task = AgentTask(
                    description=(
                        f"投票决策问题：{question}\n\n"
                        f"候选选项：\n"
                        + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options))
                        + "\n\n请对每个选项进行评估并给出排名（1为最佳），"
                        "同时简要说明理由。输出 JSON 格式："
                        '{"ranking": ["选项A", "选项B"], "scores": {"选项A": 9, "选项B": 7}, '
                        '"reason": "..."}}'
                    ),
                    assigned_to=executor.agent_profile.name,
                )

                task_result = executor.execute_task(task, context)

                if task_result.success:
                    try:
                        output_data = json.loads(task_result.output)
                        ranking = output_data.get("ranking", [])
                        if ranking:
                            for rank_idx, opt in enumerate(ranking):
                                if opt in scores:
                                    scores[opt] += len(options) - rank_idx
                        voter_results[executor.agent_profile.name] = output_data
                    except json.JSONDecodeError:
                        voter_results[executor.agent_profile.name] = {
                            "raw_output": task_result.output,
                            "parse_error": True,
                        }
                else:
                    voter_results[executor.agent_profile.name] = {
                        "error": task_result.error,
                    }
            except Exception as e:
                voter_results[executor.agent_profile.name] = {
                    "error": str(e),
                }
            finally:
                self.agent_pool.release(executor)

        sorted_options = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        total_voters = len(voter_results)
        winner = sorted_options[0][0] if sorted_options else ""

        max_possible_score = len(options) * max(total_voters, 1)
        consensus_score = (
            sorted_options[0][1] / max_possible_score if max_possible_score > 0 else 0.0
        )

        decision = {
            "winner": winner,
            "rankings": [
                {"option": opt, "score": round(score, 2)}
                for opt, score in sorted_options
            ],
            "voter_results": voter_results,
            "consensus_score": round(consensus_score, 3),
            "total_voters": total_voters,
            "question": question,
        }

        self._logger.info(
            "投票决策完成: winner=%s, consensus=%.3f, voters=%d",
            winner, consensus_score, total_voters,
        )
        return decision


# ====================================================================== #
#  便捷工厂函数
# ====================================================================== #


def create_default_pool(
    llm_adapter: Optional[LLMAdapter] = None,
    memory_facade: Optional[MemoryFacade] = None,
) -> AgentPool:
    """创建包含所有默认执行器的智能体池。

    Args:
        llm_adapter:   可选的 LLM 适配器
        memory_facade: 可选的记忆门面

    Returns:
        已注册所有默认执行器的 AgentPool 实例
    """
    pool = AgentPool()

    common_kwargs: Dict[str, Any] = {}
    if llm_adapter:
        common_kwargs["llm_adapter"] = llm_adapter
    if memory_facade:
        common_kwargs["memory_facade"] = memory_facade

    pool.register(ArchitectExecutor(**common_kwargs))
    pool.register(CoderExecutor(**common_kwargs))
    pool.register(ReviewerExecutor(**common_kwargs))
    pool.register(TesterExecutor(**common_kwargs))
    pool.register(OrchestratorExecutor(**common_kwargs))

    return pool


def create_collaboration_manager(
    llm_adapter: Optional[LLMAdapter] = None,
    memory_facade: Optional[MemoryFacade] = None,
) -> AgentCollaborationManager:
    """创建包含默认智能体池的协作管理器。

    Args:
        llm_adapter:   可选的 LLM 适配器
        memory_facade: 可选的记忆门面

    Returns:
        已初始化的 AgentCollaborationManager 实例
    """
    pool = create_default_pool(llm_adapter=llm_adapter, memory_facade=memory_facade)
    return AgentCollaborationManager(agent_pool=pool)


# ====================================================================== #
#  模块导出
# ====================================================================== #

__all__ = [
    # 数据类
    "AgentTaskResult",
    # 基类
    "AgentExecutor",
    # 具体执行器
    "ArchitectExecutor",
    "CoderExecutor",
    "ReviewerExecutor",
    "TesterExecutor",
    "OrchestratorExecutor",
    # 智能体池
    "AgentPool",
    # 协作管理器
    "AgentCollaborationManager",
    # 工厂函数
    "create_default_pool",
    "create_collaboration_manager",
]
"""
agent_protocol: 企业级多智能体系统的核心协议层。

本模块定义了智能体（Agent）之间的交互协议、角色分工、能力描述、
消息结构与任务实体，为上层编排框架提供统一的类型约束与通信范式。

设计思路:
- 基于 Pydantic BaseModel / dataclass 构建强类型的数据契约；
- 通过枚举（Enum）固化角色、能力、状态等有限集合，避免魔法字符串；
- AgentInteractionProtocol 作为协议门面类，集中管理消息路由与任务分发；
- 所有模型均支持 JSON 序列化，便于跨进程 / 跨服务通信。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field


# ====================================================================== #
#  枚举定义
# ====================================================================== #


class AgentRole(str, Enum):
    """智能体角色枚举，定义系统中各类 Agent 的职能定位。

    - architect:     架构师，负责系统设计、技术选型、模块划分
    - coder:         编码者，负责具体代码实现
    - reviewer:      评审者，负责代码审查与质量把关
    - tester:        测试者，负责测试用例生成与执行验证
    - orchestrator:  编排者，负责任务调度、流程控制与多 Agent 协同
    - analyst:       分析师，负责需求分析、数据洞察与方案提炼
    - deployer:      部署者，负责构建、打包、发布与运维相关操作
    """

    ARCHITECT = "architect"
    CODER = "coder"
    REVIEWER = "reviewer"
    TESTER = "tester"
    ORCHESTRATOR = "orchestrator"
    ANALYST = "analyst"
    DEPLOYER = "deployer"


class AgentCapability(str, Enum):
    """智能体能力枚举，描述 Agent 可执行的原子技能集合。

    - code_generation:  代码生成
    - code_review:      代码评审
    - testing:          测试（用例生成 / 执行）
    - analysis:         分析（需求 / 数据 / 日志）
    - planning:         规划（任务分解 / 路径规划）
    - deployment:       部署（构建 / 发布 / 运维）
    - communication:    通信（与其他 Agent / 外部系统交互）
    - memory_access:    记忆访问（读写长期记忆 / 知识库）
    """

    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    TESTING = "testing"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    DEPLOYMENT = "deployment"
    COMMUNICATION = "communication"
    MEMORY_ACCESS = "memory_access"


class AgentStatus(str, Enum):
    """智能体运行状态枚举。

    - idle:      空闲，可接收新任务
    - working:   执行中，正在处理分配的任务
    - waiting:   等待中，阻塞于前置条件 / 外部依赖
    - completed: 已完成，任务正常结束
    - failed:    失败，任务因错误中止
    - suspended: 已挂起，被外部暂停或主动让出
    """

    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"


class MessageType(str, Enum):
    """AgentMessage 消息类型枚举。

    - direct:       点对点直接消息
    - broadcast:    广播消息（群发）
    - task_assign:  任务分配
    - task_result:  任务结果回传
    - request:      请求（需响应）
    - response:     响应
    - notification: 通知（无需响应）
    - error:        错误消息
    """

    DIRECT = "direct"
    BROADCAST = "broadcast"
    TASK_ASSIGN = "task_assign"
    TASK_RESULT = "task_result"
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    ERROR = "error"


class TaskPriority(str, Enum):
    """任务优先级枚举。

    - low:       低优先级
    - medium:    中优先级
    - high:      高优先级
    - critical:  紧急，需立即处理
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ====================================================================== #
#  数据模型
# ====================================================================== #


class AgentMessage(BaseModel):
    """智能体间通信的消息载体。

    Attributes:
        sender:    发送方 Agent 的名称或标识
        receiver:  接收方 Agent 的名称或标识（广播时为 "broadcast"）
        content:   消息的文本内容
        msg_type:  消息类型，参见 MessageType 枚举
        payload:   结构化负载，可携带任意 JSON 可序列化数据
        timestamp: 消息创建时间，默认为当前时间
        message_id: 唯一消息 ID，用于去重与追踪
        correlation_id: 关联 ID，用于将请求与响应配对
    """

    sender: str = Field(..., description="发送方 Agent 标识")
    receiver: str = Field(..., description="接收方 Agent 标识")
    content: str = Field(default="", description="消息文本内容")
    msg_type: MessageType = Field(default=MessageType.DIRECT, description="消息类型")
    payload: Dict[str, Any] = Field(default_factory=dict, description="结构化负载")
    timestamp: datetime = Field(default_factory=datetime.now, description="消息时间戳")
    message_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        description="唯一消息 ID",
    )
    correlation_id: Optional[str] = Field(
        default=None, description="关联 ID，用于请求-响应配对"
    )


class AgentTask(BaseModel):
    """任务实体，表示一个可被 Agent 执行的工作单元。

    Attributes:
        task_id:       唯一任务 ID
        description:   任务描述，自然语言说明要完成的目标
        assigned_to:   被分配的 Agent 名称或标识
        priority:      优先级，参见 TaskPriority 枚举
        status:        任务当前状态，参见 AgentStatus 枚举
        dependencies:  依赖的前置任务 ID 列表
        result:        任务执行结果，可在完成后填充
        created_at:    任务创建时间
        updated_at:    最近一次状态更新时间
        progress:      执行进度百分比（0-100）
        max_retries:   最大重试次数
        retry_count:   已重试次数
        error_message: 失败时的错误信息
        metadata:      扩展元数据，供上层框架存储额外信息
    """

    task_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        description="唯一任务 ID",
    )
    description: str = Field(..., description="任务描述")
    assigned_to: Optional[str] = Field(default=None, description="被分配的 Agent 标识")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, description="任务优先级")
    status: AgentStatus = Field(default=AgentStatus.IDLE, description="任务当前状态")
    dependencies: List[str] = Field(default_factory=list, description="前置依赖任务 ID 列表")
    result: Optional[Dict[str, Any]] = Field(default=None, description="任务执行结果")
    created_at: datetime = Field(default_factory=datetime.now, description="任务创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="最近更新时间")
    progress: int = Field(default=0, ge=0, le=100, description="执行进度百分比")
    max_retries: int = Field(default=3, ge=0, description="最大重试次数")
    retry_count: int = Field(default=0, ge=0, description="已重试次数")
    error_message: str = Field(default="", description="失败时的错误信息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    def touch(self) -> None:
        """更新时间戳为当前时间。"""
        self.updated_at = datetime.now()

    def advance_progress(self, increment: int = 10) -> None:
        """推进进度，按增量百分比更新。"""
        self.progress = min(100, self.progress + increment)
        self.touch()

    def mark_completed(self, result: Optional[Dict[str, Any]] = None) -> None:
        """将任务标记为已完成。"""
        self.status = AgentStatus.COMPLETED
        self.progress = 100
        if result is not None:
            self.result = result
        self.touch()

    def mark_failed(self, error_message: str) -> None:
        """将任务标记为失败。"""
        self.status = AgentStatus.FAILED
        self.error_message = error_message
        self.touch()

    def mark_waiting(self) -> None:
        """将任务标记为等待中。"""
        self.status = AgentStatus.WAITING
        self.touch()


class AgentProfile(BaseModel):
    """智能体画像，描述单个 Agent 的静态配置信息。

    Attributes:
        name:          Agent 的唯一名称标识
        role:          角色，参见 AgentRole 枚举
        capabilities:  具备的能力列表，参见 AgentCapability 枚举
        system_prompt: 系统提示词，用于初始化 Agent 的行为与人格
        description:   对该 Agent 的人类可读描述
        model:         指定使用的 LLM 模型名称（可选）
        metadata:      扩展元数据
    """

    name: str = Field(..., description="Agent 唯一名称标识")
    role: AgentRole = Field(..., description="Agent 角色")
    capabilities: List[AgentCapability] = Field(
        default_factory=list, description="具备的能力列表"
    )
    system_prompt: str = Field(default="", description="系统提示词")
    description: str = Field(default="", description="人类可读描述")
    model: Optional[str] = Field(default=None, description="指定 LLM 模型名称")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    def has_capability(self, capability: AgentCapability) -> bool:
        """判断是否具备指定能力。"""
        return capability in self.capabilities


# ====================================================================== #
#  交互协议
# ====================================================================== #


class AgentInteractionProtocol(BaseModel):
    """智能体交互协议门面类，封装 Agent 间的消息通信与任务分发逻辑。

    本协议提供四种核心交互模式:
    1. direct_message:    点对点直接消息
    2. broadcast:         广播消息（群发至所有在线 Agent）
    3. task_delegation:   任务委派（创建并分配任务）
    4. result_sharing:    结果共享（将执行结果回传给上游 Agent）

    Attributes:
        agents:      已注册的 Agent 画像字典，key 为 Agent 名称
        message_log: 所有已发送的消息记录
        task_log:    所有已创建的任务记录
    """

    agents: Dict[str, AgentProfile] = Field(
        default_factory=dict, description="已注册的 Agent 画像"
    )
    message_log: List[AgentMessage] = Field(
        default_factory=list, description="消息记录日志"
    )
    task_log: List[AgentTask] = Field(
        default_factory=list, description="任务记录日志"
    )

    # ------------------------------------------------------------------ #
    #  Agent 注册 / 注销
    # ------------------------------------------------------------------ #

    def register_agent(self, profile: AgentProfile) -> None:
        """注册一个 Agent 画像到协议层。"""
        self.agents[profile.name] = profile

    def unregister_agent(self, name: str) -> None:
        """从协议层注销一个 Agent。"""
        self.agents.pop(name, None)

    def get_agent(self, name: str) -> Optional[AgentProfile]:
        """根据名称获取 Agent 画像。"""
        return self.agents.get(name)

    # ------------------------------------------------------------------ #
    #  交互模式
    # ------------------------------------------------------------------ #

    def direct_message(
        self,
        sender: str,
        receiver: str,
        content: str,
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> AgentMessage:
        """发送点对点直接消息。

        Args:
            sender:       发送方 Agent 名称
            receiver:     接收方 Agent 名称
            content:      消息文本
            payload:      可选的结构化负载
            correlation_id: 可选的关联 ID

        Returns:
            构建并记录的 AgentMessage 实例
        """
        message = AgentMessage(
            sender=sender,
            receiver=receiver,
            content=content,
            msg_type=MessageType.DIRECT,
            payload=payload or {},
            correlation_id=correlation_id,
        )
        self.message_log.append(message)
        return message

    def broadcast(
        self,
        sender: str,
        content: str,
        payload: Optional[Dict[str, Any]] = None,
        exclude: Optional[List[str]] = None,
    ) -> List[AgentMessage]:
        """发送广播消息给所有已注册 Agent（排除发送方自身及指定排除列表）。

        Args:
            sender:  发送方 Agent 名称
            content: 广播消息文本
            payload: 可选的结构化负载
            exclude: 排除的 Agent 名称列表

        Returns:
            为每个接收方构建的 AgentMessage 列表
        """
        exclude_set = set(exclude or []) | {sender}
        recipients = [name for name in self.agents if name not in exclude_set]

        messages: List[AgentMessage] = []
        for recipient in recipients:
            message = AgentMessage(
                sender=sender,
                receiver=recipient,
                content=content,
                msg_type=MessageType.BROADCAST,
                payload=payload or {},
            )
            messages.append(message)

        self.message_log.extend(messages)
        return messages

    def task_delegation(
        self,
        sender: str,
        receiver: str,
        description: str,
        priority: TaskPriority = TaskPriority.MEDIUM,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentTask:
        """任务委派：创建任务并分配给指定 Agent。

        Args:
            sender:       委派方（通常是 orchestrator）
            receiver:     被委派的 Agent 名称
            description:  任务描述
            priority:     任务优先级
            dependencies: 前置依赖任务 ID 列表
            metadata:     扩展元数据

        Returns:
            已创建并记录的 AgentTask 实例
        """
        task = AgentTask(
            description=description,
            assigned_to=receiver,
            priority=priority,
            status=AgentStatus.WAITING if dependencies else AgentStatus.IDLE,
            dependencies=dependencies or [],
            metadata=metadata or {},
        )
        self.task_log.append(task)

        # 同时发送一条 TASK_ASSIGN 消息给被委派方
        message = AgentMessage(
            sender=sender,
            receiver=receiver,
            content=f"[Task Assigned] {description}",
            msg_type=MessageType.TASK_ASSIGN,
            payload={
                "task_id": task.task_id,
                "description": description,
                "priority": priority.value,
                "dependencies": dependencies or [],
            },
        )
        self.message_log.append(message)

        return task

    def result_sharing(
        self,
        sender: str,
        receiver: str,
        task_id: str,
        result: Dict[str, Any],
        content: str = "",
    ) -> AgentMessage:
        """结果共享：将任务执行结果回传给上游 Agent。

        Args:
            sender:    执行方 Agent 名称
            receiver:  上游（接收结果的）Agent 名称
            task_id:   关联的任务 ID
            result:    执行结果数据
            content:   可选的附加说明文本

        Returns:
            已创建并记录的 AgentMessage 实例
        """
        # 查找并更新关联任务
        task = self.find_task(task_id)
        if task is not None:
            task.mark_completed(result=result)

        message = AgentMessage(
            sender=sender,
            receiver=receiver,
            content=content or f"[Task Result] task_id={task_id} completed",
            msg_type=MessageType.TASK_RESULT,
            payload={
                "task_id": task_id,
                "result": result,
                "status": "completed",
            },
            correlation_id=task_id,
        )
        self.message_log.append(message)
        return message

    # ------------------------------------------------------------------ #
    #  查询 / 工具方法
    # ------------------------------------------------------------------ #

    def find_task(self, task_id: str) -> Optional[AgentTask]:
        """根据 task_id 查找任务。"""
        for task in self.task_log:
            if task.task_id == task_id:
                return task
        return None

    def get_pending_tasks(self, agent_name: Optional[str] = None) -> List[AgentTask]:
        """获取等待中或工作中的任务列表。

        Args:
            agent_name: 可选，仅返回分配给指定 Agent 的任务

        Returns:
            符合条件的 AgentTask 列表
        """
        statuses = {AgentStatus.IDLE, AgentStatus.WORKING, AgentStatus.WAITING}
        return [
            t for t in self.task_log
            if t.status in statuses
            and (agent_name is None or t.assigned_to == agent_name)
        ]

    def get_messages(
        self,
        sender: Optional[str] = None,
        receiver: Optional[str] = None,
        msg_type: Optional[MessageType] = None,
    ) -> List[AgentMessage]:
        """按条件过滤消息记录。

        Args:
            sender:   可选，按发送方过滤
            receiver: 可选，按接收方过滤
            msg_type: 可选，按消息类型过滤

        Returns:
            符合条件的 AgentMessage 列表
        """
        result = self.message_log
        if sender is not None:
            result = [m for m in result if m.sender == sender]
        if receiver is not None:
            result = [m for m in result if m.receiver == receiver]
        if msg_type is not None:
            result = [m for m in result if m.msg_type == msg_type]
        return result


# ====================================================================== #
#  模块导出
# ====================================================================== #

__all__ = [
    # 枚举
    "AgentRole",
    "AgentCapability",
    "AgentStatus",
    "MessageType",
    "TaskPriority",
    # 数据模型
    "AgentMessage",
    "AgentTask",
    "AgentProfile",
    # 协议类
    "AgentInteractionProtocol",
]
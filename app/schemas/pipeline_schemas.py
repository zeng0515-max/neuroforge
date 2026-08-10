"""请求/响应 Pydantic Schema（与内部 State 解耦，便于 API 演进）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ====================================================================== #
#  公共子结构
# ====================================================================== #

class RequirementSpecOut(BaseModel):
    title: str = ""
    goal: str = ""
    functional_requirements: List[str] = Field(default_factory=list)
    non_functional_requirements: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)


class GeneratedFileOut(BaseModel):
    path: str
    language: str = "python"
    content: str = ""
    description: str = ""
    line_count: int = 0


class ReviewIssueOut(BaseModel):
    severity: str = "warning"
    file_path: str = ""
    line_start: int = 0
    rule_id: str = ""
    message: str = ""
    suggestion: str = ""


class ReviewReportOut(BaseModel):
    verdict: str = "approved"
    summary: str = ""
    score: int = 100
    issues: List[ReviewIssueOut] = Field(default_factory=list)
    coverage_check: Dict[str, Any] = Field(default_factory=dict)


class TestCaseOut(BaseModel):
    name: str = ""
    description: str = ""
    target_file: str = ""
    target_function: str = ""
    priority: str = "medium"
    status: str = "generated"
    error_message: str = ""
    output: str = ""
    duration_ms: int = 0
    code_snippet: str = ""


class TestReportOut(BaseModel):
    status: str = "not_run"
    summary: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error_count: int = 0
    duration_ms: int = 0
    run_command: str = ""
    test_cases: List[TestCaseOut] = Field(default_factory=list)
    test_files: List[GeneratedFileOut] = Field(default_factory=list)


class PipelineStatusOut(BaseModel):
    phase: str = "pending"
    started_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    error_message: str = ""


# ====================================================================== #
#  请求
# ====================================================================== #

class CreatePipelineRequest(BaseModel):
    """创建一个多智能体代码生成任务。"""

    user_request: str = Field(..., min_length=1, description="用户的自然语言需求描述")
    task_id: Optional[str] = Field(default=None, description="可选，自定义任务 ID；不填则自动生成")
    model: Optional[str] = Field(default=None, description="可选，指定本次任务使用的模型（覆盖全局设置）")
    skill_id: Optional[str] = Field(default=None, description="可选，指定专业技能 ID（如 algorithm/api_design）；不填则自动匹配")
    extra_args: Optional[Dict[str, Any]] = Field(
        default=None,
        description="附加约束，如 tech_stack、target_platform 等",
    )
    prefer_langgraph: bool = True


class ReRunFromPhaseRequest(BaseModel):
    """从某个阶段重新执行（暂用于 future use，保留 schema）。"""

    from_phase: str = "code_gen"
    user_feedback: Optional[str] = None


class ToolCallRequest(BaseModel):
    """直接调用已注册的工具。"""

    tool_name: str = Field(..., description="tool_registry 中的名称，如 write_file / validate_code")
    arguments: Dict[str, Any] = Field(default_factory=dict)


class AddToKnowledgeRequest(BaseModel):
    """把知识（文本或已有项目文件路径）灌入 RAG。"""

    texts: List[str] = Field(default_factory=list, description="直接给的纯文本条目")
    source: str = Field(default="manual", description="这批知识的来源标签")
    project_files: List[str] = Field(
        default_factory=list,
        description="相对于项目根的文件路径，rag.add_files 会读取并索引",
    )


class KnowledgeQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


# ====================================================================== #
#  响应
# ====================================================================== #

class CreatePipelineResponse(BaseModel):
    task_id: str
    status: PipelineStatusOut
    requirement: RequirementSpecOut
    code: List[GeneratedFileOut] = Field(default_factory=list)
    review: Optional[ReviewReportOut] = None
    test: Optional[TestReportOut] = None
    retry_counter: int = 0


class PipelineListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class TaskDetailResponse(BaseModel):
    task: Dict[str, Any]
    requirement: Optional[RequirementSpecOut] = None
    code: List[GeneratedFileOut] = Field(default_factory=list)
    review: Optional[ReviewReportOut] = None
    test: Optional[TestReportOut] = None
    files: List[Dict[str, Any]] = Field(default_factory=list)
    logs: List[Dict[str, Any]] = Field(default_factory=list)


class ToolCallResponse(BaseModel):
    tool_name: str
    ok: bool
    result: Any = None
    error: Optional[str] = None


class KnowledgeAddResponse(BaseModel):
    added: int = 0
    total: int = 0


class KnowledgeDoc(BaseModel):
    content: str
    score: float = 0.0
    source: str = ""
    file_path: str = ""


class KnowledgeQueryResponse(BaseModel):
    query: str
    items: List[KnowledgeDoc] = Field(default_factory=list)


class HealthDebugInfo(BaseModel):
    llm_error: str = ""
    rag_error: str = ""
    db_error: str = ""
    llm_model: str = ""
    code_llm_model: str = ""
    app_env: str = ""
    new_engine_version: str = ""
    new_engine_mode: str = "standalone"
    agent_count: int = 0


class HealthResponse(BaseModel):
    status: str = "ok"
    llm: str = "unknown"
    rag_available: bool = False
    rag_count: int = 0
    db_ok: bool = True
    debug: HealthDebugInfo = Field(default_factory=HealthDebugInfo, alias="debug")
    new_engine_available: bool = False
    agent_count: int = 0
    orchestrator_status: str = "unavailable"


# ====================================================================== #
#  模型管理
# ====================================================================== #
class ModelSwitchRequest(BaseModel):
    model: str = Field(..., description="目标模型名称")
    for_code: bool = Field(False, description="是否切换代码专用模型")


class ModelSwitchResponse(BaseModel):
    status: str
    model: str
    for_code: bool
    base_url: str = ""
    message: str = ""
    provider: str = ""
    api_key_url: str = ""
    api_key_fallback: bool = False


class ModelsListResponse(BaseModel):
    current_main: str
    current_code: str
    base_url: str
    available: List[str]


# ====================================================================== #
#  LLM 配置（页面内配置 API 大模型）
# ====================================================================== #
class ProviderConfigOut(BaseModel):
    """单个厂商的配置状态。"""

    name: str = ""
    env_key: str = ""
    base_url: str = ""
    key_url: str = ""
    configured: bool = False
    models: List[str] = Field(default_factory=list)


class LLMConfigStatusResponse(BaseModel):
    """当前 LLM 配置状态。"""

    configured: bool = False  # 是否已配置有效 API Key（非 DryRun）
    dry_run: bool = True
    base_url: str = ""
    model: str = ""
    code_model: str = ""
    providers: List[ProviderConfigOut] = Field(default_factory=list)
    quota_exhausted: bool = False
    quota_detail: str = ""


class LLMConfigUpdateRequest(BaseModel):
    """更新 LLM 配置。"""

    base_url: str = Field(..., min_length=1, description="OpenAI 兼容 Base URL")
    api_key: str = Field(..., min_length=1, description="API Key")
    model: str = Field(..., min_length=1, description="主模型名")
    code_model: Optional[str] = Field(default=None, description="代码模型名，留空则与主模型一致")
    provider_env_key: Optional[str] = Field(
        default=None,
        description="厂商专用环境变量名（如 DASHSCOPE_API_KEY/ARK_API_KEY），写入对应厂商 key",
    )


class LLMConfigUpdateResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    dry_run: bool = True
    model: str = ""
    base_url: str = ""


# ====================================================================== #
#  Human-in-the-Loop (HITL)
# ====================================================================== #
class ApprovalRequest(BaseModel):
    """人工审批请求。"""
    action: str = Field(..., description="审批动作: approve / reject")
    reason: Optional[str] = Field(default=None, description="审批理由或说明")


class ApprovalResponse(BaseModel):
    """审批操作响应。"""
    status: str = "ok"
    task_id: str = ""
    message: str = ""


class StreamEvent(BaseModel):
    """SSE 流式事件数据模型。

    phase: 当前阶段（requirement/code_gen/review/test_gen/done/failed）
    progress: 0-100 进度百分比
    message: 描述文本
    task_id: 任务 ID
    timestamp: 事件时间戳
    """

    phase: str = ""
    progress: int = 0
    message: str = ""
    task_id: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


__all__ = [
    "CreatePipelineRequest",
    "CreatePipelineResponse",
    "ReRunFromPhaseRequest",
    "ToolCallRequest",
    "ToolCallResponse",
    "AddToKnowledgeRequest",
    "KnowledgeAddResponse",
    "KnowledgeQueryRequest",
    "KnowledgeQueryResponse",
    "KnowledgeDoc",
    "HealthDebugInfo",
    "HealthResponse",
    "ModelSwitchRequest",
    "ModelSwitchResponse",
    "ModelsListResponse",
    "PipelineListResponse",
    "TaskDetailResponse",
    "RequirementSpecOut",
    "GeneratedFileOut",
    "ReviewReportOut",
    "ReviewIssueOut",
    "TestReportOut",
    "TestCaseOut",
    "PipelineStatusOut",
    "ApprovalRequest",
    "ApprovalResponse",
    "StreamEvent",
]

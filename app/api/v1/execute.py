"""代码执行验证 & 多轮对话修改（文档 3.6）：
- POST /api/v1/tasks/{task_id}/execute  代码执行验证
- POST /api/v1/tasks/{task_id}/chat     多轮对话修改
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.v1.tasks import _final_states
from app.db.sqlite_client import db
from app.tools.tool_registry import ToolPermissionLevel, tool_registry

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("/{task_id}/execute")
async def execute_task_code(task_id: str) -> Dict[str, Any]:
    """代码执行验证：把生成的代码写入临时目录，执行语法检查 + 导入测试 + 运行入口。"""
    if db.get_task(task_id) is None and task_id not in _final_states:
        raise HTTPException(404, f"任务 {task_id} 不存在")

    # 优先从 DB 读取文件，DB 为空时回退到内存态 code_package
    db_files = db.list_files(task_id)
    if not db_files:
        state = _final_states.get(task_id)
        if state:
            pkg = state.get("code_package")
            if pkg and hasattr(pkg, "files") and pkg.files:
                db_files = [
                    type("FileLike", (), {
                        "path": f.path, "content": f.content,
                        "language": f.language, "line_count": f.line_count,
                    })()
                    for f in pkg.files
                ]
        if not db_files:
            raise HTTPException(400, f"任务 {task_id} 没有代码文件可验证")

    files = [
        {"path": f.path, "content": f.content or "", "language": f.language or "python"}
        for f in db_files
        if (f.path or "").endswith(".py")
    ]
    if not files:
        raise HTTPException(400, "没有 Python 文件可验证")

    # 入口层 AST 校验：有 error 级违规则返回 400 拒绝执行（深度防御，避免恶意代码进入子进程）
    from app.tools.ast_validator import validate_python_source
    critical_violations: list = []
    for f in files:
        ast_result = validate_python_source(f["content"])
        if ast_result.errors:
            critical_violations.append({
                "path": f["path"],
                "violations": [
                    {"rule": v.rule, "message": v.message, "line": v.line}
                    for v in ast_result.errors
                ],
            })
    if critical_violations:
        raise HTTPException(
            400,
            detail={"message": "AST校验未通过，拒绝执行", "violations": critical_violations},
        )

    loop = asyncio.get_running_loop()
    from app.tools.code_executor import execute_code
    result = await loop.run_in_executor(
        None, lambda: execute_code(files, timeout=15)
    )
    return result.to_dict()


@router.post("/{task_id}/chat")
async def chat_modify_task(
    task_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """多轮对话修改：用户用自然语言描述修改需求，LLM 返回修改后的代码。

    请求体: {"instruction": "把第3行改一下", "auto_verify": true}
    """
    if db.get_task(task_id) is None and task_id not in _final_states:
        raise HTTPException(404, f"任务 {task_id} 不存在")

    instruction = str(body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "instruction 不能为空")

    auto_verify = bool(body.get("auto_verify", True))

    loop = asyncio.get_running_loop()
    from app.agents.chat_modify_agent import ChatModifyAgent
    agent = ChatModifyAgent()
    result = await loop.run_in_executor(
        None, lambda: agent.modify(task_id, instruction, auto_verify=auto_verify)
    )
    return result


# ====================================================================== #
#  工具权限管理 API
# ====================================================================== #

class ToolPermissionInfo(BaseModel):
    """工具权限信息。"""
    name: str
    description: str
    permission_level: str


class ToolPermissionListResponse(BaseModel):
    """工具权限列表响应。"""
    tools: List[ToolPermissionInfo] = Field(default_factory=list)
    current_permission: str = "free"


class ToolPermissionSetRequest(BaseModel):
    """设置工具调用权限请求。"""
    level: str = Field(..., description="权限等级: free / observe / modify / dangerous")


class ToolPermissionSetResponse(BaseModel):
    """设置工具调用权限响应。"""
    status: str = "ok"
    current_permission: str = ""


@router.get("/tools/permissions", response_model=ToolPermissionListResponse)
async def list_tool_permissions() -> ToolPermissionListResponse:
    """列出所有已注册工具及其权限等级。"""
    specs = tool_registry.list_specs()
    tools = [
        ToolPermissionInfo(
            name=s.name,
            description=s.description[:100] if s.description else "",
            permission_level=s.permission_level.value,
        )
        for s in specs
    ]
    return ToolPermissionListResponse(
        tools=tools,
        current_permission=tool_registry.get_current_permission().value,
    )


@router.post("/tools/permissions", response_model=ToolPermissionSetResponse)
async def set_tool_permissions(req: ToolPermissionSetRequest) -> ToolPermissionSetResponse:
    """设置当前工具调用权限等级。"""
    level_map = {
        "free": ToolPermissionLevel.FREE,
        "observe": ToolPermissionLevel.OBSERVE,
        "modify": ToolPermissionLevel.MODIFY,
        "dangerous": ToolPermissionLevel.DANGEROUS,
    }
    level = level_map.get(req.level.lower())
    if level is None:
        raise HTTPException(400, f"无效的权限等级: {req.level}，可选: free / observe / modify / dangerous")
    tool_registry.set_permission(level)
    return ToolPermissionSetResponse(
        status="ok",
        current_permission=level.value,
    )
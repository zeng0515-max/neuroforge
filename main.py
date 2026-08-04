"""FastAPI 入口：health / pipeline / tools / rag 知识 四组端点。

启动示例：
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.llm_adapter import llm
from app.db.sqlite_client import db
from app.graph.pipeline import run_pipeline
from app.graph.state import (
    CodePackage,
    PipelineState,
    RequirementSpec,
    ReviewReport,
    TestReport,
)
from app.schemas import (
    AddToKnowledgeRequest,
    CreatePipelineRequest,
    CreatePipelineResponse,
    GeneratedFileOut,
    HealthResponse,
    KnowledgeAddResponse,
    KnowledgeDoc,
    KnowledgeQueryRequest,
    KnowledgeQueryResponse,
    PipelineListResponse,
    PipelineStatusOut,
    RequirementSpecOut,
    ReviewReportOut,
    TaskDetailResponse,
    TestReportOut,
    ToolCallRequest,
    ToolCallResponse,
)
from app.tools.rag_service import rag
from app.tools.tool_registry import tool_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("main")


# ====================================================================== #
#  FastAPI 实例
# ====================================================================== #

app = FastAPI(
    title=settings.app.name,
    version=settings.app.version,
    description="多智能体代码生成系统（需求→代码→评审→测试）",
)

# CORS（dev 允许全部；生产按实际 origin 配置）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.security.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ====================================================================== #
#  启动钩子：确保 data/ 目录与 DB 就绪
# ====================================================================== #

@app.on_event("startup")
def _on_startup() -> None:  # pragma: no cover - 仅触发一次
    # 触发 DB connect / RAG connect 的惰性初始化
    try:
        _ = db.list_tasks(limit=1)
    except Exception as e:
        logger.warning("DB startup check 失败：%s", e)
    try:
        _ = rag.count()
    except Exception as e:
        logger.warning("RAG startup check 失败：%s", e)


# ====================================================================== #
#  通用 State → Schema 转换器
# ====================================================================== #

def _to_requirement_out(r: RequirementSpec) -> RequirementSpecOut:
    return RequirementSpecOut(
        title=r.title,
        goal=r.goal,
        functional_requirements=list(r.functional_requirements),
        non_functional_requirements=list(r.non_functional_requirements),
        tech_stack=list(r.tech_stack),
        acceptance_criteria=list(r.acceptance_criteria),
        assumptions=list(r.assumptions),
    )


def _to_files_out(pkg: CodePackage) -> List[GeneratedFileOut]:
    return [
        GeneratedFileOut(
            path=f.path,
            language=f.language,
            content=f.content,
            description=f.description,
            line_count=f.line_count,
        )
        for f in pkg.files
    ]


def _to_review_out(r: Optional[ReviewReport]) -> Optional[ReviewReportOut]:
    from app.schemas import ReviewIssueOut
    if r is None:
        return None
    issues = []
    for i in r.issues:
        issues.append(ReviewIssueOut(
            severity=str(getattr(i, "severity_label", i.severity) or "warning"),
            file_path=getattr(i, "file_path", "") or getattr(i, "file", ""),
            line_start=int(getattr(i, "line_start", 0) or getattr(i, "line", 0) or 0),
            rule_id=getattr(i, "rule_id", "") or getattr(i, "rule", ""),
            message=i.message,
            suggestion=i.suggestion,
        ))
    return ReviewReportOut(
        verdict=str(getattr(r.verdict, "value", r.verdict) or "approved"),
        summary=r.summary,
        score=int(getattr(r, "score", 100) or 100),
        issues=issues,
        coverage_check=dict(getattr(r, "coverage_check", None) or {}),
    )


def _to_test_out(t: Optional[TestReport]) -> Optional[TestReportOut]:
    from app.schemas import TestCaseOut, GeneratedFileOut as _GFO
    if t is None:
        return None
    cases: List[Any] = []
    for c in getattr(t, "test_cases", []) or []:
        cases.append(TestCaseOut(
            name=getattr(c, "name", ""),
            description=getattr(c, "description", ""),
            target_file=getattr(c, "target_file", ""),
            target_function=getattr(c, "target_function", ""),
            priority=getattr(c, "priority", "medium"),
            status=getattr(c, "status", "generated"),
            error_message=getattr(c, "error_message", ""),
            output=getattr(c, "output", ""),
            duration_ms=int(getattr(c, "duration_ms", 0) or 0),
            code_snippet=getattr(c, "code_snippet", ""),
        ))
    test_files: List[_GFO] = []
    for f in getattr(t, "test_files", []) or []:
        try:
            if isinstance(f, dict):
                content = f.get("content", "")
                test_files.append(_GFO(
                    path=f.get("path", ""),
                    language=f.get("language", "python"),
                    content=content,
                    description=f.get("description", ""),
                    line_count=content.count("\n") + 1 if content else 0,
                ))
            else:  # GeneratedFile
                test_files.append(_GFO(
                    path=getattr(f, "path", ""),
                    language=getattr(f, "language", "python"),
                    content=getattr(f, "content", ""),
                    description=getattr(f, "description", ""),
                    line_count=int(getattr(f, "line_count", 0) or 0),
                ))
        except Exception:
            continue
    return TestReportOut(
        status=str(getattr(t.status, "value", t.status) or "not_run"),
        summary=getattr(t, "summary", ""),
        total=int(getattr(t, "total", 0) or getattr(t, "total_tests", 0) or len(cases)),
        passed=int(getattr(t, "passed", 0) or 0),
        failed=int(getattr(t, "failed", 0) or 0),
        skipped=int(getattr(t, "skipped", 0) or 0),
        error_count=int(getattr(t, "error_count", 0) or 0),
        duration_ms=int(getattr(t, "duration_ms", 0) or 0),
        run_command=getattr(t, "run_command", ""),
        test_cases=cases,
        test_files=test_files,
    )


# ====================================================================== #
#  1. Health
# ====================================================================== #

@app.get("/api/health", response_model=HealthResponse, tags=["基础"])
def health_check() -> HealthResponse:
    """健康检查：同时校验 LLM / RAG / DB 三大子系统。"""
    # --- LLM 探针 ---
    llm_error: str = ""
    try:
        if llm.dry_run:
            llm_label = "dry-run"
            if getattr(llm, "init_error", ""):
                llm_error = llm.init_error
        else:
            llm_label = str(getattr(settings.llm, "model", "real"))
    except Exception as e:
        llm_label = "error"
        llm_error = f"{type(e).__name__}: {e}"
        logger.warning("health LLM 探针失败：%s", llm_error)

    # --- RAG 探针 ---
    rag_cnt = 0
    rag_ok = False
    rag_error = ""
    try:
        rag_cnt = int(rag.count() or 0)
        rag_ok = True
    except Exception as e:
        rag_cnt = 0
        rag_ok = False
        rag_error = f"{type(e).__name__}: {e}"
        logger.warning("health RAG 探针失败：%s", rag_error)

    # --- DB 探针 ---
    db_ok = False
    db_error = ""
    try:
        _ = db.list_tasks(limit=1)
        db_ok = True
    except Exception as e:
        db_error = f"{type(e).__name__}: {e}"
        logger.warning("health DB 探针失败：%s", db_error)

    # --- 整体 status ---
    overall = "ok" if rag_ok and db_ok and llm_label not in ("unknown", "error") else "degraded"

    return HealthResponse(
        status=overall,
        llm=llm_label,
        rag_available=rag_ok,
        rag_count=rag_cnt,
        db_ok=db_ok,
        debug={
            "llm_error": llm_error,
            "rag_error": rag_error,
            "db_error": db_error,
            "llm_model": str(getattr(settings.llm, "model", "")),
            "code_llm_model": str(getattr(settings.code_llm, "model", "")),
            "app_env": settings.app.env,
        },
    )


# ====================================================================== #
#  2. Pipeline
# ====================================================================== #

@app.post("/api/pipelines", response_model=CreatePipelineResponse, tags=["流水线"])
def create_pipeline(req: CreatePipelineRequest = Body(...)) -> CreatePipelineResponse:
    task_id = (req.task_id or "").strip() or f"task_{uuid.uuid4().hex[:12]}"

    # 先写 DB 任务行，后续 update_task_phase / mark_task_* 才能命中
    created = db.create_task(
        task_id=task_id,
        user_request=req.user_request,
        extra_args=req.extra_args or {},
    )
    if not created:
        # 如果是冲突（重复 task_id）则改为新的
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        db.create_task(task_id=task_id, user_request=req.user_request, extra_args=req.extra_args or {})

    final_state: PipelineState = run_pipeline(
        task_id=task_id,
        user_request=req.user_request,
        extra_args=req.extra_args or {},
        prefer_langgraph=req.prefer_langgraph,
    )

    status = final_state.get("status")
    status_out = PipelineStatusOut(
        phase=str(getattr(status.phase, "value", status.phase) if status else "pending"),
        started_at=getattr(status, "started_at", datetime.now()) or datetime.now(),
        updated_at=getattr(status, "updated_at", datetime.now()) or datetime.now(),
        prompt_tokens=int(getattr(status, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(status, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(status, "total_tokens", 0) or 0),
        duration_seconds=float(getattr(status, "duration_seconds", 0) or 0),
        error_message=getattr(status, "error_message", "") or "",
    ) if status else PipelineStatusOut()

    req_out = _to_requirement_out(final_state.get("requirement") or RequirementSpec())
    files_out = _to_files_out(final_state.get("code_package") or CodePackage())
    review_out = _to_review_out(final_state.get("review_report"))
    test_out = _to_test_out(final_state.get("test_report"))

    return CreatePipelineResponse(
        task_id=task_id,
        status=status_out,
        requirement=req_out,
        code=files_out,
        review=review_out,
        test=test_out,
        retry_counter=int(final_state.get("retry_counter") or 0),
    )


@app.get("/api/pipelines", response_model=PipelineListResponse, tags=["流水线"])
def list_pipelines(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PipelineListResponse:
    rows = db.list_tasks(limit=limit, offset=offset)
    items: List[Dict[str, Any]] = []
    for t in rows:
        items.append({
            "task_id": t.task_id,
            "user_request": t.user_request,
            "phase": t.phase,
            "status": t.status,
            "prompt_tokens": t.prompt_tokens,
            "completion_tokens": t.completion_tokens,
            "total_tokens": t.total_tokens,
            "error_message": t.error_message or "",
            "started_at": t.started_at.isoformat() if isinstance(t.started_at, datetime) else t.started_at,
            "finished_at": t.finished_at.isoformat() if isinstance(t.finished_at, datetime) else t.finished_at,
        })
    return PipelineListResponse(items=items, total=len(items))


@app.get("/api/pipelines/{task_id}", response_model=TaskDetailResponse, tags=["流水线"])
def get_pipeline_detail(task_id: str) -> TaskDetailResponse:
    t = db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    task_dict: Dict[str, Any] = {
        "task_id": t.task_id,
        "user_request": t.user_request,
        "extra_args": t.extra_args or {},
        "phase": t.phase,
        "status": t.status,
        "summary": t.summary or {},
        "prompt_tokens": t.prompt_tokens,
        "completion_tokens": t.completion_tokens,
        "total_tokens": t.total_tokens,
        "duration_seconds": t.duration_seconds,
        "error_message": t.error_message or "",
        "started_at": t.started_at.isoformat() if isinstance(t.started_at, datetime) else t.started_at,
        "finished_at": t.finished_at.isoformat() if isinstance(t.finished_at, datetime) else t.finished_at,
        "updated_at": t.updated_at.isoformat() if isinstance(t.updated_at, datetime) else t.updated_at,
    }

    # files / logs
    db_files = db.list_files(task_id) or []
    files = [
        {
            "path": f.path,
            "source_stage": f.source_stage,
            "language": f.language,
            "size_bytes": len(f.content or ""),
            "sha256": getattr(f, "sha256", "") or "",
            "description": f.description or "",
        }
        for f in db_files
    ]
    db_logs = db.list_stage_logs(task_id) or []
    logs = [
        {
            "stage": l.stage,
            "duration_ms": l.duration_ms,
            "prompt_tokens": l.prompt_tokens,
            "completion_tokens": l.completion_tokens,
            "total_tokens": l.total_tokens,
            "error": l.error or "",
            "created_at": l.created_at.isoformat() if isinstance(l.created_at, datetime) else l.created_at,
        }
        for l in db_logs
    ]

    # 用 DB 中的 files 拼回 code 与测试部分（更权威）
    code: List[GeneratedFileOut] = []
    test_files: List[GeneratedFileOut] = []
    for f in db_files:
        stage = (f.source_stage or "").lower()
        entry = GeneratedFileOut(
            path=f.path,
            language=f.language or "plain",
            content=f.content or "",
            description=f.description or "",
            line_count=(f.content or "").count("\n") + 1 if f.content else 0,
        )
        if "test" in stage:
            test_files.append(entry)
        else:
            code.append(entry)

    return TaskDetailResponse(
        task=task_dict,
        requirement=RequirementSpecOut(title=t.user_request[:60]) if t else None,
        code=code,
        files=files,
        logs=logs,
        test=TestReportOut(
            status="generated" if test_files else "not_run",
            summary=f"从 DB 还原 {len(test_files)} 个测试文件",
            test_files=test_files,
            total=len(test_files),
        ) if test_files else None,
    )


# ====================================================================== #
#  3. Tools
# ====================================================================== #

@app.post("/api/tools/call", response_model=ToolCallResponse, tags=["工具"])
def call_tool(req: ToolCallRequest = Body(...)) -> ToolCallResponse:
    name = (req.tool_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="tool_name 不能为空")
    if name not in tool_registry.list_names():
        raise HTTPException(status_code=404, detail=f"未知工具：{name}。可用：{tool_registry.list_names()}")
    try:
        result = tool_registry.call(name, req.arguments or {})
        return ToolCallResponse(tool_name=name, ok=True, result=result)
    except Exception as e:
        logger.exception("工具调用失败：%s", e)
        return ToolCallResponse(tool_name=name, ok=False, error=str(e))


@app.get("/api/tools", tags=["工具"])
def list_tools() -> Dict[str, Any]:
    return {
        "names": tool_registry.list_names(),
        "openai_tools": tool_registry.to_openai_tools(),
    }


# ====================================================================== #
#  4. Knowledge / RAG
# ====================================================================== #

@app.post("/api/knowledge/add", response_model=KnowledgeAddResponse, tags=["知识库"])
def add_to_knowledge(req: AddToKnowledgeRequest = Body(...)) -> KnowledgeAddResponse:
    added = 0
    if req.texts:
        added += int(rag.add_texts(texts=req.texts, source=req.source) or 0)
    if req.project_files:
        added += int(rag.add_files(paths=[p for p in req.project_files if p], source=req.source) or 0)
    return KnowledgeAddResponse(added=added, total=rag.count())


@app.post("/api/knowledge/query", response_model=KnowledgeQueryResponse, tags=["知识库"])
def query_knowledge(req: KnowledgeQueryRequest = Body(...)) -> KnowledgeQueryResponse:
    docs = rag.query(req.query, top_k=req.top_k)
    items: List[KnowledgeDoc] = []
    for d in docs:
        items.append(KnowledgeDoc(
            content=d.content,
            score=float(d.score or 0),
            source=d.source or "",
            file_path=d.file_path or "",
        ))
    return KnowledgeQueryResponse(query=req.query, items=items)


@app.post("/api/knowledge/clear", tags=["知识库"])
def clear_knowledge() -> Dict[str, Any]:
    """清空当前 collection（管理员操作）。"""
    rag.clear()
    return {"ok": True, "count_after": rag.count()}


    # 5. 简易项目文件写回：把 code_package 写回项目根的一个子目录
# ====================================================================== #

@app.post("/api/pipelines/{task_id}/export", tags=["流水线"])
def export_pipeline_files(
    task_id: str,
    output_dir: Optional[str] = Body(None, embed=True),
) -> Dict[str, Any]:
    """把 DB 中该任务的所有文件写入 output_dir 子目录，便于直接本地运行。"""
    t = db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    target = (output_dir or f"output/{task_id}").strip().lstrip("/\\")
    base = os.path.abspath(os.path.join(os.getcwd(), target))
    project_root = os.path.abspath(os.getcwd())
    if not base.startswith(project_root):
        raise HTTPException(status_code=400, detail="output_dir 必须在项目根下。")
    os.makedirs(base, exist_ok=True)

    from app.tools.ast_validator import write_file_safely
    written: List[str] = []
    # write_file_safely 以 CWD 为根解析路径，这里临时切换到 base 目录后再调用
    prev_cwd = os.getcwd()
    try:
        os.chdir(base)
        for f in db.list_files(task_id) or []:
            try:
                full = write_file_safely(f.path, content=f.content or "")
                written.append(os.path.relpath(full, base))
            except Exception as e:
                logger.warning("写文件 %s 失败：%s", f.path, e)
    finally:
        os.chdir(prev_cwd)
    return {"output_dir": base, "written": written, "count": len(written)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.app.debug,
    )

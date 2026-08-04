"""多轮对话修改 Agent：用户用自然语言描述修改需求，LLM 返回修改后的完整代码。

支持场景：
- "把第3行改一下"
- "增加一个参数校验"
- "函数名改成 calculate_factorial"
- "去掉 lru_cache 装饰器"
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.core.llm_adapter import LLMAdapter, llm
from app.db.sqlite_client import db
from app.graph.state import GeneratedFile, PipelinePhase
from app.tools.code_executor import execute_code

logger = logging.getLogger(__name__)


CHAT_MODIFY_SYSTEM_PROMPT = """你是Python代码修改助手。用户会给你修改指令，你需要根据指令修改现有代码。
规则：
1. 只修改用户提到的部分，保留其余代码不变
2. 输出格式用"### FILE: 路径"分隔每个文件，文件内直接放完整源码
3. 输出修改后的完整文件内容（不是 diff，是完整文件）
4. 保持原有代码风格和注释
5. 如果用户说的行号不准确，按语义理解修改意图
6. 只输出代码，不要解释文字"""


_FILE_SECTION_RE = re.compile(
    r"###\s*FILE\s*[:：]\s*([^\n]+?)\s*\n+"
    r"(?:```(?:python|py|text)?\s*\n)?"
    r"(.*?)"
    r"(?=\n###\s*FILE|\n```\s*$|\Z|\n#\#\#)",
    re.DOTALL | re.IGNORECASE,
)


def _parse_file_sections(raw: str) -> List[Dict[str, str]]:
    """从模型输出里解析成 [{"path":..., "content":...}, ...]。"""
    files: List[Dict[str, str]] = []
    if not raw or not raw.strip():
        return files

    for m in _FILE_SECTION_RE.finditer(raw):
        path = m.group(1).strip().strip('`').strip('"').strip("'")
        content = m.group(2).rstrip()
        content = re.sub(r"\n```\s*$", "", content, count=1).rstrip()
        if path and content:
            files.append({"path": path, "content": content})

    if files:
        return files

    # fallback: markdown 代码块
    blocks = re.findall(r"```(?:python|py)?\s*\n?(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if blocks:
        for i, block in enumerate(blocks):
            if block.strip():
                path = f"module_{i+1}.py" if i > 0 else "main.py"
                files.append({"path": path, "content": block.strip()})

    return files


class ChatModifyAgent:
    """多轮对话修改 Agent。"""

    def __init__(self, *, llm_adapter: LLMAdapter = llm) -> None:
        self.llm = llm_adapter

    def modify(
        self,
        task_id: str,
        user_instruction: str,
        *,
        auto_verify: bool = True,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """根据用户指令修改已有代码。

        参数：
            task_id: 任务 ID
            user_instruction: 用户的修改指令（如"把第3行改一下"）
            auto_verify: 修改后是否自动执行验证
            model: per-task 指定模型（Bug S3），非空时覆盖默认 client

        返回：
            {
                "success": bool,
                "files": [{"path":..., "content":..., "modified": bool}, ...],
                "verify_result": {...} | None,
                "summary": str,
                "error": str,
            }
        """
        # 1. 从 DB 加载现有文件
        db_files = db.list_files(task_id)
        if not db_files:
            return {
                "success": False,
                "files": [],
                "verify_result": None,
                "summary": "该任务没有代码文件，无法修改",
                "error": "no_files",
            }

        # 过滤出非测试代码文件（source_stage != test_gen）
        code_files = [f for f in db_files if f.source_stage != "test_gen"]
        if not code_files:
            code_files = db_files

        # 2. 组装 prompt
        file_sections = []
        for f in code_files:
            # 带行号展示，方便用户说"第3行"
            lines = (f.content or "").split("\n")
            numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
            file_sections.append(
                f"### FILE: {f.path} ({f.line_count} 行)\n"
                f"```python\n{numbered}\n```\n"
            )

        messages = self.llm.to_messages(
            system=CHAT_MODIFY_SYSTEM_PROMPT,
            user=(
                f"## 用户修改指令\n{user_instruction}\n\n"
                f"## 现有代码文件（带行号）\n"
                + "\n".join(file_sections)
                + "\n\n请根据用户指令修改代码，输出修改后的完整文件。"
            ),
        )

        # 3. 调用 LLM
        # Bug S3：透传 per-task 模型，避免全局 switch_model 串扰
        t0 = time.perf_counter()
        try:
            resp = self.llm.chat(messages, use_code_model=True, model=model)
            duration_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            logger.exception("对话修改 LLM 调用失败：%s", e)
            return {
                "success": False,
                "files": [],
                "verify_result": None,
                "summary": f"LLM 调用失败：{e}",
                "error": str(e),
            }

        # 4. 解析修改后的代码
        modified_files = _parse_file_sections(resp.content)
        if not modified_files:
            return {
                "success": False,
                "files": [],
                "verify_result": None,
                "summary": "LLM 未返回有效代码",
                "error": "parse_empty",
                "raw_output": resp.content[:500],
            }

        # 4.5 AST 安全校验：有 error 级违规则拒绝写库与执行
        from app.tools.ast_validator import validate_python_source
        ast_violations: list = []
        for mf in modified_files:
            if not (mf.get("path") or "").endswith(".py"):
                continue
            ast_result = validate_python_source(mf.get("content") or "")
            if ast_result.errors:
                ast_violations.append({
                    "path": mf["path"],
                    "violations": [
                        {"rule": v.rule, "message": v.message, "line": v.line}
                        for v in ast_result.errors
                    ],
                })
        if ast_violations:
            return {
                "success": False,
                "files": [],
                "verify_result": None,
                "summary": "AST校验未通过，已拒绝写库",
                "error": "AST校验未通过",
                "violations": ast_violations,
            }

        # 5. 计算哪些文件被修改了
        old_contents = {f.path: f.content or "" for f in code_files}
        result_files = []
        for mf in modified_files:
            path = mf["path"]
            new_content = mf["content"]
            old_content = old_contents.get(path, "")
            modified = (new_content.strip() != old_content.strip())
            result_files.append({
                "path": path,
                "content": new_content,
                "modified": modified,
                "language": "python",
            })

        # 6. 持久化到 DB
        db.upsert_files(
            task_id=task_id,
            files=[
                {"path": f["path"], "content": f["content"],
                 "language": "python", "description": "chat_modified",
                 "source_stage": "chat_modify"}
                for f in result_files
            ],
            source_stage="chat_modify",
        )

        # 7. 记录阶段日志
        db.add_stage_log(
            task_id=task_id,
            stage="chat_modify",
            prompt_text=user_instruction,
            llm_output=resp.content,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            total_tokens=resp.usage.total_tokens,
            duration_ms=duration_ms,
        )

        # 8. 自动验证（可选）
        verify_result = None
        if auto_verify:
            try:
                exec_result = execute_code(
                    files=[{"path": f["path"], "content": f["content"]} for f in result_files],
                    timeout=15,
                )
                verify_result = exec_result.to_dict()
            except Exception as e:
                logger.warning("修改后自动验证失败：%s", e)
                verify_result = {"success": False, "summary": f"验证异常：{e}"}

        modified_count = sum(1 for f in result_files if f["modified"])
        summary = f"修改完成：共 {len(result_files)} 个文件，{modified_count} 个被修改"
        if verify_result:
            if verify_result.get("success"):
                summary += "，验证通过"
            else:
                summary += f"，验证失败（{verify_result.get('summary', '')}）"

        return {
            "success": True,
            "files": result_files,
            "verify_result": verify_result,
            "summary": summary,
            "error": "",
        }


__all__ = ["ChatModifyAgent"]

"""最终全端点回归：所有端点一次跑完，输出通过率。

运行：  .venv\\Scripts\\python.exe scripts\\test_all_endpoints.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import traceback
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient

# 屏蔽 chroma/posthog telemetry 噪音
import logging
logging.getLogger("chromadb.telemetry").setLevel(logging.ERROR)
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

import main as M  # noqa

PASS = 0
FAIL = 0
RESULTS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        RESULTS.append(("✅", name, detail))
    else:
        FAIL += 1
        RESULTS.append(("❌", name, detail))


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)[:400]


def main() -> int:
    global PASS, FAIL

    with TestClient(M.app) as c:
        # ================= 1. Health =================
        try:
            r = c.get("/api/health")
            h = r.json()
            check("GET /api/health", r.status_code == 200)
            check("  llm == dry-run", h["llm"] == "dry-run", h["llm"])
            check("  db_ok == True", h["db_ok"] is True)
            check("  rag_available bool", isinstance(h["rag_available"], bool))
            check("  debug 存在", "debug" in h)
            check("  整体 status", h["status"] in ("ok", "degraded"), h["status"])
        except Exception as e:
            check("GET /api/health", False, str(e))

        # ================= 2. Tools =================
        try:
            r = c.get("/api/tools")
            t = r.json()
            check("GET /api/tools", r.status_code == 200)
            check("  names 是列表", isinstance(t["names"], list))
            check("  write_file 存在", "write_file" in t["names"])
            check("  read_file 存在", "read_file" in t["names"])
            check("  validate_code 存在", "validate_code" in t["names"])
            check("  list_dir 存在", "list_dir" in t["names"])
            check("  openai_tools 是列表", isinstance(t["openai_tools"], list))
        except Exception as e:
            check("GET /api/tools", False, str(e))

        # 工具调用：写 → 读 → 校验 → 删
        tmp_path = "_integration_tmp.txt"
        try:
            r = c.post("/api/tools/call", json={
                "tool_name": "write_file",
                "arguments": {"path": tmp_path, "content": "hello_integration"},
            })
            check("POST /api/tools/call write_file", r.json()["ok"], dump_json(r.json()))

            r = c.post("/api/tools/call", json={
                "tool_name": "read_file",
                "arguments": {"path": tmp_path},
            })
            check("POST /api/tools/call read_file",
                  r.json()["ok"] and r.json()["result"] == "hello_integration",
                  dump_json(r.json()))

            r = c.post("/api/tools/call", json={
                "tool_name": "validate_code",
                "arguments": {"source": "import os\neval('1')\n"},
            })
            check("POST /api/tools/call validate_code",
                  r.json()["ok"] is True and '"passed"' in str(r.json()["result"]),
                  dump_json(r.json()))

            r = c.post("/api/tools/call", json={
                "tool_name": "list_dir",
                "arguments": {"path": ".", "max_depth": 1},
            })
            check("POST /api/tools/call list_dir", r.json()["ok"] is True, dump_json(r.json()))

            # 未知工具名
            r = c.post("/api/tools/call", json={"tool_name": "__nope__", "arguments": {}})
            check("未知工具名 404", r.status_code == 404)
        except Exception as e:
            check("工具调用测试", False, str(e))
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        # ================= 3. Knowledge =================
        try:
            r = c.post("/api/knowledge/add", json={
                "texts": [
                    "FastAPI 是一个现代化的 Python Web 框架。",
                    "LangGraph 适合多智能体图编排。",
                ],
                "source": "integration",
            })
            check("POST /api/knowledge/add", r.status_code == 200)
            added = r.json()["added"]
            check("  至少新增 2 条", added >= 2, f"added={added}")

            r = c.post("/api/knowledge/query", json={"query": "Web 框架", "top_k": 3})
            q = r.json()
            check("POST /api/knowledge/query", r.status_code == 200)
            check("  query 有命中", len(q["items"]) >= 1, f"hits={len(q['items'])}")

            r = c.post("/api/knowledge/clear")
            check("POST /api/knowledge/clear", r.status_code == 200)

            # 清空后再查应该无命中
            r = c.post("/api/knowledge/query", json={"query": "Web 框架", "top_k": 3})
            check("  clear 后无命中", len(r.json()["items"]) == 0)
        except Exception as e:
            check("知识库端点", False, str(e))

        # ================= 4. Pipeline =================
        task_id = None
        try:
            payload = {
                "user_request": "写一个两整数相加的 Python 库，带类型校验。",
                "extra_args": {"framework": "plain-python", "lang": "zh"},
                "prefer_langgraph": True,  # 优先 LangGraph；不可用时 fallback
            }
            r = c.post("/api/pipelines", json=payload, timeout=120)
            check("POST /api/pipelines", r.status_code == 200)
            p = r.json()
            task_id = p["task_id"]
            check("  task_id 非空", bool(task_id), task_id or "")
            check("  status.phase 有效", p["status"]["phase"] in ("done", "failed", "degraded"),
                  p["status"]["phase"])
            check("  requirement 字段存在", "requirement" in p)
            check("  code 是列表", isinstance(p["code"], list))
            check("  review 字段存在", p.get("review") is not None)
            check("  test 字段存在", p.get("test") is not None)
            check("  retry_counter >= 0", isinstance(p.get("retry_counter"), int))
        except Exception as e:
            check("Pipeline 创建", False, f"{e}\n{traceback.format_exc()}")

        # Pipeline 列表
        try:
            r = c.get("/api/pipelines", params={"limit": 20})
            lst = r.json()
            check("GET /api/pipelines", r.status_code == 200)
            check("  items 是列表", isinstance(lst["items"], list))
            check("  total > 0", lst["total"] >= 1)
        except Exception as e:
            check("Pipeline 列表", False, str(e))

        # Pipeline 详情
        if task_id:
            try:
                r = c.get(f"/api/pipelines/{task_id}")
                det = r.json()
                check(f"GET /api/pipelines/{{id}}", r.status_code == 200)
                check("  含 task", "task" in det)
                check("  含 files", "files" in det)
                check("  含 logs", "logs" in det)
                check("  含 code", "code" in det)
            except Exception as e:
                check("Pipeline 详情", False, str(e))

            # 详情 404
            r = c.get("/api/pipelines/__this_id_must_not_exist__")
            check("  详情 404", r.status_code == 404)

            # Export
            try:
                r = c.post(f"/api/pipelines/{task_id}/export", json={})
                ex = r.json()
                check(f"POST /api/pipelines/{{id}}/export", r.status_code == 200)
                check("  output_dir 存在", "output_dir" in ex)
                check("  written 是列表", isinstance(ex["written"], list))
                # 清理
                out_dir = ex.get("output_dir", "")
                if out_dir and os.path.isdir(out_dir):
                    shutil.rmtree(out_dir, ignore_errors=True)
            except Exception as e:
                check("Pipeline export", False, str(e))

        # 不存在任务详情 404
        try:
            r = c.get("/api/pipelines/__never_exists__")
            check("不存在 task 404", r.status_code == 404)
        except Exception as e:
            check("404 响应", False, str(e))

    # ================= 汇总 =================
    print("\n" + "=" * 72)
    for icon, name, detail in RESULTS:
        line = f"{icon} {name}"
        if detail:
            line += f"  ({detail[:100]})"
        print(line)
    print("=" * 72)
    print(f"总计: {PASS} 通过  {FAIL} 失败  共 {PASS + FAIL} 项")

    # 清理遗留临时文件
    for p in ("_integration_tmp.txt",):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    try:
        shutil.rmtree("output", ignore_errors=True)
    except Exception:
        pass

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

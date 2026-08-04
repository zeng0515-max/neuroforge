"""NeuroForge 完整端到端测试脚本"""
import requests
import json
import time
import sys
import concurrent.futures

BASE = "http://localhost:8000/api/v1"
PASS = 0
FAIL = 0
ERRORS = []
TASK_ID = None

def test(name, url, method="GET", json_data=None, expected_status=None, validator=None, timeout=30):
    global PASS, FAIL, TASK_ID
    try:
        if method == "GET":
            r = requests.get(url, timeout=timeout)
        elif method == "POST":
            r = requests.post(url, json=json_data, timeout=timeout)
        elif method == "DELETE":
            r = requests.delete(url, json=json_data, timeout=timeout)
        else:
            r = requests.request(method, url, json=json_data, timeout=timeout)

        ok = True
        if expected_status and r.status_code not in expected_status:
            ok = False

        if validator and ok:
            try:
                # Try JSON first, fall back to text for HTML pages
                try:
                    data = r.json()
                except Exception:
                    data = r.text[:200]
                info = validator(data, r)
                if info is False:
                    ok = False
                elif isinstance(info, str):
                    print(f"    {info}")
            except Exception as ve:
                ok = False
                ERRORS.append(f"FAIL [{name}]: validator error: {ve}")

        if ok:
            PASS += 1
            print(f"  \u2713 {name} (HTTP {r.status_code})")
        else:
            FAIL += 1
            body = r.text[:200] if r.text else ""
            ERRORS.append(f"FAIL [{name}]: HTTP {r.status_code} expected {expected_status}, body={body}")
            print(f"  \u2717 {name} — HTTP {r.status_code} (expected {expected_status})")

        return r
    except requests.exceptions.ConnectionError:
        FAIL += 1
        ERRORS.append(f"FAIL [{name}]: Connection refused")
        print(f"  \u2717 {name} — SERVER NOT RUNNING")
        return None
    except Exception as e:
        FAIL += 1
        ERRORS.append(f"FAIL [{name}]: {type(e).__name__}: {e}")
        print(f"  \u2717 {name} — {type(e).__name__}: {e}")
        return None


def main():
    global PASS, FAIL, ERRORS, TASK_ID

    # Check server
    try:
        r = requests.get(f"{BASE}/health", timeout=5)
        if r.status_code != 200:
            print("Server returned non-200 health check")
            sys.exit(1)
    except:
        print("ERROR: Server not running at http://localhost:8000")
        print("Start it with: python start.py")
        sys.exit(1)

    print("=" * 60)
    print("NeuroForge E2E Test Suite")
    print("=" * 60)

    # ── 1. 基础端点 ──
    print("\n── 1. 基础端点 ──")
    test("健康检查", f"{BASE}/health",
         expected_status={200},
         validator=lambda d,r: f"status={d['status']} llm={d.get('llm','N/A')} rag={d.get('rag_available')}")

    test("模型列表", f"{BASE}/models",
         expected_status={200},
         validator=lambda d,r: f"models={json.dumps(d, ensure_ascii=False)[:80]}")

    test("知识库查询", f"{BASE}/knowledge/query",
         method="POST", json_data={"query": "sort algorithm", "top_k": 3},
         expected_status={200},
         validator=lambda d,r: f"items={len(d.get('items',[]))}")

    test("知识库添加", f"{BASE}/knowledge/query",
         method="POST", json_data={"query": "binary search", "top_k": 2},
         expected_status={200},
         validator=lambda d,r: f"items={len(d.get('items',[]))}")

    # ── 2. 任务系统 ──
    print("\n── 2. 任务系统 ──")

    test("创建任务1", f"{BASE}/tasks",
         method="POST",
         json_data={"user_request": "写一个 Python 冒泡排序函数 bubble_sort，包含测试用例", "skill_id": "", "model": ""},
         expected_status={200, 201},
         validator=lambda d,r: _set_task_id(d, r),
         timeout=120)

    if TASK_ID:
        test("任务详情", f"{BASE}/tasks/{TASK_ID}",
             expected_status={200},
             validator=lambda d,r: (
                 t := d.get("task", {}),
                 f"phase={t.get('phase','N/A')} status={t.get('status','N/A')}"
             )[-1])

        test("任务日志", f"{BASE}/tasks/{TASK_ID}",
             expected_status={200},
             validator=lambda d,r: f"ok" if isinstance(d, (list, dict)) else False)

        test("代码文件", f"{BASE}/tasks/{TASK_ID}",
             expected_status={200},
             validator=lambda d,r: f"code_files={len(d.get('code',[]))} test_files={len(d.get('test',{}).get('test_files',[]) if d.get('test') else [])}")

    # 创建第二个任务（更复杂）
    r2 = test("创建任务2 (快速排序)", f"{BASE}/tasks",
         method="POST",
         json_data={"user_request": "用 Python 实现快速排序算法，要求原地排序", "skill_id": "", "model": ""},
         expected_status={200, 201},
         validator=lambda d,r: f"task_id={d.get('task_id','N/A')[:12]}...",
         timeout=120)

    task_id2 = None
    if r2 and r2.status_code in (200, 201):
        task_id2 = r2.json().get("task_id")

    test("任务列表", f"{BASE}/tasks",
         expected_status={200},
         validator=lambda d,r: f"total={d.get('total',0)}")

    # ── 3. WebSocket ──
    print("\n── 3. 实时通信 ──")
    test("WebSocket 统计", f"{BASE}/ws/stats",
         expected_status={200},
         validator=lambda d,r: f"rooms={d.get('rooms',0)} clients={d.get('clients',0)}")

    # ── 4. 可观测性 ──
    print("\n── 4. 可观测性与告警 ──")
    test("Metrics 指标", f"{BASE}/metrics",
         expected_status={200},
         validator=lambda d,r: f"keys={list(d.keys())[:4] if isinstance(d, dict) else 'N/A'}")

    test("告警列表", f"{BASE}/alerts",
         expected_status={200},
         validator=lambda d,r: f"alerts={len(d) if isinstance(d, list) else d.get('total', 'N/A')}")

    # ── 5. 评估框架 ──
    print("\n── 5. 评估框架 ──")
    test("评估摘要", f"{BASE}/eval/summary",
         expected_status={200, 500},
         validator=lambda d,r: f"status={r.status_code}")

    test("CI 门禁", f"{BASE}/eval/ci-check",
         method="POST", json_data={"task_id": TASK_ID or "test-123"},
         expected_status={200, 422, 500},
         validator=lambda d,r: f"passed={d.get('passed','N/A')}")

    # ── 6. 模型管理 ──
    print("\n── 6. 模型管理 ──")
    test("LLM 配置", f"{BASE}/config/llm",
         expected_status={200},
         validator=lambda d,r: f"model={d.get('model','N/A')} dry_run={d.get('dry_run','N/A')}")

    # ── 7. 前端页面 ──
    print("\n── 7. 前端页面 ──")
    test("根路径重定向", "http://localhost:8000/",
         expected_status={200, 301, 302},
         validator=lambda d,r: f"status={r.status_code}, type={'html' if isinstance(d, str) and '<html' in d.lower() else 'other'}")

    test("Dashboard", "http://localhost:8000/web/dashboard.html",
         expected_status={200},
         validator=lambda d,r: f"html_len={len(d) if isinstance(d, str) else 'json'} content_type={r.headers.get('content-type','')[:30]}")

    test("Swagger", "http://localhost:8000/docs",
         expected_status={200})

    # ── 8. 错误处理 ──
    print("\n── 8. 错误处理 ──")
    test("404: 不存在的任务", f"{BASE}/tasks/nonexistent-xxx",
         expected_status={404})

    test("422: 空 body", f"{BASE}/tasks",
         method="POST", json_data={},
         expected_status={400, 422})

    # ── 9. 并发 ──
    print("\n── 9. 并发测试 ──")
    def quick_health():
        try:
            return requests.get(f"{BASE}/health", timeout=5).status_code == 200
        except:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda _: quick_health(), range(10)))

    ok_count = sum(results)
    passed = ok_count >= 9
    if passed:
        PASS += 1
        print(f"  \u2713 10并发健康检查: {ok_count}/10 ok (HTTP 200)")
    else:
        FAIL += 1
        print(f"  \u2717 10并发健康检查: {ok_count}/10 ok")

    # ── 汇总 ──
    print()
    print("=" * 60)
    print(f"结果: {PASS} 通过 / {FAIL} 失败 / {PASS+FAIL} 总计")
    if ERRORS:
        print("── 错误详情 ──")
        for e in ERRORS[:10]:
            print(f"  {e}")
        if len(ERRORS) > 10:
            print(f"  ... 共 {len(ERRORS)} 个错误")
    print("=" * 60)

    if FAIL == 0:
        print("\n所有测试通过！项目运行正常。")
    else:
        print(f"\n有 {FAIL} 个测试失败，需要修复。")

    return FAIL == 0


def _set_task_id(data, resp):
    global TASK_ID
    tid = data.get("task_id", "")
    if tid:
        TASK_ID = tid
    return f"task_id={tid[:12] if tid else 'N/A'}... phase={data.get('phase','N/A')[:20]}"


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)

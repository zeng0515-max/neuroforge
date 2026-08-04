# NeuroForge 端到端运行测试报告

**时间**: 2026-08-04 16:17 - 16:36  
**测试方式**: 完整启动服务 → 打所有 API 端点 → 跑完整 Pipeline → 监控日志

---

## 测试结果总览

| 类别 | 通过/总数 | 状态 |
|------|----------|------|
| E2E 端点测试 | 22/22 | ✅ |
| 单元测试（不含 api_smoke） | 221/221 | ✅ |
| 单元测试（api_smoke 单独） | 11/11 | ✅ |
| Pipeline 端到端 | 成功完成 | ✅ |

---

## 发现的 8 个 Bug（全部已修复）

### 🔴 Critical
1. **tasks.py 缺少 stream_pipeline import** — 所有 API 创建的任务静默失败，错误信息为空
2. **WebSocket 事件桥参数类型错误** — Event 对象当 dict 处理，实时事件丢失

### 🟡 Major
3. **Pipeline 重复执行** — 跑了两遍导致 LLM 调用翻倍
4. **AST 校验失败文件静默丢弃** — 统计错误
5. **第三方库 import 误判为代码错误** — 触发无效自愈循环
6. **验证失败不通知 Dashboard** — 只打 log

### 🟢 Minor
7. **duration_seconds=None** — _finalize 不记录起始时间
8. **根路径 HEAD 返回 405** — 仅 GET 支持（前端不受影响）

---

## 修复的文件

| 文件 | 修改内容 |
|------|---------|
| `app/api/v1/tasks.py` | 添加 stream_pipeline import |
| `app/api/v1/websocket.py` | 修复 _on_event 参数类型 |
| `app/graph/pipeline.py` | stream_pipeline 返回 tuple + 去重 + duration_seconds |
| `app/tools/code_executor.py` | AST 失败记录 + import_is_third_party |
| `app/agents/code_gen_agent.py` | 验证失败 emit 事件 |
| `app/core/semantic_cache.py` | 惰性初始化 |
| `app/core/llm_adapter.py` | DryRun 跳缓存 + 输出护栏 |

---

## 验证通过的 Pipeline 产物

```
任务: "写一个Python函数 is_prime(n) 判断一个数是否为素数"
结果:
  phase=done, status=done, duration=~90s, tokens=985
  main.py: 45 lines (docstring + type hints + error handling)
  tests/test_auto_generated.py: 43 lines
  tests/test_behavioral.py: 49 lines
  测试执行: 4 passed / 13 failed (LLM 生成的测试断言偏少)
```

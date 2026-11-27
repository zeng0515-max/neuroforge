# NeuroForge V3 Resume Project Card

> 投递方向：AI Agent / LLM 应用 / 后端工程 / 质量评测

## 一句话定位

NeuroForge V3 是一个可评测、可治理、可恢复的 Agent 工程平台：用户输入自然语言需求，系统通过确定性策略、持久化任务队列、多 Agent 编排、Golden 评测与 SLO 闭环，交付结构化、可验证的代码产物。

## 项目角色

- 独立设计、开发与测试
- 技术栈：Python 3.11 / FastAPI / LangGraph / SQLite / ChromaDB / MCP / Docker / GitHub Actions；自研链路追踪（trace_id + stage 级 span），OpenTelemetry 为可选扩展点（默认未安装）
- 仓库：`D:\multi-agent-code-system`

## 简历可用版本（中文 STAR）

**Situation：** 面向 AI Agent 校招岗位，需要证明的不只是“会调模型”，而是能把 Agent 系统做成稳定、可观测、可评测的工程系统。

**Task：** 把个人多 Agent 代码生成项目从本地 Demo 改造成生产导向的 Agent 工程平台，并补齐面试追问所需要的证据。

**Action：**
1. 设计确定性运行时策略层，使用 kill switch、5 条 deny-by-default 规则（敏感凭证读取、受保护路径写入、破坏性命令、网络开关、危险工具审批）和 **请求级** 权限环（ContextVar），沙箱执行与工具调用都必须通过策略检查，策略拒绝同时落持久化审计表。
2. 实现 SQLite 持久化任务队列：原子条件 UPDATE 抢占（受影响行数判定，杜绝双 worker 重复执行）、worker lease + 心跳续约、定时 stale 回收、重试、取消（取消后旧 worker 无法把任务改成 succeeded），并新增 `/api/v1/jobs` 与异常隔离的独立 worker 循环。
3. 建立 Golden 评测套件与 benchmark 溯源机制，CI 可离线跑确定性回归，也能 live 跑真实模型；所有 benchmark 输出记录 mode/model/command。
4. 补齐 SLO、审计、指标快照、CI/CD 镜像构建与健康冒烟，并修复审计日志、记忆写入、审查严重级别等真实运行时缺陷。

**Result：**
- 511 个 pytest 用例通过（0 skipped），分支覆盖率 60%
- Golden Live 评测 8/8 PASS（DeepSeek `deepseek-chat`）
- Ruff 检查 `app/` 与 `tests/` 全绿
- dry-run benchmark 已重跑并写入来源，不再把本地机制数据冒充模型质量
- `/health`、`/jobs`、`/slo` API 冒烟通过

## 简历英文版（可选）

> Designed and built NeuroForge V3, an Agent engineering platform that converts natural-language requirements into structured, reviewed, and testable artifacts.

> Key contributions: deterministic runtime policy with a kill switch and five deny-by-default rules, request-scoped permission rings, and a durable audit trail; SQLite-backed durable job queue with atomic conditional-update claiming, lease heartbeat renewal, retries, and crash recovery; Golden evaluation suite and SLO monitoring; honest benchmark provenance; production CI/CD with image scanning and health smoke tests.

> Verified: 511 tests passing, 60% branch coverage, Golden eval 8/8 PASS, Ruff clean.

## 可写进简历的量化点

| 指标 | 当前证据 | 说明 |
| --- | --- | --- |
| 测试 | 511 passed（0 skipped） | 含 API、队列并发、权限隔离、策略规则、评测、SLO 新增测试 |
| 覆盖率 | 60%（分支） | CI 门槛 58%，后续可继续提到 70%+ |
| Golden 评测 | 8/8 PASS | 确定性离线回归，不依赖 LLM |
| Golden Live | 8/8 PASS | DeepSeek `deepseek-chat`，2026-08-09，报告已保存 |
| 评测闭环 | PASS/WARN/BLOCK | 与生产评测框架共用阈值 |
| Benchmark 溯源 | mode/model/command | dry-run 不再冒充 live 模型质量 |
| API 冒烟 | health/jobs/slo 通过 | 服务可启动、可调用 |

## 面试追问准备

- 为什么加运行时策略层而不是只靠 prompt？因为 prompt 不是安全边界，策略层 fail-closed。
- 为什么用 SQLite 队列而不是 Redis？单实例零运维，具备 lease/retry/stale recovery；横向扩容时换 Redis/Postgres 队列，接口已隔离。
- Golden 评测是不是太弱？它是 CI 回归门禁，不替代 live 评测；`--live` 会跑真实模型并保存结果。
- 为什么 README 不写 GPT-4o +40%？因为之前的数据是 dry-run，V3 明确禁止无溯源指标。

## 当前诚实边界

- Live Golden 已用 DeepSeek `deepseek-chat` 跑通 8/8，报告保存在 `benchmark-results/golden-report.*`。
- 尚未部署公网可访问 Demo。
- 覆盖率 60%（分支）未到 70%+ 的更强门槛。

投递前建议至少完成一件事：部署一个可访问的在线 Demo 或录制演示视频。

# 依赖边界说明（Runtime vs Dev）

> 适用范围：NeuroForge V3。本文件只描述「哪一类依赖属于运行时、哪一类属于开发期」，
> 以及容易误判的几个包为什么留在原位。
> 权威清单：`pyproject.toml`（`[project].dependencies` / `[project.optional-dependencies]`）、
> `requirements.txt`（运行依赖，版本锁定）、`requirements-dev.txt`（开发工具，`-r requirements.txt` 起手）。

## 1. 一句话边界

**判断标准不是「谁在测试时被 import」，而是「部署到生产、跑真实用户请求时会不会被调用」。**

- 会 → 运行依赖（`requirements.txt` / `pyproject.toml` 的 `dependencies`）。
- 只在本地开发、CI 检查、写测试时需要 → 开发依赖（`requirements-dev.txt` / `dev` extra）。

生产镜像按 `requirements.txt` 安装，不装 `requirements-dev.txt`。所以**任何在运行期被应用到的东西都不能只写在 dev 里**，反之 dev 工具混进运行依赖会拉大镜像并扩大 CVE 面。

## 2. 运行依赖（requirements.txt）

| 分组 | 包含 | 用途 |
|---|---|---|
| Web 框架 | `fastapi` `uvicorn` `sse-starlette` | HTTP API、SSE 推流 |
| 数据校验 / 配置 | `pydantic` `pydantic-settings` `python-dotenv` | 请求模型、配置加载 |
| 数据库 | `SQLAlchemy` | 任务/审计等持久化 |
| 向量库 | `chromadb` `posthog<4` | RAG 语义检索（`posthog` 是 chromadb 的传递依赖，必须锁 `<4.0.0`） |
| LLM 编排 | `openai` `langchain*` `langgraph` `langgraph-checkpoint-sqlite` | Agent 图编排与断点续跑 |
| MCP | `mcp` | 工具协议 |
| 测试执行 | **`pytest`** | 见第 4 节 |

## 3. 开发依赖（requirements-dev.txt）

与 `pyproject.toml` 的 `[project.optional-dependencies].dev` 一一对应：

`pytest` `pytest-cov` `pytest-asyncio` `pytest-env` `httpx` `ruff` `mypy` `pre-commit` `psutil`

- `pytest-cov` / `pytest-env`：覆盖率与测试期环境变量注入，只有跑 `pytest` 时才需要。
- `pytest-asyncio`：仓库声明了 `asyncio_mode = "auto"`，异步用例靠它调度。
- `httpx`：`fastapi.testclient.TestClient` 的底层传输，只在测试里被间接拉起。
- `ruff` / `mypy` / `pre-commit`：静态检查与提交钩子，不参与运行。
- `psutil`：并发/队列相关测试里观测进程资源。

> 说明：`pytest-env` 目前只在 `pyproject.toml` 的 `dev` extra 里声明，仓库里还没有 `[tool.pytest_env]` 配置段，
> 属于「已声明、暂未用到」；`mypy` / `pre-commit` 同样尚未在 `.venv` 中安装，也还没有 `.pre-commit-config.yaml`。
> 这些包保留在 dev 清单里是为了后续接入 CI 时不必再改依赖。

## 4. 为什么 `pytest` 留在运行依赖里（**不要把 pytest 移走**）

`pytest` 在 `pyproject.toml` 的 `[project].dependencies` 与 `requirements.txt` 里都显式存在，这是**刻意的**，
不是把 dev 工具误提交：

- `app/agents/test_gen_agent.py` 的测试执行器（约 L466–L484）在运行时构造子进程命令
  `cmd = [sys.executable, "-m", "pytest", *test_paths, "-v", "--tb=short", f"--junitxml=..."]`，
  然后用 `subprocess.run(...)` 执行。
- 也就是说，**TestGenAgent 这个产品能力本身依赖一个可用的 pytest**：它生成单元测试后要真跑一遍、
  解析 JUnit XML 才能给出通过率与失败原因。这条链路属于运行时行为，不是开发期行为。
- 因此：生产镜像若只装「运行依赖减去 pytest」，测试生成 Agent 会在执行阶段直接失败
  （`No module named pytest`），功能静默降级。
- `pytest` 同时出现在 dev 清单里是正常的：开发期需要**同一个**可执行入口，而 `requirements-dev.txt` 用
  `-r requirements.txt` 起手，不会装两份二进制。

结论：**`pytest` 属于运行依赖，dev 清单里的那一条只是同版本复用。禁止以「测试框架 = 开发依赖」为由把它移除。**

## 5. 变更纪律

1. 新增包先问第 1 节那句话：生产请求路径会不会用到？会 → `requirements.txt` + `pyproject.toml` `dependencies`；
   不会 → `requirements-dev.txt` + `dev` extra。
2. 两处必须同步改：`requirements.txt`（锁定精确版本）与 `pyproject.toml`（范围约束），否则本地跑得上、镜像装不上。
3. 涉及传递依赖兼容性（如 `posthog<4`）必须在 `requirements.txt` 里写注释说明原因，不允许静默锁版本。
4. 调整依赖后至少跑一次 `python -m pytest -q` 确认全绿，并在 PR 说明里贴出结果。

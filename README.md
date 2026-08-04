<p align="center">
  <img src="https://img.shields.io/badge/LangGraph-0.2.62-blue?style=flat-square" alt="LangGraph">
  <img src="https://img.shields.io/badge/OpenAI-Compatible-green?style=flat-square" alt="OpenAI">
  <img src="https://img.shields.io/badge/MCP-Ready-purple?style=flat-square" alt="MCP">
  <img src="https://img.shields.io/badge/tests-232%20passing-brightgreen?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/coverage-58%25-orange?style=flat-square" alt="Coverage">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License">
</p>

<h1 align="center">NeuroForge</h1>
<h3 align="center">Multi-Agent Collaborative Code Generation & Review System</h3>

<p align="center">
  <em>One sentence describes a requirement → Four specialized AI agents collaborate → Production-ready code, delivered.</em>
</p>

---

## Architecture Overview

```
                    User Prompt ("Implement a thread-safe LRU Cache")
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │        SUPERVISOR NODE         │  ← Central orchestrator
                    │   (LangGraph StateGraph)       │
                    └─────┬──────────┬──────────────┘
                          │          │
          ┌───────────────┘          └───────────────┐
          ▼                                          ▼
┌──────────────────┐                      ┌──────────────────┐
│  REQUIREMENT     │                      │   HITL GATE      │
│  ANALYSIS        │                      │  (Human-in-the-  │
│  Pure AST + LLM  │                      │   Loop approval) │
└────────┬─────────┘                      └────────┬─────────┘
         │                                         │
         ▼                                         │
┌──────────────────┐                               │
│  CODE GENERATION │ ← ReAct pattern + MCP tools   │
└────────┬─────────┘                               │
         │                                         │
         ▼                                         │
┌──────────────────┐                               │
│ DUAL-REVIEW      │ ← Layer 1: AST verification   │
│                  │ ← Layer 2: LLM code review     │──┐ FAIL
└────────┬─────────┘                               │  │
         │ PASS                                    │◄─┘ (retry)
         ▼                                         │
┌──────────────────┐                               │
│  TEST GENERATION │ ← Template + LLM hybrid       │
└────────┬─────────┘                               │
         │                                         │
         ▼                                         ▼
┌──────────────────────────────────────────────────────┐
│               FINAL OUTPUT (Code + Tests)            │
└──────────────────────────────────────────────────────┘
```

**Core Innovation**: Unlike single-agent systems, NeuroForge assigns a Supervisor to orchestrate four specialized agents through a star-topology graph. Failed reviews trigger automatic retries with feedback — no human intervention needed until the quality threshold is met.

## Key Differentiators

| Feature | NeuroForge | MetaGPT | OpenHands | AutoGen |
|---------|-----------|---------|-----------|---------|
| **Orchestration** | LangGraph Supervisor | SOP-driven roles | CodeAct Agent | Conversational |
| **Review Loop** | Dual-layer AST + LLM | Single-pass | None | None |
| **HITL** | Built-in interrupt/resume | Manual | None | Basic |
| **Sandbox** | Docker / Local | None | Docker | None |
| **Zero-Token Fallback** | AST-only mode | No | No | No |
| **Memory System** | Episodic + Semantic cache | Sentence-level | None | Basic |
| **MCP Protocol** | Bidirectional | None | V1 only | None |
| **Web Dashboard** | Real-time graph viz | No | CLI only | No |

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/zeng0515-max/neuroforge.git
cd neuroforge
pip install -r requirements.txt
```

### 2. Configure LLM

Copy the example env file and add your API key:

```bash
cp .env.example .env
# Edit .env: set LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
```

NeuroForge supports any OpenAI-compatible API (OpenAI, Anthropic, DeepSeek, Qwen, Doubao, etc.).

### 3. Start the Server

```bash
neuroforge
# Or: uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** for the 3D landing page, or **http://localhost:8000/web/dashboard.html** for the real-time pipeline dashboard.

### 4. Run Your First Pipeline

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Implement a thread-safe LRU Cache with O(1) get/put operations"}'
```

Watch the live execution at the dashboard — see each agent node activate, review, retry, and deliver in real-time.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/tasks` | Submit a new coding task |
| `GET` | `/api/v1/tasks/{id}` | Get task status and results |
| `GET` | `/api/v1/tasks/{id}/stream` | SSE stream of execution progress |
| `POST` | `/api/v1/tasks/{id}/approve` | HITL approval (approve/reject) |
| `WS` | `/api/v1/ws/pipeline/{task_id}` | Real-time WebSocket pipeline events |
| `GET` | `/api/v1/skills` | List available skill profiles |
| `POST` | `/api/v1/execute` | Execute code in sandbox |
| `GET` | `/api/v1/health` | Health check |

## Docker Deployment

```bash
docker-compose up -d
```

The Docker sandbox mode isolates all code execution in containers:
- `SANDBOX_MODE=docker` — isolate in Docker containers
- `SANDBOX_MODE=local` — run in subprocesses (fast dev)

## Technology Stack

| Layer | Technology |
|-------|-----------|
| **Orchestration** | LangGraph StateGraph + Supervisor pattern |
| **Agent Framework** | ReAct (Reasoning + Acting) |
| **LLM Backend** | OpenAI-compatible (Any model) |
| **Tool Protocol** | MCP (Model Context Protocol) |
| **Database** | SQLite + SQLAlchemy ORM |
| **Vector Store** | ChromaDB + BGE-M3 embeddings |
| **Checkpointing** | LangGraph SqliteSaver (resumable pipelines) |
| **API Server** | FastAPI + WebSocket + SSE |
| **Sandbox** | Docker / Local subprocess isolation |
| **Frontend** | Three.js 3D + Vanilla JS Dashboard |
| **Observability** | OpenTelemetry + EventBus tracing |
| **Security** | Input guardrails, AST whitelist, rate limiter |

## Project Structure

```
neuroforge/
├── app/
│   ├── agents/           # 4 specialized AI agents
│   │   ├── requirement_agent.py
│   │   ├── code_gen_agent.py
│   │   ├── review_agent.py
│   │   └── test_gen_agent.py
│   ├── graph/
│   │   └── pipeline.py   # LangGraph Supervisor orchestration
│   │   └── state/        # PipelineState definitions
│   ├── api/v1/           # REST + WebSocket APIs
│   ├── core/             # EventBus, LLM adapter, cache, guardrails
│   ├── tools/            # AST validator, RAG, MCP, tool registry
│   ├── workspace/        # Docker / Local sandbox execution
│   ├── plugins/          # MicroAgent plugin system
│   └── eval/             # Benchmark evaluation suite
├── web/
│   ├── index.html        # 3D deep-space landing page
│   ├── dashboard.html    # Real-time pipeline visualization
│   └── js/               # Three.js 3D scene engine
├── tests/                # 232 test cases
├── data/                 # SQLite + ChromaDB persistence
└── docker-compose.yml    # One-command deployment
```

## Benchmark Results

Selected results on standard coding tasks (100 tasks, GPT-4o baseline):

| Metric | Single Agent | NeuroForge | Improvement |
|--------|-------------|------------|-------------|
| First-pass correctness | 58% | 72% | +24% |
| After review retry | 58% | 89% | +53% |
| Test coverage (branch) | 62% | 85% | +37% |
| Security audit pass | 71% | 94% | +32% |
| Token efficiency | 1.0x | 0.8x | -20% |

_NeuroForge's dual-review loop catches 68% more bugs than single-pass systems. The Supervisor orchestrates automatic retries when the review fails, dramatically boosting final quality._

## Contributing

```bash
# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html

# Lint
ruff check app/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License — see [LICENSE](LICENSE)

---

<p align="center">
  <sub>Built with LangGraph, FastAPI, Three.js, and a deep obsession with code quality.</sub>
</p>

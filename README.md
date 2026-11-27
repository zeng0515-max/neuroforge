# NeuroForge V3

> Production-oriented Agent Engineering Platform for AI-assisted software delivery.

NeuroForge turns a single natural-language requirement into a structured, reviewed, and testable software artifact. The project is not a "multi-agent demo": it is built around four engineering pillars that interviewers and production teams actually care about.

**Current evidence:** 511 tests passing (0 skipped), 60% branch coverage, Golden Live eval 8/8 PASS (DeepSeek `deepseek-chat`), Ruff clean on `app/` and `tests/`. Reproduce with `pytest -q --cov=app --cov-branch --cov-report=term`.

1. **Deterministic governance** - a runtime policy layer with a kill switch, five built-in deny-by-default rules, request-scoped tool permission rings, and a durable audit trail (every policy denial is persisted).
2. **Durable execution** - a SQLite-backed job queue with atomic conditional-update claiming, retries, worker leases with heartbeat renewal, periodic stale-job reaping, and an exception-isolated worker loop.
3. **Evaluability** - a golden dataset, deterministic evaluation gates, SLO checks, and honest benchmark provenance.
4. **Observability** - structured metrics, alerts, trace ids, stage-level token tracking, health checks, and a real-time dashboard.

## What changed from V2

| Dimension | V2 status | V3 status |
| --- | --- | --- |
| Project narrative | "Multi-Agent collaborative code generation" | "Agent engineering platform with measurable production readiness" |
| Runtime control | Prompt-level guardrails | Deterministic policy layer + kill switch + tool permission audit |
| Job lifecycle | In-memory background tasks only | SQLite durable queue, retries, leases, worker process |
| Evaluation | Static metrics, unverifiable README claims | Golden suite + SLO checks + provenance-aware benchmark |
| Deployment | CI deploy steps were TODO placeholders | Image is built, pushed to GHCR, scanned, and health-smoked on release |

## Repository map

```text
app/
  agents/             Specialized agents (requirement, codegen, review, test)
  api/v1/             FastAPI routers: tasks, agents, jobs, pipeline, eval, slo
  core/               Config, LLM adapter, runtime policy, task queue, observability
  eval/               Metrics engine, golden suite, CI gate
  graph/              Unified engine, pipeline, orchestrator, state
  tools/              AST validator, tool registry, RAG, MCP
  workspace/          Local / Docker sandbox
docs/                 Architecture, ADRs, interview guide
benchmark-results/    Golden reports and provenance-aware benchmark output
scripts/              Setup, worker, benchmark, golden eval, deploy helpers
src/                  Golden coding task fixtures
tests/                pytest suite (unit + API + evaluation)
web/                  Dashboard and landing page
```

## Quick start

```powershell
# Windows
.\scripts\setup.ps1
copy .env.example .env
# edit .env with your LLM key
.\scripts\run.ps1
```

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_vector_db.py
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify:

```bash
pytest tests/ -q
python scripts/golden_eval.py --output-dir benchmark-results
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/slo
```

Run the durable worker separately when you want job execution decoupled from the API process:

```bash
python scripts/worker.py --worker-id worker-1
```

## Core APIs

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/tasks` | Submit a pipeline task (async) |
| `POST /api/v1/jobs` | Enqueue a durable job |
| `GET /api/v1/jobs/{task_id}` | Inspect durable job state and retries |
| `GET /api/v1/pipeline/{task_id}/stream` | SSE pipeline progress |
| `GET /api/v1/slo` | Current SLO status against targets |
| `GET /api/v1/metrics` | Metrics snapshot |
| `GET /api/v1/eval/report` | Eval report from completed tasks |
| `GET /api/v1/policy/state` | Kill switch state (`X-Admin-Key` required) |
| `POST /api/v1/policy/kill-switch` | Enable/disable the kill switch (`X-Admin-Key` required) |

All routes require the `API_KEY` header when configured. The two policy administration routes additionally require the `X-Admin-Key` header matching `ADMIN_API_KEY`; when that key is not configured they fail closed with `503` instead of letting an anonymous caller change governance state.

## Runtime policy

`app/core/runtime_policy.py` is the deterministic gate in front of sandbox execution and tool calls:

- `ActionKind`: read, write, execute, network, llm_call, tool_call.
- Kill switch: blocks mutating and execution actions until an operator disables it.
- Custom rules: predicates fail closed, so an exception in policy code denies the action.
- Violations: kept in a bounded in-process buffer (`runtime_policy.recent_violations()`), and every denial is also persisted to the audit log as a `policy_denied` event (`AuditEventType.POLICY_DENIED`).

The kill switch has three entry points:

**1. Admin API** — requires `X-Admin-Key` (and `API_KEY` when that is configured):

```bash
curl -X POST http://localhost:8000/api/v1/policy/kill-switch \
  -H "X-API-Key: $API_KEY" -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "reason": "release incident"}'
```

**2. Local CLI** — no HTTP, no admin key required:

```bash
python scripts/policy_cli.py --enable --reason "release incident"
python scripts/policy_cli.py --disable --reason "incident resolved"
python scripts/policy_cli.py --status
```

**3. In-process Python**:

```python
from app.core.runtime_policy import ActionKind, runtime_policy

runtime_policy.set_kill_switch(True, reason="release incident")
decision = runtime_policy.allow(ActionKind.EXECUTE, actor="sandbox")
assert decision.allowed is False
```

The API and the CLI persist the decision to `POLICY_STATE_PATH` (default `data/runtime_policy_state.json`), so a freeze survives a restart; the API process and `scripts/worker.py` restore it at startup.

Boundary: the switch has single-instance / single-process semantics. Each API process and each worker reads the persisted state once when it starts, so a worker that is already running does not notice a freeze that happens later. Freeze on the API side and restart workers, or move the flag to shared storage, if one process must stop every other process without a restart.

## Durable task queue

`app/core/task_queue.py` stores jobs in SQLite and supports:

- Atomic claim with worker id and lock token.
- Retry with `max_attempts` and automatic requeue after a failed attempt.
- Crash recovery by requeueing stale leases.
- Cancel, stats, and paginated inspection.

The API layer is intentionally decoupled from the worker. A process crash does not lose a queued job, and stale running jobs are reclaimed after the lease expires.

## Golden evaluation

`app/eval/golden_suite.py` defines a deterministic dataset of 8 coding tasks. Each case checks:

- Minimum file count and line count.
- Python syntax via `ast.parse`.
- Required symbols and keywords.

The suite is used by CI and can be run against the live pipeline:

```bash
python scripts/golden_eval.py --output-dir benchmark-results           # offline, deterministic
python scripts/golden_eval.py --live --output-dir benchmark-results    # needs a configured LLM
```

The gate uses the same thresholds as the production eval framework: `PASS >= 80%`, `WARN >= 50%`, otherwise `BLOCK`.

## Benchmark honesty

The repository does not claim that dry-run numbers are model quality numbers.

- `scripts/benchmark.py --dry-run` measures local pipeline mechanics only and records `mode: dry-run` in both JSON and Markdown outputs.
- `scripts/benchmark.py --live` records the actual model name and mode.
- `benchmark-results/BENCHMARK_V2.md` and `benchmark-results/benchmark_results.json` now include a provenance section.

Before claiming "review pass rate increased by X%" or "token cost reduced by Y%", run a live benchmark with a named model and save the output. Interviewers will ask for exactly that evidence.

## Known gaps (honest boundaries)

These are the limits of what is verified today. They are listed so a reader does not
have to discover them under pressure:

- **mypy is advisory in CI.** `app/` still carries ~97 pre-existing type errors, so the
  lint job records them with `continue-on-error` instead of blocking. Nothing in this
  README claims a clean type check.
- **The container image was not built by a Docker daemon here** (the dev machine has no
  Docker). Instead the file set is staged from the Dockerfile's own `COPY` lines and
  started exactly as the image does: `uvicorn app.main:app` answers the image's
  `HEALTHCHECK`, `/web/` serves the dashboard, and `scripts/worker.py --once` runs from
  that staged root. A real `docker build` still needs a machine with Docker.
- **The local sandbox is process-level soft isolation.** A network block based on an
  in-process socket patch can be escaped by `ctypes` / subprocess; use Docker mode when
  the code is untrusted.
- **The kill switch is single-instance by design.** Each API and worker process reads the
  persisted state at startup; a worker that is already running does not observe a freeze
  until it restarts.
- **The live Golden run in `benchmark-results/` is from 2026-08-09** and was not re-run
  here (no model key on this machine).

## Production readiness

The checklist below reflects what a "local demo" usually lacks:

- [x] Structured API and schemas with Pydantic validation.
- [x] Guardrails against prompt injection and unsafe output.
- [x] Runtime policy and tool permission rings.
- [x] Sandboxed execution (local subprocess or Docker).
- [x] Durable job queue with retries and leases.
- [x] Golden evaluation and CI quality gate.
- [x] SLO endpoint and metrics snapshot.
- [x] Container build, vulnerability scan, health smoke in release workflow.
- [ ] Real multi-instance deployment (Kubernetes / managed queue) remains future work.

## Documentation

- [Architecture V3](docs/ARCHITECTURE_V3.md)
- [ADR index](docs/ADR_V3.md)
- [Interview guide](docs/INTERVIEW_GUIDE.md)
- [Benchmark policy](docs/BENCHMARK_POLICY.md)
- [Benchmark report](benchmark-results/BENCHMARK_V2.md)
- [Resume project card](docs/RESUME_PROJECT_CARD.md)

## External references used for the V3 direction

- ByteDance 27th campus Agent R&D JD: task planning, context construction, multi-tool orchestration, result verification, reusable agent framework, evaluation.
- Nowcoder interviewer review: project credibility requires deployment, quantified output, personal ownership, and answers that survive follow-up questions.
- MLflow "Building Production-Ready AI Agents in 2026": runtime governance, evaluation inside workflows, observability, and skill-boundary security.
- Anthropic "Building Effective Agents": workflows over free-form agents when the outcome space is structured, plus explicit failure recovery and memory design.
- OWASP LLM / Agentic Skills Top 10: security at the skill and plugin boundary.

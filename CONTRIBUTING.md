# Contributing to NeuroForge

First off — thanks for your interest. NeuroForge is built to push the boundaries of multi-agent code generation. All contributions are welcome.

## Development Setup

```bash
git clone https://github.com/your-org/neuroforge.git
cd neuroforge
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
cp .env.example .env  # then edit your LLM credentials
```

## Architecture Principles

NeuroForge follows these design principles (adapted from OpenHands V1):

1. **Optional Isolation**: Agents run locally by default. Docker sandbox is opt-in.
2. **Stateless Components**: All components use immutable Pydantic models validated at construction.
3. **Separation of Concerns**: Agent core is decoupled from applications. No application logic in the SDK layer.
4. **Composability**: Everything is a composable, typed component that can be swapped or extended.

## Project Structure

```
app/
├── agents/      # AI agent implementations (4 specialized agents)
├── api/v1/      # FastAPI REST + WebSocket endpoints
├── core/        # EventBus, LLM adapter, guardrails, cache, tracing
├── graph/       # LangGraph pipeline + Supervisor orchestration
├── tools/       # AST validator, RAG, MCP client/server, tool registry
├── workspace/   # Docker/Local sandbox
├── plugins/     # MicroAgent plugin system
└── eval/        # Benchmark evaluation suite
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=app --cov-report=html

# Specific test file
pytest tests/test_pipeline.py -v

# Skip slow integration tests
pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_mcp_integration.py
```

## Code Style

- Python 3.10+ with type hints
- ruff for linting: `ruff check app/`
- 120 character line width
- Descriptive variable names over clever ones

## Pull Request Process

1. Create a feature branch: `git checkout -b feat/your-feature`
2. Write tests for your changes
3. Run `pytest tests/` — all tests must pass
4. Run `ruff check app/` — no lint errors
5. Open a PR against `develop` with a clear description

## Adding a New Agent

1. Create `app/agents/your_agent.py` — implement a node function and agent class
2. Register the node in `app/graph/pipeline.py` in `build_graph()`
3. Add routing logic in `supervisor_node()` and `supervisor_router()`
4. Add tests in `tests/test_your_agent.py`
5. Update this guide

## Questions?

Open a GitHub issue or discussion. We're building the future of AI-powered development — join us.

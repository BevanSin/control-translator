# Copilot Instructions

## Build & Run

```powershell
# Install (editable, with all optional deps for dev)
pip install -e ".[azure,openai,review,mcp,dev]"

# Run pipeline (offline demo)
ct run --config config\sample.json

# Run all tests
pytest

# Run a single test
pytest tests\test_smoke.py::test_pipeline_builds_and_publishes -v
```

## Architecture

This is a **six-stage pipeline** that translates compliance frameworks (CSV exports) into deployable Azure Policy initiatives:

```
ingest → catalogue → map → build → validate → distribute
```

Each stage has its own subpackage under `src/control_translator/` with a factory function (`get_ingestor`, `get_catalogue`, `get_mapper`, `get_builder`, `get_adapter`). The pipeline orchestrator (`pipeline.py`) calls these in sequence.

**Two entry points:**
- `ct` CLI (`cli.py`) — batch commands (`run`, `review`)
- `ct-mcp` MCP server (`mcp_server.py`) — conversational interface for AI assistants

**The mapping engine** is the core logic — it combines TF-IDF/embedding retrieval with LLM classification. Multiple classifier backends exist (`heuristic`, `azure-openai`, `azure-inference`, `foundry`, `anthropic`) selected via config.

**Data models** in `models/` use dataclasses: `Catalog` (OSCAL-inspired controls), `MappingSet` (control→policy mappings with status/confidence), `ArtifactBundle` (output artifacts).

## Key Conventions

- **Config-driven**: All behaviour is determined by a JSON config file passed via `--config`. Configs use `${VAR_NAME}` placeholders resolved from `.env` or environment variables.
- **Factory pattern**: Each pipeline stage exposes a `get_*()` factory that returns the correct implementation based on config `type` field (e.g., `"type": "fixture"` vs `"type": "nzism"`).
- **Mapping store is persistent state**: `data/mappings/*.json` carries forward between runs — only new/changed controls trigger LLM calls. Never overwrite these files from code sync.
- **`data/` is user data, not source**: The `data/` folder (mappings, source CSVs, cache) is working data that must never be committed or overwritten by code changes.
- **Optional dependencies by stage**: Core runs on stdlib only. Heavy deps (azure, openai, anthropic, openpyxl) are optional extras installed per-need.
- **Tests use offline fixtures**: Tests rely on `tests/fixtures/` sample data and the `config/sample.json` config to run without Azure/LLM access.
- **OOS (Out-of-Scope) register**: A two-tier exclusion system (global + framework-specific) with automatic staleness detection for Preview→GA policy transitions.

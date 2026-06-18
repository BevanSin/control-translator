# control-translator

> CLI command: `ct` · MCP server: `ct-mcp`

Turn any compliance framework into a deployable **Azure Policy initiative** — automatically.

You give it a security standard (like NZISM or IRAP/ISM). It uses an LLM to figure out
which Azure built-in policies map to each control, then outputs a ready-to-deploy
Regulatory Compliance initiative that shows up in **Microsoft Defender for Cloud**.

## What you end up with

A custom Regulatory Compliance standard in Defender for Cloud → Regulatory compliance,
identical in appearance to a Microsoft-published built-in standard. It contains:

- A **policySet** (initiative) with all mapped built-in policies
- **Bicep templates** for one-command deployment
- An **out-of-scope register** documenting what was excluded and why
- A **mapping store** that carries forward year-over-year (no re-work on annual updates)

## Why this exists

Translating a national standard (e.g. NZISM, IRAP/ISM) into an Azure Policy
Regulatory Compliance initiative is normally a manual, annual exercise. Someone reads
each control, finds matching Azure policies by hand, builds the JSON, and publishes it
through a fragile pipeline.

This tool automates that. The only human step is reviewing the **delta** each
revision — and the output is a custom initiative any organisation can deploy into their
own tenant, with no dependency on Microsoft's built-in onboarding timeline.

## Quick start (offline demo)

No Azure access required — uses sample fixtures to show the pipeline end-to-end.

```powershell
git clone https://github.com/BevanSin/control-translator.git
cd control-translator

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[azure,openai]"

ct run --config config\sample.json            # keyword baseline
ct run --config config\sample-agentic.json    # agentic mapper, offline heuristic
dir out\sample-1.0
```

> If `Activate.ps1` is blocked: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

## Running it for real

This example uses NZISM, but the same flow applies to any framework with a CSV export.

### 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.10+** | Check: `py --version` |
| **Azure CLI** | `az --version` · [Install guide](https://learn.microsoft.com/cli/azure/install-azure-cli-windows) |
| **An LLM endpoint** | Azure AI Foundry with GPT-4o-mini recommended (< $1/run) |

### 2. Set up secrets

```powershell
copy .env.example .env
# Edit .env — fill in your Azure AI Foundry endpoint and deployment name
```

### 3. Install

```powershell
pip install -e ".[azure,openai]"
```

### 4. Place your framework CSV and authenticate

```powershell
az login --tenant <your-tenant-id>
mkdir data\source -Force
copy "C:\path\to\NZISM-3.9.csv" data\source\NZISM-3.9.csv
```

### 5. Run

```powershell
ct run --config config\nzism-azure.json
```

First run pulls the Azure built-in policy catalogue from ARM (cached afterward),
ingests your controls, and runs the agentic mapper. For NZISM with 1,216 controls
expect ~35 minutes and < $1 in Azure AI compute.

### 6. Review and approve

```powershell
ct review --config config\nzism-azure.json
```

The review command shows you what needs human sign-off. Approve by editing the mapping
store (`data\mappings\nzism.json`) or use the MCP server for a conversational workflow.

### 7. Deploy

```powershell
cd out\nzism-3.9
az deployment sub what-if --location australiaeast --template-file main.bicep --name nzism-3-9
az deployment sub create  --location australiaeast --template-file main.bicep --name nzism-3-9
```

Your initiative appears in Defender for Cloud → Regulatory compliance within 24 hours.

## Real-world results (NZISM v3.9)

| Metric | Value |
|--------|-------|
| Controls in scope (Restricted profile) | 1,216 of 1,422 total |
| Controls with Azure built-in coverage | 437 |
| Policy definitions in initiative | 528 |
| Policies covering multiple controls | 283 (55%) |
| LLM classifier | GPT-4o-mini via Azure AI Foundry |
| Run time | ~35 minutes (5 parallel threads) |
| Cost | < $1 in Azure AI compute |

## MCP server — conversational interface

Instead of working in the CLI and spreadsheets, you can interact with `ct` through
natural language using the MCP (Model Context Protocol) server. Connect it to
Claude Code, Claude Desktop, VS Code Copilot, or any MCP-capable client.

### Install and run

```powershell
pip install -e ".[mcp]"

# For Claude Code / VS Code (stdio transport)
ct-mcp

# For Claude Desktop / MCP Inspector (HTTP transport)
ct-mcp --transport http --port 8000
```

### Add to Claude Code

```bash
claude mcp add ct-mcp -- ct-mcp
```

### What you can do

| Tool | What it does |
|------|-------------|
| `run_pipeline` | Run the full pipeline end-to-end |
| `approve_controls` | Approve pending mappings → include in initiative |
| `reject_controls` | Reject mappings → exclude from initiative |
| `add_to_oos_register` | Add policies to the out-of-scope register |
| `get_mapping_details` | Look up a specific control's mapping |
| `search_controls` | Search by keyword, filter by status |

| Resource | What it exposes |
|----------|----------------|
| `ct://status` | Framework info, store stats, last run |
| `ct://pending-review` | Controls awaiting sign-off |
| `ct://oos-candidates` | Policies flagged for potential exclusion |
| `ct://oos-reconsidered` | Stale OOS entries needing review |
| `ct://bundle-summary` | Latest bundle stats |
| `ct://run-history` | Pipeline run history |

### Example conversations

- *"What's the current status of the NZISM mapping?"*
- *"Show me the pending review queue"*
- *"Approve controls 06.2.5.C.01 through 06.2.5.C.05"*
- *"Search for controls related to encryption"*
- *"Add policy abc123 to the OOS register — it requires In-Guest agent"*
- *"Run the pipeline and tell me what changed"*

---

## How it works

### Pipeline

```
ingest → catalogue → map → build → validate → distribute
```

| Stage | What it does |
|-------|--------------|
| **ingest** | Reads your framework CSV → normalised control catalogue |
| **catalogue** | Pulls Azure built-in policy definitions from ARM |
| **map** | Maps each control to relevant built-in policies (the core) |
| **build** | Generates policySet JSON + Bicep + deployment scripts |
| **validate** | Schema lint and structural checks |
| **distribute** | Writes versioned bundle to `out/` |

### The mapping engine (the novel part)

The mapper runs two stages per control:

1. **Retrieve** — shortlists the top-k most similar built-in policies using TF-IDF
   or embeddings (fast, no LLM call).
2. **Classify** — the LLM judges each candidate: is it relevant? should it be
   globally excluded? It returns a confidence score and rationale.

Results are saved to a **mapping store** that carries forward. On the next annual
update, only new or changed controls need fresh LLM calls — a typical delta is
30–90 controls, not 1,216.

### Choosing the classifier

Set `mapping.classifier` in your config:

| Classifier | LLM | Auth | Recommendation |
|---|---|---|---|
| `heuristic` | None (token overlap) | Free, offline | Testing only |
| `azure-openai` | GPT-4o / GPT-4o-mini | `az login` (keyless) | **Recommended** |
| `azure-inference` | Phi-4, Llama, Mistral | `az login` | Alternative |
| `foundry` | Claude via Foundry | `az login` | Alternative |
| `anthropic` | Claude (direct) | `ANTHROPIC_API_KEY` | Alternative |

**Recommended config (Azure AI Foundry):**
```json
"mapping": {
  "classifier":       "azure-openai",
  "model":            "${AZURE_OPENAI_DEPLOYMENT}",
  "foundry_base_url": "${AZURE_OPENAI_ENDPOINT}"
}
```

The tool auto-detects Foundry endpoints and handles Entra ID token refresh for long runs.
If Azure's content filter blocks a control, it falls back to the heuristic automatically.

### Classification profiles

Frameworks with classification levels (e.g. NZISM) can filter out-of-scope controls
at ingest so they never reach the LLM:

```json
"ingest": {
  "type": "nzism",
  "source": "data/source/NZISM-3.9.csv",
  "classification_profile": "restricted"
}
```

| Profile | Use case |
|---------|----------|
| `all` | Full catalogue (no filtering) |
| `restricted` | NZ Government Azure — excludes Secret/Top Secret |
| `protected` | AU Government (IRAP/ISM) — excludes above Protected |

### Automatic filters

These run before any LLM call to reduce noise and cost:

| Filter | What it excludes |
|--------|-----------------|
| **OOS register** | Policies you've explicitly excluded (human decisions) |
| **Preview** | `[Preview]:` policies — tracked, reconsidered when GA |
| **Manual-effect** | Policies with `effect: Manual` (can't evaluate automatically) |
| **Deprecated** | `[Deprecated]:` policies |
| **Classification** | Controls above your deployment's classification level |

### The OOS register

A two-tier list of policies excluded from mapping:

```json
"global_ignore": [
  "data/mappings/global-ignore.json",     // cross-framework (In-Guest, process controls)
  "data/mappings/nzism-ignore.json"       // framework-specific
]
```

The engine checks for **staleness** every run — if a previously-excluded Preview policy
goes GA, it flags it in `oos-reconsidered.json` for you to re-evaluate.

### Parallel classification

```json
"mapping": { "concurrency": 5 }
```

With 5 threads and GPT-4o-mini, a full NZISM run takes ~35 minutes instead of ~3 hours.
Raise to 8–10 if your rate limits allow.

### Initiative structure

- **Version** is independent of the standard — use semver (`1.0.0`) for policy changes
- **Controls become groups** in the policySet
- **Policy deduplication** — one definition with multiple `groupNames` (55%+ in practice)
- **Parameter overrides** — map policy parameters to initiative-level defaults

---

## Project structure

```
src/control_translator/
  cli.py              — CLI entrypoint (ct run, ct review, etc.)
  mcp_server.py       — MCP server (ct-mcp)
  pipeline.py         — pipeline orchestration
  config.py           — config loading + env var resolution
  ingest/             — framework CSV → normalised catalogue
  catalogue/          — Azure built-in policy pull + cache
  mapping/            — TF-IDF retrieval + LLM classification engine
  build/              — policySet + Bicep generation
  validate/           — structural lint
  distribute/         — output bundle writing
  models/             — OSCAL catalogue, mapping, bundle data models
  review/             — Excel export/import for authority sign-off
```

## Data vs code

The `data/` folder is **your working data** — never overwrite it from a code sync.

| Folder | What it is | Sync from repo? |
|--------|-----------|-----------------|
| `src/`, `config/` | Tool source code + config templates | Yes |
| `data/mappings/` | Your mapping decisions + OOS registers | **Never** |
| `data/source/` | Your framework CSV exports | **Never** |
| `data/cache/` | ARM policy cache (regenerates automatically) | **Never** |

## Secrets

Config files use `${VAR_NAME}` placeholders. Supply real values in `.env` (gitignored):

```
AZURE_OPENAI_ENDPOINT=https://<resource>.services.ai.azure.com/openai/v1
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
```

Copy `.env.example` → `.env` and fill in your values.

## Annual update cycle

1. Export new standard CSV → `data/source/`
2. Update `framework.version` + bump `build.initiative_version`
3. Run `ct run` — carry-forward means only new/changed controls need LLM calls
4. Review OOS reconsidered items (anything gone GA?)
5. Triage OOS candidates → promote confirmed ones to global-ignore
6. Approve pending mappings (authority sign-off)
7. Re-deploy

> Back up `data/mappings/` — it's your year-over-year institutional knowledge.

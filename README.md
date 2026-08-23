# control-translator

> CLI command: `ct` · MCP server: `ct-mcp` · Local API: `ct-api` · Local dashboard: `ct-web`

Development is governed through GitHub Issues and agent-prepared pull requests.
See the [contribution guide](CONTRIBUTING.md) and
[engineering harness](docs/engineering-harness.md). The implemented local
service contracts and operator procedures are documented in the
[Phase 2 service architecture](docs/service-architecture.md).

### Documentation index

| Document | Use it for |
| --- | --- |
| This README | Install, quick start, dashboard, API, and how the pipeline works |
| [End-to-end process guide](docs/process-guide.html) | CLI operator deep dive: full configuration reference, OOS triage, authority sign-off, deployment, annual update cycle, troubleshooting |
| [Service architecture](docs/service-architecture.md) | Service contracts, data locations, and recovery procedures |
| [Engineering harness](docs/engineering-harness.md) | How issues, agents, reviews, and CI gates fit together |
| [Future work](docs/future-work.md) | Ideas captured but not yet built |

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

## Local project workspaces

The CLI and MCP server remain config-driven. Their shared application service
derives a stable project ID from the resolved config-file path and uses that
project for durable run history. `ProjectStore` lets a local application isolate
projects beneath a separate data root. Pass the root explicitly, or set
`CONTROL_TRANSLATOR_DATA_ROOT`; the platform user-data location is used by default.

Each project has a UUID identifier and contains:

```text
<data-root>/<project-id>/
  project.json       # schema-versioned metadata
  source/            # uploaded standards
  config/            # project configuration
  mappings/          # mapping state and corrections
  guidance/          # local guidance
  runs/              # run metadata + sanitized event history (see PipelineService below)
  artifacts/         # generated outputs
```

Project files can contain sensitive compliance material. The store creates directories
and metadata with owner-only permissions where the platform supports them; access is
otherwise governed by the account and filesystem hosting the data root. Do not place
the root in a Git working tree or sync it to an unapproved service. Back it up using
your approved encrypted backup process. Deleting a project permanently removes only
its UUID-named project directory; back up anything that must be retained first.

`project.json` is atomically replaced on each metadata update and records its schema
version. A future unsupported version is rejected rather than silently changed.
Migration is explicit: back up the data root, install a release that supports the
required migration, then migrate a copy before replacing the original workspace.

The CLI and MCP service do not automatically copy config-referenced source,
mapping, OOS, catalogue, or output files into these directories. For full
project isolation, place those files in the workspace (or use the local API
source-ingestion routes below) and set every config path accordingly. See the
[service data and recovery guide](docs/service-architecture.md).

## Running it for real

This example uses NZISM, but the same flow applies to any framework with a CSV export.

### 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.10+** | Check: `py --version` |
| **Azure CLI (optional)** | Required for live ARM catalogue refresh or keyless Foundry authentication; not required for the bundled catalogue |
| **An LLM endpoint (optional)** | Azure AI Foundry or a direct provider improves mapping quality; the heuristic remains fully local |

### 2. Set up secrets

```powershell
copy .env.example .env
# Edit .env — fill in your Azure AI Foundry endpoint and deployment name
```

### 3. Install

```powershell
pip install -e ".[azure,openai]"
```

### 4. Place your framework CSV and authenticate when needed

```powershell
# Only required for a live ARM catalogue or keyless Azure model provider:
az login --tenant <your-tenant-id>
mkdir data\source -Force
copy "C:\path\to\NZISM-3.9.csv" data\source\NZISM-3.9.csv
```

### 5. Run

```powershell
ct run --config config\nzism-azure.json
```

The Azure configuration pulls the built-in policy catalogue from ARM and caches
it. To remove that Azure dependency, set `"catalogue": {"type": "bundled"}`.
The release includes a normalized production snapshot with real Azure policy
IDs, its authoritative Azure/azure-policy commit, generation date, checksum,
and Microsoft MIT licence. This affects catalogue freshness only; source files,
mapping stores, review decisions, generated artifacts, and validation remain
local. For NZISM with 1,216 controls expect ~35 minutes and < $1 in model
compute when a cloud classifier is selected.

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

Tools accept an optional config path. Resources currently resolve the default
`config/nzism-azure.json` from the server's working directory, so start one
server per repository/config context. Explicit remote project selection belongs
to the future HTTP API and is not part of the current MCP contract.

### Example conversations

- *"What's the current status of the NZISM mapping?"*
- *"Show me the pending review queue"*
- *"Approve controls 06.2.5.C.01 through 06.2.5.C.05"*
- *"Search for controls related to encryption"*
- *"Add policy abc123 to the OOS register — it requires In-Guest agent"*
- *"Run the pipeline and tell me what changed"*

---

## Local dashboard — supported offline local product

Download the verified `control-translator-dist` artifact from the latest CI run
or release, extract it, install its wheel with the dashboard extra, then run one
command:

```powershell
$wheel = (Get-ChildItem .\control_translator-*.whl).FullName
pip install "$wheel[web]"
ct-web
```

`ct-web` selects and binds an available `127.0.0.1` port before starting the
server, serves the packaged frontend from that same origin, and opens the
browser only for an interactive terminal. It never needs Node.js or network
access at runtime. Use `ct-web --no-browser` for headless operation, or add
`--print-token` only when deliberately connecting a browser tab manually. The
token is per-process, is not printed by default, and is never stored on disk.

The browser receives the token only in a URL fragment, removes that fragment
from its history before rendering, and retains the token in tab memory. It is
not sent to the server in a URL, saved in browser storage, or included in
access logs. Stop the launcher with `Ctrl+C`; Uvicorn drains and closes its
loopback listener cleanly.

Use `ct-web --data-root <directory>` to choose the durable project root. The
directory must be available and must not be a symlink. If the selected port is
busy or another launcher already owns it, `ct-web` exits with a deterministic
message; omit `--port` to select a different available port. An ownership-safe
lock in the canonical data root prevents two dashboard processes from mutating
the same projects. A lock left by a terminated process is reclaimed only after
the recorded process is confirmed dead. Existing data roots are
forward-compatible only when their project schema is supported:
back up the root before upgrading, test a copy, and retain the previous wheel
for rollback. Do not put the data root in this repository, and do not overwrite
user-owned `data/`, `.env`, `.mcp.json`, source exports, or mapping stores.

Back up the entire selected data root using approved encrypted storage after
active runs have stopped. To recover from an interrupted run, restart
`ct-web`, open the project, inspect the durable history, and start a new run
only after the prior record has reconciled to a terminal state. Troubleshooting
messages deliberately omit file paths and tokens; verify the installation with
`ct-web --no-browser`, then check that the displayed loopback URL loads.

## Local API — secure loopback transport for the web UI

`ct-api` exposes the same project/run/review/artifact operations as the CLI and
MCP server over a small, versioned HTTP API used by the local browser dashboard.
It is designed to be safe on a single developer
workstation, not for remote or multi-user hosting:

- **Loopback only.** Binds to `127.0.0.1` by default and rejects any request
  whose `Host` header does not name an approved loopback host (defeats DNS
  rebinding). CORS defaults to an empty origin allow-list, so no web page can
  read a response even if it can trigger a request.
- **Ephemeral local session token.** A fresh, high-entropy token is generated
  each time the process starts and printed to stderr; it is never written to
  disk. Every state-changing or otherwise sensitive route requires it in the
  `X-CT-Session-Token` header — only `GET /api/v1/health` is unauthenticated.
- **Typed, project-scoped contracts.** Requests/responses are pydantic models.
  Every project-scoped route derives the project id from the supplied config
  path and rejects the call (`403`) if it does not match the id in the URL —
  the sole guard against cross-project access.
- **Sanitized errors.** Domain errors map to a small, stable, allow-listed set
  of HTTP responses; no filesystem path, raw exception text, or credential
  ever appears in a response body. Request validation failures (`422`) use the
  same sanitized envelope and never echo the submitted value back to the
  caller.
- **Config-bound project identity.** A project's id is always the
  deterministic id derived from its config path (see `project_id_for_config`);
  `POST /projects` binds to that same id at creation time so every later
  config-backed route (open, run, review, …) can enforce the identity check
  above without ever producing an unusable project.

### Install and run

```powershell
pip install -e ".[api]"
ct-api                      # binds 127.0.0.1:8756; prints the session token to stderr
```

### Routes (all under `/api/v1`)

| Route | Auth | Purpose |
|-------|------|---------|
| `GET /health` | none | Liveness check |
| `GET /projects`, `POST /projects` | token | List / create local projects |
| `POST /projects/{id}/open`, `DELETE /projects/{id}` | token | Open (status) / delete a project |
| `POST /projects/{id}/runs`, `GET .../runs`, `GET .../runs/{run_id}` | token | Start / list / inspect pipeline runs |
| `GET /projects/{id}/runs/{run_id}/events?after_sequence=N`, `POST .../cancel` | token | Reconnect-safe run event history and cooperative cancellation |
| `GET /projects/{id}/review`, `POST .../review/approve`, `POST .../review/reject` | token | Searchable/filterable/paginated review + mapping decisions |
| `GET /projects/{id}/guidance`, `POST .../guidance`, `POST .../guidance/delete` | token | Project-local guidance with source/provenance for future runs |
| `POST /projects/{id}/oos`, `POST .../oos/reconsider` | token | Add or reconsider policies in the out-of-scope register |
| `POST /projects/{id}/sources/upload`, `POST .../sources/url` | token | Ingest CSV/XLSX uploads or HTTPS URL sources into project-local normalized CSV |
| `GET /projects/{id}/mappings/{control_id}`, `GET .../mappings/search` | token | Mapping lookups |
| `GET /projects/{id}/artifacts`, `GET .../artifacts/inventory`, `GET .../artifacts/{name}/preview`, `GET .../artifacts/{name}/download` | token | Bundle summary plus allow-listed, bounded previews and attachment downloads |

The events response returns events ordered by sequence, omits duplicate
sequences, reports `dropped_event_count`, and includes `latest_sequence` plus a
single `terminal_state` once the run is durable. Polling clients should persist
the last rendered sequence and resume with `after_sequence=<last sequence>`;
history may start after zero when older events have been dropped from the
bounded store.

Remote/multi-user hosting remains out of scope for this API foundation.

## Frontend project dashboard

The `frontend/` workspace contains the local-only React + TypeScript dashboard
for standards setup and live pipeline monitoring. It is a Vite application that
talks only to the authenticated loopback API above; it does not duplicate
project, run, source-ingestion, or mapping domain logic.

```powershell
cd frontend
npm ci
npm run dev       # serves the dashboard on 127.0.0.1
npm run build     # production bundle with local static assets only
```

Start `ct-api` separately, then paste the printed session token into the
dashboard bootstrap form. Vite development and preview servers proxy
same-origin `/api` requests to the loopback API, preserving its empty CORS
allow-list. Explicit API origins are restricted to HTTP loopback addresses.
The token is sent in the `X-CT-Session-Token` header and is kept only in tab
memory; it is not placed in URLs, logs, localStorage, or other persistent
browser storage. Theme selection defaults to the operating system preference,
and only an explicit light/dark choice is persisted. The guided Home page
introduces the workflow, while Create, Run, Review, and Outputs keep each stage
focused. System-wide portal preferences, including the compact theme switch,
live under Config in the top-right menu.

Dashboard workflow:

1. Create or open a project with the config path that owns it. The status panel
   shows framework metadata, mapping counts, artifact availability, and the
   selected run summary without exposing local filesystem paths.
2. Ingest a standard from a CSV/XLSX upload or HTTPS URL. Validation feedback
   reports safe metadata (filename, rows, columns, size) and stores normalized
   content under the project workspace.
3. Start a full run or review refresh. The dashboard disables duplicate starts,
   source ingestion, config-path edits, and deletion while a non-terminal run
   owns the project lock; server-side lock conflicts are still mapped to safe
   messages.
4. Monitor the run timeline. The UI polls bounded event history using
   `after_sequence`, renders events in stable order, highlights warnings, and
   shows exactly one terminal summary for succeeded, failed, or cancelled runs.
5. Cancel when needed and keep polling until the durable `cancelled` state is
   visible. Refreshing or reopening a project reloads run history and resumes
   monitoring any non-terminal run so interrupted/restarted sessions are
   explicit rather than silently lost.
6. Review mappings through the dashboard table. Search, filter, paginate through
   all results, inspect confidence/rationale/policy references, select multiple
   rows, and approve or reject only after the confirmation dialog. Conflict
   feedback reports updated, already-current, and missing controls. Review data
   loads on first entry and remains cached until the project, configuration, or
   successful run changes; use the explicit refresh control when newer data is
   expected.
7. Manage local guidance with explicit source and provenance. Guidance is stored
   in the project workspace and feeds future mapping runs; it does not mutate the
   current mapping store or generated artifacts until the pipeline is run again.
8. Promote OOS candidates or reconsider OOS entries through the same locked
   mutation service used by mapping changes. Bulk/destructive actions are
   disabled while a run owns the project lock and still require confirmation in
   the browser.
9. Browse generated artifact inventory by allow-listed names only. Supported
   text/JSON files use bounded previews; the inventory loads when Outputs is
   first opened, and downloads are authenticated attachment
   responses for generated Azure Policy files and never expose arbitrary
   filesystem paths or archive contents.

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

### Pipeline events

Every run emits typed events so the CLI, MCP server, and a future web API can
follow progress without parsing console text:

```python
from control_translator.pipeline import run_pipeline

events = []
result = run_pipeline(config, event_sink=events.append)
```

| Event type | When | Key summary fields |
|---|---|---|
| `run.started` | once, before ingest | `framework`, `version`, `engine`, `classifier` |
| `stage.started` | entering a stage | stage-specific inputs |
| `stage.progress` | mapping checkpoints | `mapped`, `total` |
| `stage.completed` | stage succeeded | stage-specific counts |
| `stage.failed` | stage raised | `error_type` |
| `run.warning` | validation lint, OOS staleness, interrupt | `kind`, `count`/`index` |
| `run.completed` | successful run | `duration_s`, `approved`, `pending`, `lint_errors` |
| `run.failed` | run aborted | `error_type` |

Contract rules:

- `stage` is one of `ingest`, `catalogue`, `map`, `build`, `validate`, `distribute`.
- Each event carries `schema_version`, `run_id`, a gapless `sequence`, and a UTC `timestamp`.
- `summary` holds scalar fields only — control prose, policy lists, and raw
  exception text are never included, and secret-looking keys are redacted.
- Omitting `event_sink` installs the console renderer, which reproduces the
  existing stderr progress output exactly.

### Pipeline run service

`control_translator.runs.PipelineService` wraps `run_pipeline` with a durable,
project-scoped run lifecycle for callers that don't want to touch pipeline
internals or scrape JSONL files out of `out/`:

```python
from control_translator.projects import ProjectStore
from control_translator.runs import PipelineService, RunState

store = ProjectStore()
project = store.create("Example")
service = PipelineService(store)

handle = service.start(project.id, config)          # returns a RunHandle
record = service.wait(project.id, handle.run_id)     # blocks for tests/CLIs
assert record.state is RunState.SUCCEEDED

service.list(project.id)                             # every run, oldest first
service.events(project.id, handle.run_id)             # bounded, sanitized history
service.cancel(project.id, handle.run_id)             # cooperative cancellation
```

Guarantees:

- **Run identity and state** — every run gets a stable id and moves through an
  explicit `queued → running → {succeeded, failed, cancelled}` state machine;
  invalid transitions raise `InvalidRunStateTransitionError`. Active tracking
  (threads, cancellation flags) is always scoped to `(project_id, run_id)`, so
  a run id can never be cancelled or observed from the wrong project.
- **Durable, bounded history** — run metadata and up to `max_events` (default
  500) sanitized events are written atomically under the project's own
  `runs/<run-id>/` directory, so a new `PipelineService` instance (for example
  after a process restart) can `list()` and `events()` a run it never started.
  Once the bound is exceeded, the oldest events are dropped and
  `dropped_event_count` records how many — kept in sync on the run record once
  the run reaches a terminal state. The event history file is itself
  schema-versioned; malformed or unsupported-schema history raises a typed
  error rather than silently returning partial data.
- **No concurrent mutation** — starting a run acquires a per-project lock file
  (`runs/.mutation-lock.json`) recording the holder's PID and an ownership
  token; a second `start()` for the same project raises
  `ProjectRunConflictError` while a run is active. If the holder process is
  gone (a crash), the lock is recognised as stale and reclaimed automatically —
  it is never assumed stale just because it is old, and a holder (or a
  reclaimer) only ever deletes the exact lock content it just re-verified, so a
  lock replaced by another process in between is never removed out from under
  its new owner. Mapping, OOS, and configured-guidance updates also acquire a
  canonical resource lock beside each file they rewrite, so separate project
  configs and pipeline runs that share a mutable file cannot lose concurrent
  updates.
- **Crash recovery** — a run left `queued`/`running` by a killed process or an
  unclean restart is never reported as perpetually in-flight: the next time it
  is observed via `get()`/`list()` in a process not actively tracking it, it is
  reconciled to `failed` with clearly-labelled, sanitized diagnostics.
- **Honest, cooperative cancellation** — `cancel()` sets a flag that is only
  checked at safe stage boundaries (the start of each of the six stages, and
  right after a mapping checkpoint has saved the mapping store). A single
  in-flight LLM classification call is **never** interrupted; the run finishes
  that call before honouring cancellation at the next boundary. Cancellation
  produces a `stage.cancelled`/`run.cancelled` event, never contradictory
  `stage.failed`/`run.failed` history alongside a `cancelled` run record.
- **Faithful failures** — the original exception type and a sanitized message
  (using the same secret/URL redaction as pipeline events) are retained on the
  run record; failures are never turned into a success-shaped result. The
  worker's lock release and active-run tracking cleanup happen only after the
  terminal run record has been durably saved, and starting a run cleans up the
  lock/record if the worker thread itself fails to launch.
- **Mapping carry-forward preserved** — the service does not alter the config
  or mapping store contract; a run started through `PipelineService` behaves
  identically to calling `run_pipeline` directly.

### CLI/MCP compatibility during shared-service migration

`ct` and `ct-mcp` now call shared application services for project/config
resolution, pipeline execution, and mapping/OOS mutations. Existing commands,
`--config` usage, output shape, and exit-code behavior are preserved for
backward compatibility.

- Relative `--config` paths still resolve from the current working directory.
- Absolute config paths are still supported unchanged.
- Mapping mutation rules (approve/reject/OOS writes) are implemented once in
  the shared service layer and reused by both adapters.



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
| `heuristic` | None (token overlap) | Free, offline | Offline baseline |
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
  api/                — local, loopback-only HTTP API (ct-api)
  pipeline.py         — pipeline orchestration
  events.py           — structured pipeline events + console renderer
  projects/           — local, isolated project workspace storage
  runs/               — durable pipeline run service (identity, history, locking, cancellation)
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

# control-translator

> Working name — rename freely. CLI command: `ct`.

An **agentic interpretation engine** that translates any published security standard
into deployable **cloud compliance controls**, grounded in an **OSCAL** control
catalogue and emitted as native cloud policy artefacts.

The reference framework is **NZISM**; the reference cloud provider is **Azure** (built-in
policy only). Both are plugins — the engine core is framework- and provider-agnostic.

## Why this exists

Translating a national standard (e.g. NZISM) into an Azure Policy *Regulatory
Compliance* initiative is an annual hand-built exercise with a fragile publishing
pathway. This project turns that into a repeatable pipeline whose only human-judgement
step is reviewing the **delta** each revision — and whose output is a **custom**
initiative any organisation can self-deploy into their own tenant, with no dependency on
a central built-in-onboarding pathway.

## Pipeline

```
ingest -> catalogue -> map -> build -> validate -> distribute
```

| Stage | What it does | Agnostic? |
|-------|--------------|-----------|
| **ingest** | published standard (CSV/PDF/URL) → OSCAL catalogue | framework plugin |
| **catalogue** | pull provider built-in policy definitions | provider plugin |
| **map** ★ | control → built-in policy mapping (the core) | engine core |
| **build** | mapping → native initiative (policySet + Bicep + IaC) | provider plugin |
| **validate** | schema lint + (future) sandbox deploy | provider plugin |
| **distribute** | publish artefact bundle | adapter |

★ The mapping engine is the genuinely novel component. It runs retrieval shortlisting
(TF-IDF) followed by LLM classification in parallel threads, with a durable carry-forward
store so each annual revision only processes the delta.

## Module status

| Module | Status |
|--------|--------|
| `models/oscal.py` | Implemented — OSCAL catalogue, group, control; serialise/deserialise |
| `models/mapping.py` | Implemented — MappingSet, ControlMapping, Decision enum, durable store |
| `models/bundle.py` | Implemented — ArtifactBundle with file registry |
| `ingest/fixture.py` | Implemented — loads OSCAL catalogue from a JSON fixture (offline tests) |
| `ingest/nzism.py` | **Implemented** — NZISM CSV export → OSCAL (23 chapters, 1,422 controls); normalises CIDs, cleans prose, classification-profile filtering |
| `catalogue/offline.py` | Implemented — loads built-in policies from a cached JSON file |
| `catalogue/azure.py` | **Implemented** — live ARM pull of built-in policy definitions; caches result; filters `[Deprecated]`, `[Preview]`, Manual-effect policies by default |
| `mapping/keyword.py` | Implemented — deterministic TF-IDF baseline (free, no LLM) |
| `mapping/retrieval.py` | Implemented — TF-IDF shortlist; returns top-k candidates for the LLM |
| `mapping/agentic.py` | **Implemented** — retrieval shortlist → LLM classify with OOS candidate detection; runs in parallel (ThreadPoolExecutor) |
| `mapping/classifier.py` | **Implemented** — five classifiers: `heuristic` (offline), `anthropic` (Claude, first-party), `foundry` (Claude via Azure AI Foundry), `azure-openai` (GPT-4o/mini via Azure OpenAI or Foundry endpoint), `azure-inference` (Phi-4, Llama, Mistral via serverless endpoint). All include OOS candidate detection, content-filter fallback, and existing OOS list as context. |
| `mapping/engine.py` | **Implemented** — carry-forward, preview auto-filter, global-ignore filter (normalised GUID matching), Manual-effect filter, parallel classification (configurable thread count), OOS staleness detection, progress output |
| `mapping/store.py` | **Implemented** — durable mapping store, two-tier OOS register (single path or list of paths), staleness check (preview→GA, deprecated), normalised GUID comparison |
| `build/azure.py` | **Implemented** — policySet + assignment + Bicep + deploy.sh; semver independent of standard version; NZISM control-group conventions; policy deduplication (one definition, multiple groupNames); parameter overrides; OOS + OOS-candidates + OOS-reconsidered bundle files |
| `validate/azure.py` | Lint implemented (structural checks, group reference validation); sandbox deploy — TODO |
| `distribute/local.py` | Implemented — writes versioned bundle to `out/` |
| `distribute/community_policy.py` | TODO stub |
| `distribute/gov_repo.py` | TODO stub |
| `scripts/inspect.py` | **Implemented** — post-run bundle inspection (stats, OOS candidates, reconsidered entries, mapping store summary) |

## Real-world results (NZISM v3.9, Restricted profile)

First full automated run against the published NZISM v3.9:

| Metric | Value |
|--------|-------|
| Controls in scope (Restricted profile) | 1,216 of 1,422 total |
| Controls with Azure built-in coverage | 437 |
| Policy definitions in initiative | 528 |
| Policies covering multiple controls | 283 (55%) |
| Preview policies auto-filtered | 181 |
| Out-of-scope (Secret/Top Secret) auto-excluded | 206 |
| LLM classifier | GPT-4o-mini via Azure AI Foundry |
| Run time | ~35 minutes (5 parallel threads) |
| Cost | < $1 in Azure AI compute |

The initiative deploys as a custom Regulatory Compliance standard in
**Defender for Cloud → Regulatory compliance**, identical in appearance to a
Microsoft-published built-in standard.

## Scope

- **In scope now:** built-in policy only, Azure, NZISM / any CSV-exportable standard.
- **Future:** custom policy generation for controls with no built-in coverage;
  additional cloud providers; additional frameworks. See `docs/future-work.md`.

## Prerequisites

> Commands below assume **Windows (PowerShell)** as the default. macOS / Linux / WSL
> equivalents are noted where they differ.

| Requirement | When needed | Notes |
|-------------|-------------|-------|
| **Python 3.10+** | Always | Check: `py --version`. 3.10–3.13 all work. |
| **Git** | Always | Check: `git --version`. [git-scm.com](https://git-scm.com/download/win) |
| **Azure CLI (`az`)** | Always | Built-in policy pull + Foundry keyless auth. [Install guide](https://learn.microsoft.com/cli/azure/install-azure-cli-windows) |
| **LLM — Azure AI Foundry** | Agentic mapping | Deploy `gpt-4o-mini` in a Foundry project. See Section "Choosing the classifier". |
| **LLM — Anthropic API key** | Agentic mapping | From [console.anthropic.com](https://console.anthropic.com) |
| **LLM — Heuristic** | Testing only | No setup — runs fully offline. Lower quality. |

### Installing Python (Windows)

```powershell
# uv (recommended — no admin rights needed)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.12

# or winget
winget install Python.Python.3.12
```

> **macOS:** `brew install python@3.12`  ·  **Ubuntu/WSL:** `sudo apt install -y python3.12 python3.12-venv`

## Data vs code — important separation

The `data/` folder is **your working data**, not part of the tool. Never overwrite it
from a code sync — it contains your curated mapping decisions and OOS register.

| Folder | What it is | Sync from repo? |
|--------|-----------|-----------------|
| `src/` | Tool source code | Yes |
| `config/` | Config templates (contain `${VAR}` placeholders, not real secrets) | Yes |
| `data/mappings/` | Your mapping store + OOS registers — institutional knowledge | **Never** |
| `data/source/` | Your framework CSV exports | **Never** |
| `data/cache/` | ARM policy cache (regenerates on first run) | **Never** |

```powershell
# Correct — sync only code:
robocopy <source>\src    C:\repos\control-translator\src    /MIR /NFL /NDL
robocopy <source>\config C:\repos\control-translator\config /MIR /NFL /NDL
```

## Secrets — environment variables and .env

Config files use `${VAR_NAME}` placeholders for secrets. Supply real values via:

1. A `.env` file in the project root (gitignored, loaded automatically):
   ```
   AZURE_OPENAI_ENDPOINT=https://<resource>.services.ai.azure.com/openai/v1
   AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
   ANTHROPIC_API_KEY=sk-ant-...
   ```
2. Shell environment variables set before running `ct`.

Copy `.env.example` to `.env` and fill in your values. The `.env` file is gitignored and
must never be committed.

## Quick start (offline demo, Windows)

```powershell
git clone https://github.com/BevanSin/control-translator.git
cd control-translator

python -m venv .venv
.venv\Scripts\Activate.ps1          # (.venv) prefix confirms activation
pip install -e ".[azure]"

ct run --config config\sample.json            # keyword baseline, no cloud
ct run --config config\sample-agentic.json    # agentic mapper, offline heuristic
dir out\sample-1.0
```

If `Activate.ps1` is blocked: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

The sample configs use placeholder fixtures — no cloud access required.

## Running it for real (NZISM → Azure → LLM)

### 1. Set up secrets
```powershell
copy .env.example .env
# edit .env — fill in AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT
```

### 2. Install with LLM support
```powershell
pip install -e ".[azure,openai]"    # azure = ARM pull, openai = GPT-4o-mini via Foundry
```

### 3. Authenticate and place your CSV
```powershell
az login --tenant <your-tenant-id>
mkdir data\source -Force
copy "C:\path\to\NZISM-ISM Document-V.-3.9-April-2025.csv" data\source\NZISM-3.9.csv
```

### 4. Run
```powershell
ct run --config config\nzism-azure.json
```

First run pulls the built-in policy catalogue from ARM (cached afterward), ingests
1,216 in-scope NZISM controls (Restricted profile), and runs the agentic mapper with
5 parallel LLM threads. Expect ~35 minutes and < $1 in compute cost.

### 5. Inspect results
```powershell
python scripts\inspect.py              # auto-discovers the latest bundle
```

### 6. Review and approve (authority sign-off gate)
```powershell
ct review --config config\nzism-azure.json
```

Edit `data\mappings\nzism.json` — change `"decision": "review"` to `"decision": "include"`
(or `"ignore"`). Re-run `ct run`. Approved controls appear in `policySet.json`.

### 7. Deploy
```powershell
cd out\nzism-3.9
az deployment sub what-if --location australiaeast --template-file main.bicep --name nzism-3-9
az deployment sub create  --location australiaeast --template-file main.bicep --name nzism-3-9
```

Deploys an audit-only Regulatory Compliance initiative. Appears in
**Defender for Cloud → Regulatory compliance** within 24 hours.

## Choosing the classifier

Set `mapping.classifier` in your config. All LLM classifiers include OOS candidate
detection, content-filter fallback, and the existing OOS register as context.

| `classifier` | LLM | Auth / cost | Install |
|---|---|---|---|
| `heuristic` | None (token overlap) | Free, no signup, offline | — |
| `azure-openai` | GPT-4o / GPT-4o-mini via Azure OpenAI or Foundry | `az login` (keyless) or `AZURE_OPENAI_API_KEY`. Billed to Azure sub. **Recommended.** | `pip install "openai>=1.50"` |
| `azure-inference` | Phi-4, Llama, Mistral via Foundry serverless | `az login` or `AZURE_INFERENCE_API_KEY` | `pip install azure-ai-inference` |
| `foundry` | Claude via Azure AI Foundry (Anthropic endpoint) | `az login` or `ANTHROPIC_FOUNDRY_API_KEY` | `pip install "anthropic>=0.39"` |
| `anthropic` | Claude, first-party API | `ANTHROPIC_API_KEY` | `pip install "anthropic>=0.39"` |

**Azure AI Foundry (recommended — bills to your Azure sub):**
```json
"mapping": {
  "classifier":       "azure-openai",
  "model":            "${AZURE_OPENAI_DEPLOYMENT}",
  "foundry_base_url": "${AZURE_OPENAI_ENDPOINT}"
}
```
The tool auto-detects `services.ai.azure.com` endpoints and uses the appropriate client
with Entra ID token refresh for long runs.

**Content filter fallback:** if Azure's content safety filter blocks a control (common
for security standards containing language about attacks, penetration testing, etc.),
the classifier logs a warning and automatically falls back to the heuristic for that
control. The run continues uninterrupted.

## Classification profiles (NZISM)

NZISM controls carry a `Classifications` field. For a public-cloud deployment approved
only up to a certain classification level, out-of-scope controls can be filtered at
ingest so they never reach the LLM:

```json
"ingest": {
  "type": "nzism",
  "source": "data/source/NZISM-3.9.csv",
  "classification_profile": "restricted"
}
```

| Profile | Includes | Excludes | Use case |
|---------|---------|---------|----------|
| `all` | Everything | Nothing | Full catalogue reference |
| `restricted` | All Classifications, Restricted/Sensitive, Unclassified | Secret, Top Secret, Confidential-only | **NZ Government Azure (default)** |
| `protected` | As above + Protected | Above Protected | AU Government (IRAP/ISM) |

For NZISM v3.9 with `restricted` profile: 1,216 controls (206 S/TS excluded).

## Parallel classification

The agentic mapper runs LLM calls in parallel using a thread pool, configurable via
`mapping.concurrency` (default: 5). With `concurrency: 5` and GPT-4o-mini, a full
NZISM run takes ~35 minutes instead of ~3 hours sequential.

```json
"mapping": {
  "concurrency": 5
}
```

Raise to 8–10 if your Foundry rate limits allow. The engine uses
`ThreadPoolExecutor` — the LLM client is thread-safe; the TF-IDF retriever is
read-only after `.prepare()`; shared state (results dict, OOS accumulator) is
protected by `threading.Lock`.

## The agentic mapper — how it works

`mapping.engine: agentic` runs a two-stage core per control:

1. **Retrieve** — TF-IDF shortlists the top-k most similar built-in policies
   (fast, recall-oriented, no LLM call).
2. **Classify** — the LLM judges each shortlisted candidate:
   - `relevant` + `confidence` — does this policy cover the control?
   - `oos_candidate` + `oos_reason` — should this policy be globally excluded for
     structural reasons (requires In-Guest agent, organisation-specific values, etc.)?

OOS candidates are collected across all controls and emitted as `oos-candidates.json`
in the bundle for human review. The existing OOS register is supplied as context so the
LLM applies consistent patterns.

## Automatic filters (applied before any LLM call)

| Filter | What it excludes | Configurable? |
|--------|-----------------|---------------|
| **Global OOS register** | Policies in `mapping.global_ignore` (any format GUID/ARM path) | `global_ignore` — single path or list |
| **Preview auto-filter** | `[Preview]:` policies — tracked, emitted in `out-of-scope.json` | `mapping.preview_filter: false` to disable |
| **Manual-effect filter** | Azure policies with `effect: Manual` (process/attestation controls — can't evaluate automatically) | `catalogue.exclude_manual: false` to disable |
| **Classification profile** | Out-of-scope classification controls (e.g. Secret/Top Secret for a Restricted deployment) | `ingest.classification_profile` |
| **Deprecated filter** | `[Deprecated]:` policies | `catalogue.exclude_deprecated: false` to disable |

## OOS register — two-tier pattern

`mapping.global_ignore` accepts a **single path or a list of paths** (union of all):

```json
"global_ignore": [
  "data/mappings/global-ignore.json",    ← cross-framework: process controls, In-Guest, etc.
  "data/mappings/nzism-ignore.json"      ← NZISM-specific; IRAP would use irap-ignore.json
]
```

Each file is published in `out-of-scope.json` with a `source` field distinguishing
human decisions from auto-preview entries. Schema:

```json
[
  { "policy_id": "<guid-or-full-arm-id>", "display_name": "...",
    "reason": "Requires Guest Configuration agent pre-deployed.",
    "oos_date": "2025-09-03" }
]
```

### OOS staleness detection

On every run the engine cross-references the OOS register against the current
catalogue. Flagged entries appear in `oos-reconsidered.json`:

- **Preview → GA:** policy was `[Preview]:` when excluded but is now generally available.
- **Policy removed:** policy no longer exists in the built-in catalogue (deprecated/removed).

`ct review` surfaces these as the highest-priority item.

## Initiative structure & build options

- **Version independent of the standard.** `build.initiative_version` is a semver
  (`1.0.0`) for policy changes. The standard document version goes in the description
  prefix (`NZISM v3.9. ...`). Bump the semver freely.
- **Controls become groups.** Each control → `policyDefinitionGroups` entry with
  `name: New_Zealand_ISM_06.2.5.C.01`, zero-padded chapter category, control text.
- **Policy deduplication.** The same built-in policy mapping to multiple controls
  appears once with all control group names in `groupNames`. In practice 55%+ of
  policies cover multiple controls.
- **Required defaults.** `build.parameter_overrides` maps a policy GUID → parameter →
  initiative-level parameter + default. See `data/parameters/nzism.example.json`.

## Distribution targets

| Target | Behaviour | Status |
|--------|-----------|--------|
| `local` | Write versioned bundle to `out/` | Implemented |
| `community-policy` | PR-ready folder for Azure/Community-Policy repo | TODO |
| `gov-repo` | Scaffold + push to an owner-hosted gov repo (canada-ca pattern) | TODO |

## Annual update cycle

1. Export new standard CSV → `data/source/`.
2. Update `framework.version` and bump `build.initiative_version`.
3. **Do not delete the mapping store.** Carry-forward means only new/changed controls
   need fresh LLM calls — a typical annual delta is 30–90 controls, not 1,216.
4. Run `ct review` → check **OOS RECONSIDERED** first (anything gone GA?).
5. Triage new OOS candidates → promote confirmed ones to `global-ignore.json`.
6. Work the pending review queue (authority sign-off).
7. Re-deploy with the new initiative version.

> Back up `data/mappings/` in source control — it is your year-over-year institutional
> knowledge. Losing it means re-running all LLM calls from scratch.

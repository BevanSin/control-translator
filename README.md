# control-translator

> CLI command: `ct`.

An **agentic interpretation engine** that translates any published security standard
into deployable **cloud compliance controls**, grounded in an **OSCAL** control
catalogue and emitted as native cloud policy artefacts.

The first framework is **NZISM**; the first cloud provider is **Azure** (built-in
policy only). Both are plugins — the engine core is framework- and provider-agnostic.

## Why this exists

Translating a national standard (e.g. NZISM) into an Azure Policy *Regulatory
Compliance* initiative is, today, an annual hand-built spreadsheet exercise with a
fragile publishing pathway. This project turns that into a repeatable pipeline whose
only human-judgement step is reviewing the **delta** each revision — and whose output
is a **custom** initiative any organisation can self-deploy into their own tenant,
with no dependency on a central built-in-onboarding pathway.

## Pipeline

```
ingest -> catalogue -> map* -> build -> validate -> distribute
```

| Stage | What it does | Provider/framework agnostic? |
|-------|--------------|------------------------------|
| **ingest** | published standard (doc/CSV) -> OSCAL catalogue | framework plugin |
| **catalogue** | pull provider built-in policy definitions | provider plugin |
| **map** ★ | control -> built-in policy mapping (the core) | engine core |
| **build** | mapping -> native control set (policySet + assignment + IaC) | provider plugin |
| **validate** | schema lint + sandbox deploy (audit-only) | provider plugin |
| **distribute** | publish artefact bundle | adapter (local / community / gov-repo) |

★ The mapping engine is the only genuinely novel component. Everything else is
ingest, templating, or git plumbing.

## Module status

| Module | Status |
|--------|--------|
| `models/oscal.py` | implemented (minimal OSCAL subset) |
| `models/mapping.py`, `models/bundle.py` | implemented |
| `ingest/fixture.py` | implemented (loads an OSCAL catalogue from disk) |
| `ingest/nzism.py` | implemented — parses the NZISM CSV export (23 chapters, 1,422 controls) |
| `catalogue/offline.py` | implemented (loads built-ins from a cached file) |
| `catalogue/azure.py` | implemented — live ARM pull of built-in policy definitions (+ cache) |
| `mapping/keyword.py` | implemented (deterministic baseline mapper) |
| `mapping/retrieval.py` | implemented (TF-IDF shortlist; swap for embeddings later) |
| `mapping/agentic.py` | implemented — retrieval shortlist → LLM classify |
| `mapping/classifier.py` | `AnthropicClassifier` implemented (Claude, real); `HeuristicClassifier` offline stand-in |
| `mapping/store.py` | implemented (durable mapping + global-ignore) |
| `build/azure.py` | implemented (emits policySet + assignment + Bicep) |
| `validate/azure.py` | **TODO** — schema lint + sandbox deploy |
| `distribute/local.py` | implemented |
| `distribute/community_policy.py`, `distribute/gov_repo.py` | **TODO** stubs |

## Scope

- **In scope now:** built-in policy only, Azure, NZISM reference framework.
- **Future:** custom policy generation for controls with no built-in coverage;
  additional cloud providers; additional frameworks. See `docs/future-work.md` for
  captured ideas (custom per-agency control overlays, Must/Should initiative split,
  URL-or-CSV control input).

## Prerequisites

> Commands below assume **Windows (PowerShell)** as the default. macOS / Linux / WSL
> equivalents are noted where they differ.

| Requirement | When you need it | Notes |
|-------------|------------------|-------|
| **Python 3.10+** | always | 3.10, 3.11, 3.12, 3.13 all work — check with `py --version` |
| **Azure CLI (`az`), logged in** | real run only | for the live ARM pull of built-in policies — [install](https://learn.microsoft.com/cli/azure/install-azure-cli) |
| **`ANTHROPIC_API_KEY`** | real run only (agentic classifier) | from [console.anthropic.com](https://console.anthropic.com) |

If `py --version` already shows 3.10+, skip ahead to Quick start.

### Installing Python 3.10+ (Windows)

Easiest is **uv** ([astral.sh/uv](https://docs.astral.sh/uv/)) — it manages both the
Python version and the virtualenv, no admin rights needed:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.12
```

Or install Python natively with **winget**:

```powershell
winget install Python.Python.3.12
```

Product page: [python.org/downloads/windows](https://www.python.org/downloads/windows/).

> **macOS:** `brew install python@3.12` · **Ubuntu/WSL:** `sudo apt install -y python3.12 python3.12-venv`
> · uv installer (macOS/Linux): `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Data vs code — important separation

The `data/` folder is **your working data**, not part of the tool:

| Folder | What it is | Sync from repo? |
|--------|-----------|-----------------|
| `src/` | Tool source code | ✅ Yes |
| `config/` | Config templates | ✅ Yes (but don't overwrite your named config) |
| `data/mappings/` | Your mapping store + OOS registers | ❌ Never — these are yours |
| `data/source/` | Your framework CSV exports | ❌ Never |
| `data/cache/` | ARM policy cache (regenerates on first run) | ❌ Never |

When pulling a newer version of the tool, **only sync `src/` and `config/`**:
```powershell
robocopy <source>\src    C:\repos\control-translator\src    /MIR /NFL /NDL
robocopy <source>\config C:\repos\control-translator\config /MIR /NFL /NDL
```

Overwriting `data\mappings\nzism.json` loses all curated decisions and forces a full re-run. Overwriting `data\mappings\global-ignore.json` replaces your OOS register.

## Quick start (offline demo, Windows)

```powershell
# from the control-translator folder
python -m venv .venv
.venv\Scripts\Activate.ps1          # prompt should now show (.venv)
pip install -e .

ct run --config config\sample.json            # keyword baseline mapper
ct run --config config\sample-agentic.json    # agentic mapper, offline heuristic classifier
dir out\sample-1.0
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, or use cmd:
`.venv\Scripts\activate.bat`.

> **uv shortcut (no activation needed):** `uv venv; uv pip install -e .; uv run ct run --config config\sample.json`
> **macOS / Linux:** `python3 -m venv .venv && source .venv/bin/activate`, then the same `ct` commands with `/` paths.

The sample configs use **placeholder fixtures** (clearly fake controls and policy
GUIDs) so the full pipeline runs with no cloud access.

## Running it for real (NZISM → Azure → Claude)

```powershell
.venv\Scripts\Activate.ps1
pip install -e ".[yaml,agentic,azure]"      # yaml=config, agentic=Claude, azure=ARM pull

az login                                     # ARM token for the built-in policy pull
$env:ANTHROPIC_API_KEY = "sk-ant-..."        # agentic classifier (this session only)

mkdir data\source -Force
copy "C:\path\to\NZISM-ISM Document-V.-3.9-April-2025.csv" data\source\NZISM-3.9.csv

ct run --config config\nzism-3.9.yaml
```

First run pulls ~1,000+ built-in definitions (cached to `data\cache\` afterward),
ingests the 1,422 NZISM controls, and runs the agentic mapper.

### Choosing the classifier (incl. using your Azure sub instead of an Anthropic key)

Set `mapping.classifier` in the config:

| `classifier` | LLM | Auth / cost |
|--------------|-----|-------------|
| `heuristic` | none (token overlap) | **free, no signup** — runs fully offline. Lower quality; good for a first proof or baseline. |
| `anthropic` | Claude (first-party) | `ANTHROPIC_API_KEY` |
| `foundry` | Claude via **Azure AI Foundry** | **billed to your Azure subscription** — no separate Anthropic account |

**No-LLM run (free):**
```powershell
# set classifier: heuristic in config\nzism-3.9.yaml, then:
ct run --config config\nzism-3.9.yaml
```

**Foundry (Claude on Azure):** deploy a Claude model in your Foundry project, then set
in the config:
```yaml
mapping:
  classifier: foundry
  model: claude-opus-4-7        # your Foundry *deployment name*
  foundry_base_url: "https://<resource>.services.ai.azure.com/anthropic"
```
Auth is keyless via Microsoft Entra ID by default (`az login` + `pip install azure-identity`),
or set an API key:
```powershell
$env:ANTHROPIC_FOUNDRY_API_KEY = "<your-foundry-key>"
```
Note Foundry uses your **deployment name** as the model id, and structured-output /
prompt-cache features may differ on the preview — the classifier falls back automatically
if the service rejects them.

**Two things to know first:**
1. The review gate is on by default (`auto_approve: false`), so a first run produces an
   *empty* initiative on purpose — new proposals go to **review**. Set `auto_approve: true`
   for an auto-cut, or follow the review step below.
2. Agentic mode makes ~1,422 Claude calls (one per control). For a free, instant sanity
   check on the real data first, set `engine: keyword` in the config.

### Review and approve (the authority sign-off gate)

```powershell
ct review --config config\nzism-3.9.yaml     # lists pending proposals + rationales
```

Edit `data\mappings\nzism.json` — change accepted decisions from `"review"` to
`"include"` (this is the durable mapping that carries forward next year), then re-run
`ct run`. Approved controls now build into the initiative.

### Deploy the initiative (audit-only)

The build writes `out\nzism-3.9\main.bicep`. Deploy it from that folder:

```powershell
cd out\nzism-3.9
az deployment sub create --location australiaeast --template-file main.bicep --name nzism-3-9
# management-group scope instead: az deployment mg create --management-group-id <MG_ID> --template-file main.bicep --name nzism-3-9
```

It deploys the custom **Regulatory Compliance** initiative plus an audit-mode
assignment, so it surfaces in Defender for Cloud → Regulatory compliance.

## The agentic mapper

`mapping.engine: agentic` runs the two-stage core:

1. **Retrieve** — `mapping/retrieval.py` shortlists the top-k built-in policies for
   each control (TF-IDF baseline; swap in embeddings later).
2. **Classify** — `mapping/classifier.py` judges each shortlisted policy:
   - `classifier: anthropic` — **the real path.** Calls Claude (`claude-opus-4-7` by
     default, adaptive thinking, cached instructions, structured JSON output) to decide
     include/ignore with a calibrated confidence and a rationale for the authority sign-off.
     Needs `pip install control-translator[agentic]` and `ANTHROPIC_API_KEY`.
   - `classifier: heuristic` — offline token-overlap stand-in so the pipeline runs and
     tests without network. Not a substitute for the LLM.

The mapper only *proposes*; the engine applies the auto-approve / human-review gate.

## Initiative structure & build options

The Azure builder mirrors the published NZISM built-in initiative:

- **Version is independent of the standard.** `build.initiative_version` is a semver
  (e.g. `1.0.0`) for policy/deprecation changes; the framework document version
  (`framework.version`, e.g. NZISM 3.9) is written into the **description** prefix
  (`NZISM v3.9. ...`). Bump the semver freely without changing the standard alignment.
- **Controls become groups.** Each control → a `policyDefinitionGroups` entry:
  `name` = `<build.group_prefix><control-id>` (e.g. `New_Zealand_ISM_06.2.5.C.01`),
  `category` = zero-padded chapter (`06. Information security monitoring`),
  `description` = the control text. Set `include_metadata_id: true` (+ `metadata_id_template`)
  only for the built-in submission path.
- **Required default parameters.** `build.parameter_overrides` points at a JSON file
  mapping a policy GUID → its parameter → an initiative-level parameter + default. The
  builder surfaces those as top-level `parameters` and wires the policy refs via
  `[parameters('...')]`. See `data/parameters/nzism.example.json` (real RSA-key-size /
  TLS examples pulled from v3.8).

### Out-of-scope register

`mapping.global_ignore` is the durable OOS register. It accepts a single file path **or
a list of paths** — the engine takes the union, enabling a two-tier structure:

```json
"global_ignore": [
    "data/mappings/global-ignore.json",   // cross-framework: process controls, In-Guest, etc.
    "data/mappings/nzism-ignore.json"     // per-standard: NZISM-only; IRAP would use irap-ignore.json
]
```

Each file filters the mapper (those policies are never proposed) and is published as
`out-of-scope.json` in the bundle for transparency. Schema (see `data/mappings/global-ignore.example.json`):

```json
[
  { "policy_id": "<guid>", "display_name": "...",
    "reason": "Requires the Guest Configuration / In-Guest policy + agent.",
    "oos_date": "2025-09-03" }
]
```

Typical reasons: too hard to implement broadly (e.g. TLS 1.3), requires In-Guest /
Guest Configuration, or needs a customer-specific list (e.g. SOE image names).

## Distribution targets

| Target | Behaviour | Modelled on |
|--------|-----------|-------------|
| `local` | write versioned bundle to `out/` | — |
| `community-policy` | PR-ready folder for the Azure Community Policy repo | Azure/Community-Policy |
| `gov-repo` | scaffold + push to an owner-hosted gov repo | canada-ca/cloud-guardrails |

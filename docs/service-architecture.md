# Phase 2 service architecture and operations

Phase 2 is the local service foundation used by the CLI and MCP adapters. It is
implemented and tested offline. It does not expose an HTTP API, accept uploads,
or select projects on behalf of a remote user; those remain work for a later
FastAPI phase.

> **Phase 3 update:** the local, loopback-only HTTP API described in this
> document as future work now exists as `control_translator.api` (`ct-api`).
> It calls the same `ControlTranslatorService`/`PipelineService` contracts
> described below without duplicating pipeline logic. See the
> [README "Local API" section](../README.md#local-api--secure-loopback-transport-for-a-future-web-ui)
> for routes and `tests/test_api.py` for its contract/security tests. Remote or
> multi-user hosting remains out of scope.

## Architecture

```text
CLI / MCP
    │
    ▼
ControlTranslatorService     config resolution, summaries, review mutations
    │
    ├── ProjectStore         UUID-isolated local workspaces
    ├── PipelineService      durable lifecycle, cancellation, run history
    └── run_pipeline         ingest → catalogue → map → build → validate → distribute
             │
             └── typed PipelineEvent stream
```

`ControlTranslatorService` derives a stable project UUID from the resolved
configuration-file path and creates that project on first use. The same config
path therefore finds the same durable history after a process restart. Moving
the config path selects a new project identity. Configuration still controls
all source, mapping, OOS, catalogue, and output paths: the service does not copy
those files into the workspace. Applications requiring complete isolation must
set those paths beneath the selected project's directories.

CLI and MCP are adapters over the same application service. `ct run` and
`ct review` replay the persisted typed events through the console renderer.
MCP tools return JSON and MCP resources expose current state without requiring
clients to parse terminal output.

## Stable service contracts

These Python contracts are the boundary a future FastAPI adapter should call.
HTTP handlers should translate transport input and typed errors rather than
calling pipeline internals.

### Projects

`ProjectStore` provides `create`, `load`, `list`, `update`, `delete`, and
`resolve_path`. Project IDs are canonical UUID strings. Absolute child paths,
path traversal, symlink project roots, malformed metadata, and unsupported
schema versions are rejected with typed `ProjectStoreError` subclasses.

### Runs

`PipelineService` provides:

- `start(project_id, config, do_distribute=True) -> RunHandle`
- `get(project_id, run_id) -> RunRecord`
- `list(project_id) -> list[RunRecord]`
- `events(project_id, run_id) -> list[dict]`
- `cancel(project_id, run_id) -> None`
- `wait(project_id, run_id, timeout=None) -> RunRecord`

A run follows `queued → running → succeeded | failed | cancelled`. Terminal
states never transition again. Run IDs are opaque 32-character lowercase hex
tokens and are meaningful only with their project ID. Metadata and bounded
event history are durable and schema-versioned.

Cancellation is cooperative. It is observed at stage boundaries and after a
mapping checkpoint; an in-flight classifier request completes before the next
check. Cancellation records `cancelled` and cancellation events, never a
success or contradictory failure events. A non-terminal record discovered
after a restart is reconciled to `failed` with `RunInterrupted`.

### Events

Each event contains:

- `schema_version`, `run_id`, gapless `sequence`, and UTC `timestamp`
- a stable type (`run.*`, `stage.*`, or `run.warning`)
- an optional pipeline `stage`
- a short display `message`
- a scalar-only `summary`

The default history limit is 500. Oldest events are discarded when the limit
is reached and `RunRecord.dropped_event_count` reports the loss. Clients must
not assume history starts at sequence zero when that count is non-zero.

### Application operations

`ControlTranslatorService` exposes `run`, `review`, `approve_controls`,
`reject_controls`, `add_to_oos_register`, `mapping_details`,
`search_controls`, `status`, `pending_review`, `bundle_json_resource`,
`bundle_summary`, and `run_history`.

Application errors have stable codes for adapter mapping:

| Code | Meaning |
|---|---|
| `invalid_identifier` | Invalid or unknown request identifier |
| `invalid_project_or_config` | Configuration/project cannot be resolved |
| `pipeline_failed` | Run or review refresh reached a failed/cancelled state |
| `pipeline_in_progress` | A project mutation conflicts with an active run |

MCP tools accept an optional config path. MCP resources currently use
`config/nzism-azure.json` relative to the server working directory. A future
HTTP adapter must require an explicit authenticated project context rather than
reuse this working-directory default.

## Local data layout and boundaries

```text
<data-root>/<project-id>/
  project.json
  source/
  config/
  mappings/
  guidance/
  runs/
    .mutation-lock.json
    <run-id>/
      run.json
      events.json
  artifacts/
```

The data root comes from an explicit `ProjectStore` argument,
`CONTROL_TRANSLATOR_DATA_ROOT`, or the platform user-data location. Directories
and metadata use owner-only permissions where supported. Atomic replacement is
used for project metadata, run state, and event history.

Only `project.json` and `runs/` are populated automatically by the service.
Callers own source placement and configuration. Repository `data/`, `.env`,
uploaded standards, mapping stores, and generated `out/` content remain user
data and must not be committed or overwritten during code updates.

## Operator workflows

### Run and review

1. Back up the mapping and OOS stores.
2. Verify every mutable config path belongs to the intended project.
3. Run `ct run --config <path>`; a non-zero exit means validation warnings.
4. Run `ct review --config <path>` or use MCP review resources.
5. Approve/reject mappings or update OOS decisions.
6. Run the pipeline again so generated artifacts reflect the decisions.
7. Inspect the bundle before deployment.

Only one run or review mutation may hold a project's mutation lock. Wait for
the active run to finish or cancel it; do not delete the lock manually while
its process is alive.

### Cancellation and recovery

Request cancellation through `PipelineService.cancel`. Continue polling
`get`/`list` until the terminal record is durable. A killed process can leave a
lock and non-terminal record; the next service instance reclaims a lock whose
owner process is gone and marks the interrupted run failed. Inspect the mapping
checkpoint and artifacts, then start a new run. Never report an interrupted run
as successful.

### Backup, restore, and migration

1. Stop active runs and verify there is no live mutation lock.
2. Back up the complete data root with approved encrypted storage. Preserve
   permissions and include mapping/OOS state, sources, run history, and artifacts.
3. Test restoration to a different root by constructing `ProjectStore` with
   that root and loading each project.
4. Before an upgrade, retain the original backup and migrate a copy.
5. If a schema is unsupported, stop. Install a release with an explicit
   migration path; never edit schema-version fields to bypass validation.
6. Replace the original only after projects, mappings, history, and bundles
   have been verified from the migrated copy.

Artifact directories are replaceable pipeline output; mapping and OOS stores
are the authoritative human decisions and receive the highest backup priority.

## Security review boundaries

- **Path traversal:** validated project IDs and `resolve_path` prevent absolute
  paths and `..` escapes. Config paths are trusted operator input in Phase 2;
  do not expose them directly as unauthenticated HTTP input. `ct-api` accepts a
  config path only from an authenticated caller and additionally restricts
  artifact resource names to a fixed allow-list rather than an arbitrary
  filename.
- **Cross-project access:** run storage and active cancellation are keyed by
  `(project_id, run_id)`. A run from one project cannot be read or cancelled
  through another project. `ct-api` additionally verifies that a request's
  URL `project_id` matches the id derived from its supplied `config_path`
  before performing any read or mutation, rejecting mismatches with `403`.
- **Sensitive diagnostics:** events use scalar summaries and sanitize
  secret-looking keys, URLs, and exception messages. MCP maps internal errors
  to generic messages. `ct-api` maps every domain exception to a small,
  allow-listed HTTP response and never returns `str(exc)`. Sensitive source
  prose and credentials must never be added to logs.
- **Deletion:** `ProjectStore.delete` accepts only a validated existing project
  UUID and removes that one workspace. Deletion is permanent; authorize it at
  the future transport layer and back up retained records first.
- **Local API network exposure:** `ct-api` binds to loopback only by default,
  validates the `Host` header on every request (defeating DNS rebinding), and
  applies an empty CORS origin allow-list. Every sensitive/state-changing
  route requires a fresh, high-entropy, per-process session token that is
  never persisted to disk.

The offline acceptance suite in `tests/test_service_acceptance.py` verifies the
composed lifecycle, explicit failure/cancellation, redaction, cross-project
denial, path traversal rejection, and deletion containment. `tests/test_api.py`
covers the additional API-layer security properties (host/origin/token
enforcement, cross-project rejection, traversal, oversized bodies, and
cancellation authorization).

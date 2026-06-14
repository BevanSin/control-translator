# Future work

Captured ideas, not yet built. Each fits the existing stage architecture
(ingest → catalogue → map → build → validate → distribute).

## 1. Custom per-agency controls (front-end overlay)

Let an agency add their **own** control(s) on top of NZISM at the front end, then run
the same retrieve → classify flow to find built-in policy that enforces each custom
control.

- The mapping engine is already framework-agnostic, so a custom control flows through
  unchanged once it's in the OSCAL catalogue.
- Implementation sketch: author custom controls (id, title, prose, compliance) in the
  front end → emit a small OSCAL group → **merge** it with the NZISM catalogue before
  the mapping stage (e.g. a `MergeIngestor` that overlays catalogues). Keep custom
  control ids in a reserved namespace (e.g. `AGENCY.x.y`) so they never collide with
  NZISM ids and are easy to filter in the built initiative.
- The durable mapping store already keys by control id, so custom-control mappings
  carry forward across NZISM revisions for free.

## 2. Must / Should grouping (two initiative sets)

The ingestor already captures `props["compliance"]` (Must / Should / Must Not /
Should Not). Consider emitting **two sets** instead of one:

- a **Must** initiative (candidate for `Deny`/enforce where the policy supports it), and
- a **Should** initiative (audit-only).

This is a build-layer split keyed on `props["compliance"]` — partition approved
mappings into two `policySetDefinition`s (or two `policyDefinitionGroups` buckets) so
agencies can enforce the mandatory controls and merely report on the recommended ones.

## 3. Richer progress output

The current progress feed prints one line per control to stderr using a simple ASCII
progress bar. Improvements to consider:

- **Live dashboard** — use the `rich` library (`pip install rich`) for a proper multi-line
  dashboard showing: progress bar, current control, running approved/pending/OOS counts,
  estimated time remaining, token usage if available from the API response.
- **Async / parallel classification** — the agentic mapper currently calls the LLM
  sequentially. Batching controls (e.g. 4–8 at once within one API call) or running
  parallel async calls (bounded concurrency) could reduce wall-clock time from ~15 min
  to ~2–4 min for a full NZISM run.
- **Resume on interrupt** — the mapping store already carries forward decided controls,
  so `Ctrl+C` and re-running is effectively free. A cleaner interrupt handler that saves
  the partial store before exiting would make this seamless.

## 4. Flexible front-end control input (URL or CSV)

The front end should accept the control list either by:

- **reaching out to a website** (fetch the framework / control catalogue published
  online), or
- **CSV upload** (the current NZISM export path).

Implementation: add ingestors behind the existing `FrameworkIngestor` interface — a
`UrlIngestor` (fetch + parse) alongside the CSV path — both normalising to the same
OSCAL catalogue. Selection stays config-driven (`ingest.type` / `ingest.source`).

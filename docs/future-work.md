# Future work

Captured ideas, not yet built. Each fits the existing pipeline architecture
(ingest → catalogue → map → build → validate → distribute).

## 1. Custom per-agency controls (front-end overlay)

Let an agency add their **own** controls on top of a standard (NZISM, IRAP/ISM, etc.)
at the front end, then run the same retrieve → classify flow to find Azure built-in
policies that enforce each custom control.

- The mapping engine is already framework-agnostic, so a custom control flows through
  unchanged once it's in the catalogue.
- Implementation sketch: author custom controls (id, title, prose, compliance) in the
  front end → emit a small catalogue group → **merge** it with the framework catalogue
  before the mapping stage (e.g. a `MergeIngestor` that overlays catalogues). Keep custom
  control ids in a reserved namespace (e.g. `AGENCY.x.y`) so they never collide with
  framework ids and are easy to filter in the built initiative.
- The durable mapping store already keys by control id, so custom-control mappings
  carry forward across framework revisions for free.

## 2. Must / Should grouping (two initiative sets)

The ingestor already captures `props["compliance"]` (Must / Should / Must Not /
Should Not). Consider emitting **two Azure initiative sets** instead of one:

- a **Must** initiative (candidate for `Deny`/enforce where the policy supports it), and
- a **Should** initiative (audit-only).

This is a build-layer split keyed on `props["compliance"]` — partition approved
mappings into two `policySetDefinition`s (or two `policyDefinitionGroups` buckets) so
agencies can enforce the mandatory controls and merely report on the recommended ones.

## 3. Flexible front-end control input (URL or CSV)

The front end should accept the control list either by:

- **reaching out to a website** (fetch the framework / control catalogue published
  online), or
- **CSV upload** (the current path for NZISM, IRAP/ISM exports).

Implementation: add ingestors behind the existing `FrameworkIngestor` interface — a
`UrlIngestor` (fetch + parse) alongside the CSV path — both normalising to the same
catalogue. Selection stays config-driven (`ingest.type` / `ingest.source`).

## 4. Custom Azure Policy generation

For controls where no Azure built-in policy exists, generate a custom policy definition
using the LLM:

- Identify controls with no mapping (decision: `ignore` + no policies)
- Generate a custom policy rule targeting the relevant Azure resource types
- Emit as part of the bundle alongside the built-in initiative
- Requires a separate review/approval gate for generated policy logic

## 5. Web dashboard for review workflow

A lightweight FastAPI + HTML frontend for the review queue, OOS triage, and run status.
Complements the MCP server with a visual interface for non-technical reviewers
(e.g. authority sign-off by someone who doesn't use Claude or VS Code).

## 6. Additional framework ingestors

Extend beyond CSV to support other compliance frameworks natively:

- **ISM/IRAP** (Australian Signals Directorate) — similar CSV structure to NZISM
- **ISO 27001/27002** — control catalogue from published standard
- **CIS Benchmarks** — already structured as controls with Azure mapping hints
- **NIST 800-53** — well-structured, OSCAL-native

Each is an ingestor plugin producing the same normalised catalogue.

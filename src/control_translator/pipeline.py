"""Pipeline orchestration: ingest -> catalogue -> map -> build -> validate -> distribute."""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .ingest import get_ingestor
from .catalogue import get_catalogue
from .mapping import (MappingEngine, MappingStore, load_global_ignore,
                      load_oos_records, check_oos_staleness, get_mapper)
from .mapping.corrections import load_corrections
from .build import get_builder
from .validate import AzureValidator
from .distribute import get_adapter
from .models import Catalog, MappingSet, ArtifactBundle


def _banner(msg: str) -> None:
    print(f"\n▶  {msg}", file=sys.stderr, flush=True)


def _done(msg: str) -> None:
    print(f"   ✓  {msg}", file=sys.stderr, flush=True)


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def _append_run_log(log_path: str, entry: dict) -> None:
    """Append one JSON line to the run log (one entry per run, history preserved)."""
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


@dataclass
class PipelineResult:
    catalog: Catalog
    mapping: MappingSet
    bundle: ArtifactBundle | None
    lint_errors: list[str]
    published_to: str | None
    elapsed_seconds: float = 0.0


def run_pipeline(config: dict, *, do_distribute: bool = True) -> PipelineResult:
    start_time = time.monotonic()
    start_wall = datetime.now(tz=timezone.utc)
    fw = config["framework"]

    _banner(f"Ingest  — {fw.get('display_name', fw['id'])} v{fw['version']}"
            f"   [started {start_wall.astimezone().strftime('%H:%M:%S')}]")
    icfg = config["ingest"]
    catalog = get_ingestor(icfg["type"]).ingest(
        icfg["source"], framework_id=fw["id"], version=fw["version"],
        classification_profile=icfg.get("classification_profile", "all"))
    n_controls = sum(1 for _ in catalog.controls())
    _done(f"{n_controls} controls across {len(catalog.groups)} chapters")

    ccfg = config["catalogue"]
    cache_path = ccfg.get("source")
    from_cache = cache_path and os.path.exists(cache_path)
    _banner(f"Catalogue — {'loading from cache' if from_cache else 'pulling from ARM (first run)'}")
    cat_obj  = get_catalogue(ccfg["type"], cache_path, ccfg)
    policies = cat_obj.builtins()
    # show a breakdown of what was filtered (best-effort — some filters only apply on live pull)
    filters_note = []
    if hasattr(cat_obj, "exclude_non_auditable") and cat_obj.exclude_non_auditable:
        filters_note.append("Modify/DINE-only excluded")
    if hasattr(cat_obj, "exclude_manual") and cat_obj.exclude_manual:
        filters_note.append("Manual excluded")
    _done(f"{len(policies)} built-in policies available"
          + (" (cached)" if from_cache else " — cache written for next run")
          + (f"  [{', '.join(filters_note)}]" if filters_note and not from_cache else ""))

    mcfg = config["mapping"]
    oos = load_oos_records(mcfg.get("global_ignore"))
    store = MappingStore(mcfg["store"])
    existing = store.load(fw["id"], fw["version"])
    n_existing = sum(1 for m in existing.mappings.values()
                     if m.decision.value in ("include", "ignore"))

    _banner(f"Map  —  engine: {mcfg.get('engine','keyword')}  "
            f"|  classifier: {mcfg.get('classifier','—')}  "
            f"|  {n_existing} carry-forward  |  "
            f"{n_controls - n_existing} to classify")

    corrections = load_corrections(mcfg.get("corrections"))
    engine = MappingEngine(
        get_mapper(mcfg.get("engine", "keyword"), mcfg),
        global_ignore=load_global_ignore(mcfg.get("global_ignore")),
        auto_approve=mcfg.get("auto_approve", False),
        confidence_threshold=mcfg.get("confidence_threshold", 0.75),
        oos_context=oos,
        corrections=corrections,
        preview_filter=mcfg.get("preview_filter", True),
        exclude_patterns=mcfg.get("exclude_patterns", []),
        verbose=True,
        concurrency=mcfg.get("concurrency", 5),
    )
    try:
        mapping = engine.run(
            catalog, policies, existing,
            checkpoint_fn=lambda r: store.save(r),
        )
    except KeyboardInterrupt:
        print("\n\n   ⚠  interrupted — saving progress to mapping store...",
              file=sys.stderr)
        raise
    finally:
        try:
            store.save(mapping)  # type: ignore[possibly-undefined]
        except Exception:
            pass

    _banner("Build")
    bcfg = dict(config["build"])
    ov_path = bcfg.get("parameter_overrides")
    if ov_path and os.path.exists(ov_path):
        with open(ov_path, encoding="utf-8") as fh:
            bcfg["parameter_overrides"] = json.load(fh)
    oos_reconsidered = check_oos_staleness(oos, policies)
    bundle = get_builder(bcfg["type"]).build(
        catalog, mapping, framework=fw, options=bcfg,
        oos=oos, oos_suggestions=mapping.oos_suggestions or None,
        oos_reconsidered=oos_reconsidered or None)

    approved  = mapping.approved()
    pending   = mapping.pending_review()
    defs      = json.loads(bundle.files.get("policySet.json", "{}")).get(
                    "properties", {}).get("policyDefinitions", [])
    multi     = sum(1 for d in defs if len(d.get("groupNames", [])) > 1)
    _done(f"{len(approved)} controls with coverage  |  "
          f"{len(defs)} policy definitions  |  "
          f"{multi} covering multiple controls")

    lint_errors = AzureValidator().lint(bundle)
    if lint_errors:
        print(f"   ⚠  {len(lint_errors)} lint warning(s)", file=sys.stderr)

    published_to = None
    if do_distribute:
        _banner("Distribute")
        adapter = get_adapter(config["distribute"]["type"])
        published_to = adapter.publish(
            bundle, out_dir=config.get("out_dir", "out"),
            target=config["distribute"].get("target"))
        _done(f"published → {published_to}")

    if oos_reconsidered:
        print(f"\n   ⚠  {len(oos_reconsidered)} OOS entries need review "
              f"(see out/oos-reconsidered.json)", file=sys.stderr)

    # ── timing ────────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start_time
    finish_wall = datetime.now(tz=timezone.utc)
    print(f"\n   ⏱  Completed in {_fmt_elapsed(elapsed)}"
          f"  (started {start_wall.astimezone().strftime('%H:%M:%S')}"
          f", finished {finish_wall.astimezone().strftime('%H:%M:%S')})",
          file=sys.stderr)

    # ── run log ───────────────────────────────────────────────────────────────
    out_dir  = config.get("out_dir", "out")
    slug     = f"{fw['id']}-{fw['version']}"
    log_path = os.path.join(out_dir, slug, "run-log.jsonl")
    n_ignore = sum(1 for m in mapping.mappings.values()
                   if m.decision.value == "ignore")
    n_carry  = sum(1 for m in mapping.mappings.values()
                   if m.source != "auto" and m.decision.value in ("include","ignore"))
    log_entry = {
        "run_at":             start_wall.isoformat(),
        "duration_s":         round(elapsed, 1),
        "framework":          fw["id"],
        "version":            fw["version"],
        "initiative_version": bcfg.get("initiative_version", ""),
        "engine":             mcfg.get("engine", "keyword"),
        "classifier":         mcfg.get("classifier", "—"),
        "retrieval":          mcfg.get("retrieval", "tfidf"),
        "concurrency":        mcfg.get("concurrency", 5),
        "classification_profile": icfg.get("classification_profile", "all"),
        "controls_total":     n_controls,
        "carry_forward":      n_carry,
        "approved":           len(approved),
        "pending":            len(pending),
        "ignored":            n_ignore,
        "coverage_pct":       round(len(approved) / n_controls * 100, 1) if n_controls else 0,
        "policy_definitions": len(defs),
        "multi_control_policies": multi,
        "oos_candidates":     len(mapping.oos_suggestions or []),
        "preview_excluded":   len(mapping.preview_excluded or []),
        "pattern_excluded":   len(mapping.pattern_excluded or []),
        "oos_reconsidered":   len(oos_reconsidered or []),
        "lint_errors":        len(lint_errors),
    }
    _append_run_log(log_path, log_entry)

    print(file=sys.stderr)
    return PipelineResult(catalog, mapping, bundle, lint_errors, published_to, elapsed)

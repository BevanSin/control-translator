"""Pipeline orchestration: ingest -> catalogue -> map -> build -> validate -> distribute."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .ingest import get_ingestor
from .catalogue import get_catalogue
from .mapping import (MappingEngine, MappingStore, load_global_ignore,
                      load_oos_records, check_oos_staleness, get_mapper)
from .mapping.corrections import load_corrections
from .build import get_builder
from .validate import AzureValidator
from .distribute import get_adapter
from .events import (ConsoleEventRenderer, EventEmitter, EventSink, Stage,
                     WarningKind)
from .models import Catalog, MappingSet, ArtifactBundle


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


class PipelineCancelledError(Exception):
    """Raised by a ``cancel_check`` callback when cooperative cancellation is requested.

    ``run_pipeline`` only checks for cancellation at safe stage boundaries (and at
    mapping checkpoints, right after the mapping store has been saved). Work already
    in flight inside a stage — most notably a single in-progress LLM classification
    call — is never interrupted; cancellation takes effect before the *next* boundary
    is entered, not mid-call.
    """


@dataclass
class PipelineResult:
    catalog: Catalog
    mapping: MappingSet
    bundle: ArtifactBundle | None
    lint_errors: list[str]
    published_to: str | None
    elapsed_seconds: float = 0.0
    run_id: str = ""




@contextmanager
def _stage(emitter: EventEmitter, stage: Stage, message: str = "", **summary):
    """Emit a stage start event, and a stage failure/cancellation event if the stage raises."""
    emitter.stage_started(stage, message, **summary)
    try:
        yield
    except PipelineCancelledError:
        # Cancellation is not a failure: keep the event history's own terminal
        # signal consistent with the run's CANCELLED state, not stage/run.failed.
        emitter.stage_cancelled(stage)
        emitter.run_cancelled(stage=stage)
        raise
    except BaseException as exc:
        emitter.stage_failed(stage, exc)
        emitter.run_failed(exc, stage=stage)
        raise


def run_pipeline(config: dict, *, do_distribute: bool = True,
                 event_sink: EventSink | None = None,
                 run_id: str | None = None,
                 cancel_check: Callable[[], None] | None = None) -> PipelineResult:
    """Run the pipeline, emitting structured progress and result events.

    When no ``event_sink`` is supplied the console renderer is used, which
    reproduces the human-readable progress the CLI has always written to stderr.

    ``cancel_check``, when supplied, is invoked at safe stage boundaries — before
    each of the six stages starts, and after each mapping checkpoint has saved the
    mapping store. It should raise (typically ``PipelineCancelledError``) to abort
    the run. Cancellation is cooperative: a stage already running to completion
    (in particular one in-flight LLM classification call) is never interrupted.
    """
    emitter = EventEmitter(
        ConsoleEventRenderer() if event_sink is None else event_sink, run_id)

    def _check_cancel() -> None:
        if cancel_check is not None:
            cancel_check()

    start_time = time.monotonic()
    start_wall = datetime.now(tz=timezone.utc)
    fw = config["framework"]
    icfg = config["ingest"]
    mcfg = config["mapping"]

    emitter.run_started(framework=fw["id"], version=fw["version"],
                        started_at=start_wall.isoformat(),
                        distribute=do_distribute,
                        engine=mcfg.get("engine", "keyword"),
                        classifier=mcfg.get("classifier", "—"),
                        classification_profile=icfg.get("classification_profile", "all"))

    with _stage(emitter, Stage.INGEST,
                f"Ingest  — {fw.get('display_name', fw['id'])} v{fw['version']}"
                f"   [started {start_wall.astimezone().strftime('%H:%M:%S')}]",
                framework=fw["id"], version=fw["version"],
                classification_profile=icfg.get("classification_profile", "all")):
        _check_cancel()
        catalog = get_ingestor(icfg["type"]).ingest(
            icfg["source"], framework_id=fw["id"], version=fw["version"],
            classification_profile=icfg.get("classification_profile", "all"))
        n_controls = sum(1 for _ in catalog.controls())
        emitter.stage_completed(
            Stage.INGEST,
            f"{n_controls} controls across {len(catalog.groups)} chapters",
            controls=n_controls, groups=len(catalog.groups))

    ccfg = config["catalogue"]
    cache_path = ccfg.get("source")
    catalogue_kind = ccfg["type"]
    from_cache = bool(
        catalogue_kind == "azure"
        and cache_path
        and os.path.exists(cache_path)
        and not ccfg.get("refresh", False)
    )
    if catalogue_kind == "bundled":
        catalogue_start = "Catalogue — loading bundled Azure snapshot"
    elif catalogue_kind == "offline":
        catalogue_start = "Catalogue — loading offline file"
    else:
        catalogue_start = f"Catalogue — {'loading from cache' if from_cache else 'pulling from ARM (first run)'}"
    with _stage(emitter, Stage.CATALOGUE,
                catalogue_start, from_cache=from_cache, catalogue_source=catalogue_kind):
        _check_cancel()
        cat_obj  = get_catalogue(ccfg["type"], cache_path, ccfg)
        policies = cat_obj.builtins()
        # show a breakdown of what was filtered (best-effort — some filters only apply on live pull)
        filters_note = []
        if hasattr(cat_obj, "exclude_non_auditable") and cat_obj.exclude_non_auditable:
            filters_note.append("Modify/DINE-only excluded")
        if hasattr(cat_obj, "exclude_manual") and cat_obj.exclude_manual:
            filters_note.append("Manual excluded")
        metadata = getattr(cat_obj, "metadata", None)
        if metadata is not None:
            source_note = f" — bundled snapshot {metadata.generated_at}"
        elif catalogue_kind == "offline":
            source_note = " (offline file)"
        else:
            source_note = " (cached)" if from_cache else " — cache written for next run"
        emitter.stage_completed(
            Stage.CATALOGUE,
            f"{len(policies)} built-in policies available"
            + source_note
            + (f"  [{', '.join(filters_note)}]" if filters_note and not from_cache else ""),
            policies=len(policies), from_cache=from_cache,
            catalogue_source=catalogue_kind,
            snapshot_generated_at=metadata.generated_at if metadata is not None else None)

    oos = load_oos_records(mcfg.get("global_ignore"))
    store = MappingStore(mcfg["store"])
    existing = store.load(fw["id"], fw["version"])
    n_existing = sum(1 for m in existing.mappings.values()
                     if m.decision.value in ("include", "ignore"))

    with _stage(emitter, Stage.MAP,
                f"Map  —  engine: {mcfg.get('engine','keyword')}  "
                f"|  classifier: {mcfg.get('classifier','—')}  "
                f"|  {n_existing} carry-forward  |  "
                f"{n_controls - n_existing} to classify",
                engine=mcfg.get("engine", "keyword"),
                classifier=mcfg.get("classifier", "—"),
                carry_forward=n_existing, to_classify=n_controls - n_existing):
        _check_cancel()
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

        def _checkpoint(result: MappingSet) -> None:
            store.save(result)
            _check_cancel()
            emitter.stage_progress(Stage.MAP, mapped=len(result.mappings),
                                   total=n_controls)

        try:
            mapping = engine.run(catalog, policies, existing,
                                 checkpoint_fn=_checkpoint)
        except KeyboardInterrupt:
            emitter.warning(WarningKind.INTERRUPTED, stage=Stage.MAP,
                            message="interrupted — saving progress to mapping store")
            raise
        finally:
            try:
                store.save(mapping)  # type: ignore[possibly-undefined]
            except Exception:
                pass

        approved = mapping.approved()
        pending  = mapping.pending_review()
        emitter.stage_completed(
            Stage.MAP,
            "",
            approved=len(approved), pending=len(pending),
            oos_candidates=len(mapping.oos_suggestions or []),
            preview_excluded=len(mapping.preview_excluded or []),
            pattern_excluded=len(mapping.pattern_excluded or []))

    with _stage(emitter, Stage.BUILD, "Build"):
        _check_cancel()
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

        defs  = json.loads(bundle.files.get("policySet.json", "{}")).get(
                    "properties", {}).get("policyDefinitions", [])
        multi = sum(1 for d in defs if len(d.get("groupNames", [])) > 1)
        emitter.stage_completed(
            Stage.BUILD,
            f"{len(approved)} controls with coverage  |  "
            f"{len(defs)} policy definitions  |  "
            f"{multi} covering multiple controls",
            controls_with_coverage=len(approved), policy_definitions=len(defs),
            multi_control_policies=multi,
            initiative_version=bcfg.get("initiative_version", ""))

    with _stage(emitter, Stage.VALIDATE):
        _check_cancel()
        lint_errors = AzureValidator().lint(bundle)
        for index, lint_error in enumerate(lint_errors):
            emitter.warning(WarningKind.VALIDATION, stage=Stage.VALIDATE,
                            message=lint_error, index=index)
        emitter.stage_completed(Stage.VALIDATE, "", lint_errors=len(lint_errors))

    published_to = None
    if do_distribute:
        with _stage(emitter, Stage.DISTRIBUTE, "Distribute",
                    adapter=config["distribute"]["type"]):
            _check_cancel()
            adapter = get_adapter(config["distribute"]["type"])
            published_to = adapter.publish(
                bundle, out_dir=config.get("out_dir", "out"),
                target=config["distribute"].get("target"))
            emitter.stage_completed(Stage.DISTRIBUTE, f"published → {published_to}",
                                    published=True)

    if oos_reconsidered:
        emitter.warning(WarningKind.OOS_STALENESS, stage=Stage.BUILD,
                        message="OOS entries need review (see out/oos-reconsidered.json)",
                        count=len(oos_reconsidered))

    # ── timing ────────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start_time
    finish_wall = datetime.now(tz=timezone.utc)

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

    emitter.run_completed(
        message=f"⏱  Completed in {_fmt_elapsed(elapsed)}"
                f"  (started {start_wall.astimezone().strftime('%H:%M:%S')}"
                f", finished {finish_wall.astimezone().strftime('%H:%M:%S')})",
        duration_s=round(elapsed, 1), controls=n_controls,
        approved=len(approved), pending=len(pending),
        policy_definitions=len(defs), lint_errors=len(lint_errors),
        published=bool(published_to))

    return PipelineResult(catalog, mapping, bundle, lint_errors, published_to,
                          elapsed, emitter.run_id)

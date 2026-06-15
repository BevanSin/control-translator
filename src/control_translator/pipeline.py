"""Pipeline orchestration: ingest -> catalogue -> map -> build -> validate -> distribute."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

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


@dataclass
class PipelineResult:
    catalog: Catalog
    mapping: MappingSet
    bundle: ArtifactBundle | None
    lint_errors: list[str]
    published_to: str | None


def run_pipeline(config: dict, *, do_distribute: bool = True) -> PipelineResult:
    fw = config["framework"]

    _banner(f"Ingest  — {fw.get('display_name', fw['id'])} v{fw['version']}")
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
    policies = get_catalogue(ccfg["type"], cache_path, ccfg).builtins()
    _done(f"{len(policies)} built-in policies available"
          + (" (cached)" if from_cache else " — cache written for next run"))

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
        # always save whatever was completed — even on Ctrl+C or crash
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

    approved = mapping.approved()
    defs = json.loads(bundle.files.get("policySet.json", "{}")).get(
        "properties", {}).get("policyDefinitions", [])
    _done(f"{len(approved)} controls with coverage  |  "
          f"{len(defs)} policy definitions  |  "
          f"{sum(1 for d in defs if len(d.get('groupNames',[])) > 1)} covering multiple controls")

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

    print(file=sys.stderr)
    return PipelineResult(catalog, mapping, bundle, lint_errors, published_to)

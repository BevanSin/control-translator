"""Run a mapping evaluation and produce a report.

Deliberately offline by default: the bundled catalogue and the heuristic
classifier need no credentials and no network, so the retrieval numbers are
reproducible anywhere. Point ``mapping.classifier`` at a real provider to
compare configurations.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from ..catalogue import get_catalogue
from ..ingest import get_ingestor
from ..mapping.corrections import load_corrections
from ..mapping.store import _norm_id
from .goldset import align_gold_set, load_gold_set
from .metrics import recall_at_k, rejected_policy_hits, score_selection

DEFAULT_KS = (5, 12, 25, 50)


def _load_rejected_policies(paths: object) -> set[str]:
    """Policy ids a reviewer has rejected outright, from the OOS/ignore register."""
    if not paths:
        return set()
    candidates = [paths] if isinstance(paths, str) else list(paths)
    rejected: set[str] = set()
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            entries = json.load(handle)
        for entry in entries:
            if isinstance(entry, dict) and entry.get("policy_id"):
                rejected.add(_norm_id(entry["policy_id"]))
    return rejected


def evaluate(config: dict, *, initiative_path: str, ks: tuple[int, ...] = DEFAULT_KS,
             group_prefix: str = "", control_limit: int | None = None) -> dict:
    """Score the configured mapping stack against a published initiative."""
    framework = config["framework"]
    ingest_cfg = config["ingest"]
    catalogue_cfg = config["catalogue"]
    mapping_cfg = config.get("mapping", {})

    catalog = get_ingestor(ingest_cfg["type"]).ingest(
        ingest_cfg["source"],
        framework_id=framework["id"],
        version=framework["version"],
        classification_profile=ingest_cfg.get("classification_profile", "all"))
    controls = {control.id: control for control in catalog.controls()}

    policies = get_catalogue(
        catalogue_cfg["type"], catalogue_cfg.get("source"), catalogue_cfg).builtins()
    policy_ids = {_norm_id(policy.id) for policy in policies}

    gold = load_gold_set(initiative_path, group_prefix=group_prefix)
    aligned = align_gold_set(gold, control_ids=set(controls),
                             catalogue_policy_ids=policy_ids)

    evaluated_ids = sorted(aligned.pairs)
    if control_limit is not None:
        evaluated_ids = evaluated_ids[:control_limit]
    evaluated_gold = {cid: aligned.pairs[cid] for cid in evaluated_ids}

    from ..mapping.base import get_mapper

    mapper = get_mapper(mapping_cfg.get("engine", "agentic"), mapping_cfg)
    if not hasattr(mapper, "retriever"):
        raise ValueError(
            f"evaluation needs a retrieval-based mapper, but engine "
            f"{mapping_cfg.get('engine', 'agentic')!r} has no retriever. Use the "
            f"agentic engine.")
    mapper.prepare(policies)
    if hasattr(mapper, "set_corrections"):
        mapper.set_corrections(load_corrections(mapping_cfg.get("corrections")))

    max_k = max(ks) if ks else 0
    shortlists: dict[str, list[str]] = {}
    selected: dict[str, set[str]] = {}
    per_control: list[dict] = []

    for control_id in evaluated_ids:
        control = controls[control_id]
        expected = evaluated_gold[control_id]

        ranked = mapper.retriever.query(f"{control.title}. {control.prose}", max_k)
        shortlist = [_norm_id(policy.id) for policy, _ in ranked]
        shortlists[control_id] = shortlist

        proposal = mapper.propose(control, policies)
        chosen = {_norm_id(policy.id) for policy in proposal.policies}
        selected[control_id] = chosen

        per_control.append({
            "control_id": control_id,
            "gold_policies": len(expected),
            "shortlist_hits": len(expected & set(shortlist[:mapping_cfg.get("top_k", 12)])),
            "selected": len(chosen),
            "selected_correct": len(chosen & expected),
            "missed": sorted(expected - chosen),
        })

    recall_rows = [recall_at_k(shortlists, evaluated_gold, k).to_dict() for k in ks]
    selection = score_selection(selected, evaluated_gold)
    rejected = _load_rejected_policies(catalogue_cfg.get("oos_register")
                                       or mapping_cfg.get("global_ignore"))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "framework": {"id": framework["id"], "version": framework["version"]},
        "catalogue": {"type": catalogue_cfg["type"], "policies": len(policies)},
        "mapping": {
            "engine": mapping_cfg.get("engine", "agentic"),
            "classifier": mapping_cfg.get("classifier", "heuristic"),
            "retrieval": mapping_cfg.get("retrieval", "tfidf"),
            "top_k": mapping_cfg.get("top_k", 12),
        },
        "gold_set": aligned.to_dict(),
        "recall_at_k": recall_rows,
        "selection": selection.to_dict(),
        "rejected_policy_check": rejected_policy_hits(selected, rejected),
        "per_control": per_control,
    }


def format_report(report: dict) -> str:
    """Readable console summary of an evaluation report."""
    gold = report["gold_set"]
    mapping = report["mapping"]
    lines = [
        f"Evaluation - {report['framework']['id']} v{report['framework']['version']}",
        f"  gold set          {gold['source']}",
        f"  published         {gold['published_controls']} controls, "
        f"{gold['published_pairs']} pairs",
        f"  evaluated         {gold['evaluated_controls']} controls, "
        f"{gold['evaluated_pairs']} pairs",
        f"  not in framework  {len(gold['controls_absent_from_framework'])} controls",
        f"  not in catalogue  {len(gold['policies_absent_from_catalogue'])} policies",
        f"  catalogue         {report['catalogue']['type']} "
        f"({report['catalogue']['policies']} policies)",
        f"  mapping           engine={mapping['engine']} "
        f"classifier={mapping['classifier']} retrieval={mapping['retrieval']} "
        f"top_k={mapping['top_k']}",
        "",
        "Retrieval ceiling - can the shortlist even contain the gold policy?",
    ]
    for row in report["recall_at_k"]:
        lines.append(
            f"  recall@{row['k']:<3} {row['recall']:6.1%}   "
            f"{row['matched_pairs']}/{row['total_pairs']} pairs   "
            f"{row['controls_fully_covered']}/{row['controls']} controls fully covered")

    selection = report["selection"]
    lines += [
        "",
        f"Selection with classifier={mapping['classifier']}",
        f"  precision {selection['precision']:.1%}   recall {selection['recall']:.1%}   "
        f"f1 {selection['f1']:.1%}",
        f"  tp {selection['true_positives']}  fp {selection['false_positives']}  "
        f"fn {selection['false_negatives']}",
    ]

    rejected = report["rejected_policy_check"]
    lines += [
        "",
        f"Previously rejected policies proposed: "
        f"{rejected['total_rejected_proposals']} across "
        f"{rejected['controls_proposing_rejected']} controls "
        f"(register holds {rejected['rejected_policies_known']})",
        "",
        "The gold set is a reference, not absolute truth. Read these numbers as a",
        "regression baseline and a way to compare configurations, not as a grade.",
    ]
    return "\n".join(lines)

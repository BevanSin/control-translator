"""Seeding the mapping store from existing artefacts.

Two seed sources are supported:

  1. policySet.json — an existing Azure initiative (published or custom).
     Extracts control→policy pairs from policyDefinitionGroups + policyDefinitions.
     Use case: bootstrap from a previously published version (e.g. NZISM v3.8)
     so the LLM only needs to classify controls not already covered.

  2. CSV — a flat mapping file with columns:
       control_id, policy_id, decision, reason
     Use case: port from a manual spreadsheet, or incorporate a partner's mapping.

Seed decisions are written to the mapping store with source="seeded". They are
carry-forwarded on every subsequent run exactly like LLM decisions. Human review
decisions (source="human-review" or "review-override") are never overwritten.

Usage:
    ct seed --from-initiative nzism-v3.8.json --config config\\nzism-azure.json
    ct seed --from-csv       prior-mappings.csv --config config\\nzism-azure.json
    ct seed --from-initiative v3.8.json --from-csv extras.csv --config ... --dry-run
"""
from __future__ import annotations

import csv
import json
import os
import re

from .mapping.store import MappingStore, _norm_id
from .models import ControlMapping, Decision, MappingSet, PolicyRef

_PROTECTED_SOURCES = {"human-review", "review-override"}
_CTRL_PATTERN      = re.compile(r"\d{2}\.\d+\.\d+\.[A-Z]\.\d{2}")


def _strip_group_prefix(name: str, prefix: str) -> str:
    """Remove a known group prefix to recover the bare control ID."""
    if prefix and name.startswith(prefix):
        return name[len(prefix):]
    # fallback: try to extract the NZISM-style control ID from the name
    m = _CTRL_PATTERN.search(name)
    return m.group(0) if m else name


# ── policySet.json seeder ─────────────────────────────────────────────────────

def seed_from_initiative(
    pset_path: str,
    *,
    store: MappingStore,
    framework_id: str,
    version: str,
    group_prefix: str = "",
    overwrite_human: bool = False,
    dry_run: bool = False,
) -> dict:
    """Extract control→policy pairs from a policySet.json and write to the mapping store.

    Returns a summary: {seeded, skipped_human, skipped_no_control, already_seeded}.
    """
    with open(pset_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    props = raw.get("properties", raw)

    # Build group_name → control_id map
    ctrl_from_group: dict[str, str] = {}
    for g in props.get("policyDefinitionGroups", []):
        gname    = g.get("name", "")
        ctrl_id  = _strip_group_prefix(gname, group_prefix)
        if ctrl_id:
            ctrl_from_group[gname] = ctrl_id

    # Accumulate policies per control
    ctrl_policies: dict[str, list[PolicyRef]] = {}
    for pd in props.get("policyDefinitions", []):
        pid      = pd.get("policyDefinitionId", "")
        ref_name = pd.get("policyDefinitionReferenceId", "")
        for gname in pd.get("groupNames", []):
            ctrl_id = ctrl_from_group.get(gname)
            if ctrl_id:
                ctrl_policies.setdefault(ctrl_id, []).append(
                    PolicyRef(policy_id=pid, display_name=ref_name))

    # Load store and write decisions
    mapping_set = store.load(framework_id, version)
    basename    = os.path.basename(pset_path)
    summary = {"seeded": 0, "skipped_human": 0,
               "skipped_no_control": 0, "already_seeded": 0}

    for ctrl_id, refs in sorted(ctrl_policies.items()):
        existing = mapping_set.mappings.get(ctrl_id)
        if existing and existing.source in _PROTECTED_SOURCES and not overwrite_human:
            summary["skipped_human"] += 1
            continue
        if existing and existing.source == "seeded":
            summary["already_seeded"] += 1
            if overwrite_human:
                pass   # still update
            else:
                continue

        mapping_set.mappings[ctrl_id] = ControlMapping(
            control_id=ctrl_id,
            decision=Decision.INCLUDE,
            policies=refs,
            rationale=f"Seeded from initiative: {basename}",
            source="seeded",
            confidence=1.0,
        )
        summary["seeded"] += 1

    if not dry_run:
        store.save(mapping_set)

    summary["groups_found"]   = len(ctrl_from_group)
    summary["pairs_extracted"] = sum(len(v) for v in ctrl_policies.values())
    return summary


# ── CSV seeder ────────────────────────────────────────────────────────────────

_ARM_PREFIX = "/providers/Microsoft.Authorization/policyDefinitions/"


def _normalise_pid(pid: str) -> str:
    pid = pid.strip()
    if not pid:
        return ""
    # bare GUID → full ARM path
    if "/" not in pid:
        return _ARM_PREFIX + pid.lower()
    return pid


def seed_from_csv(
    csv_path: str,
    *,
    store: MappingStore,
    framework_id: str,
    version: str,
    overwrite_human: bool = False,
    dry_run: bool = False,
) -> dict:
    """Read control→policy decisions from a CSV file and write to the mapping store.

    Required columns: control_id, decision
    Optional columns: policy_id, reason, display_name

    decision values: include | ignore | review
    """
    mapping_set = store.load(framework_id, version)
    summary = {"seeded": 0, "skipped_human": 0,
               "skipped_bad_row": 0, "already_seeded": 0}

    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        # normalise header names to lowercase
        rows = [{k.strip().lower(): v for k, v in row.items()}
                for row in reader]

    for row in rows:
        ctrl_id  = (row.get("control_id") or "").strip()
        dec_str  = (row.get("decision")    or "").strip().lower()
        pid_raw  = (row.get("policy_id")   or "").strip()
        reason   = (row.get("reason")      or row.get("rationale") or "").strip()
        dname    = (row.get("display_name") or "").strip()

        if not ctrl_id or not dec_str:
            summary["skipped_bad_row"] += 1
            continue

        try:
            decision = Decision(dec_str)
        except ValueError:
            summary["skipped_bad_row"] += 1
            continue

        existing = mapping_set.mappings.get(ctrl_id)
        if existing and existing.source in _PROTECTED_SOURCES and not overwrite_human:
            summary["skipped_human"] += 1
            continue
        if existing and existing.source == "seeded" and not overwrite_human:
            summary["already_seeded"] += 1
            continue

        pid = _normalise_pid(pid_raw)
        refs = [PolicyRef(policy_id=pid, display_name=dname)] if pid else []

        mapping_set.mappings[ctrl_id] = ControlMapping(
            control_id=ctrl_id,
            decision=decision,
            policies=refs,
            rationale=reason or f"Seeded from CSV: {os.path.basename(csv_path)}",
            source="seeded",
            confidence=1.0,
        )
        summary["seeded"] += 1

    if not dry_run:
        store.save(mapping_set)
    return summary

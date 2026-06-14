"""Durable, version-controlled mapping store + OOS register loader."""
from __future__ import annotations

import json
import os

from ..models import MappingSet

# Type alias: the global_ignore config value can be a single path or a list of paths.
_PathArg = str | list[str] | None


class MappingStore:
    def __init__(self, path: str):
        self.path = path

    def load(self, framework_id: str, version: str) -> MappingSet:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                return MappingSet.from_dict(json.load(fh))
        return MappingSet(framework_id=framework_id, version=version)

    def save(self, mapping_set: MappingSet) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(mapping_set.to_dict(), fh, indent=2, ensure_ascii=False)


def _norm_id(policy_id: str) -> str:
    """Normalise any policy id form to a bare lowercase GUID for comparison.

    Handles both bare GUIDs ('82067dbb-...') and full ARM resource ids
    ('/providers/Microsoft.Authorization/policyDefinitions/82067dbb-...').
    """
    return policy_id.rstrip("/").split("/")[-1].lower()


def _paths(arg: _PathArg) -> list[str]:
    """Normalise a single path or list of paths into a list of existing file paths."""
    if not arg:
        return []
    paths = [arg] if isinstance(arg, str) else list(arg)
    return [p for p in paths if p and os.path.exists(p)]


def _load_one(path: str) -> tuple[set[str], list[dict]]:
    """Load one OOS register file. Returns (normalised_id_set, record_list)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    ids: set[str] = set()
    records: list[dict] = []
    for item in data:
        raw = item if isinstance(item, str) else item.get("policy_id", "")
        if raw:
            ids.add(_norm_id(raw))
        if isinstance(item, dict):
            records.append(item)
    return ids, records


def load_global_ignore(arg: _PathArg) -> set[str]:
    """Policy ids (normalised to bare GUIDs) to exclude from all proposals.

    `arg` can be:
      - a single file path  (string) — backward-compatible
      - a list of file paths         — union of all files
      - None / empty string          — empty set

    Typical use: one shared cross-framework file + one per-standard file.
    Example config:
        "global_ignore": [
            "data/mappings/global-ignore.json",   <- process controls, In-Guest, etc.
            "data/mappings/nzism-ignore.json"     <- NZISM-specific; IRAP would use irap-ignore.json
        ]
    """
    result: set[str] = set()
    for path in _paths(arg):
        ids, _ = _load_one(path)
        result |= ids
    return result


def load_oos_records(arg: _PathArg) -> list[dict]:
    """Full OOS records from one or more register files (union, preserving order).

    Returns the rich records (policy_id, reason, oos_date, ...) for publishing
    in out-of-scope.json. Accepts the same path or list-of-paths as load_global_ignore.
    """
    result: list[dict] = []
    for path in _paths(arg):
        _, records = _load_one(path)
        result.extend(records)
    return result


def check_oos_staleness(oos_records: list[dict],
                        policies: list) -> list[dict]:
    """Cross-reference OOS entries against the current built-in catalogue.

    Returns entries that warrant reconsideration:
    - Policy was [Preview]: when OOS'd but is now GA in the catalogue.
    - Policy no longer exists in the catalogue (deprecated / removed).
    """
    current = {_norm_id(p.id): p for p in policies}
    reconsidered: list[dict] = []
    for entry in oos_records:
        pid = _norm_id(entry.get("policy_id", ""))
        old_name = entry.get("display_name", "")
        if not pid:
            continue
        if pid not in current:
            reconsidered.append({
                **entry,
                "reconsideration_reason": (
                    "Policy no longer in the built-in catalogue — "
                    "may have been deprecated or removed."),
            })
        else:
            new_name = current[pid].display_name
            was_preview = old_name.strip().lower().startswith("[preview]")
            is_preview = new_name.strip().lower().startswith("[preview]")
            if was_preview and not is_preview:
                reconsidered.append({
                    **entry,
                    "current_display_name": new_name,
                    "reconsideration_reason": (
                        "No longer in preview — now generally available. "
                        "Review for inclusion in the initiative."),
                })
    return reconsidered

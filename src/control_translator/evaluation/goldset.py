"""Gold mapping set built from a published Azure Policy initiative.

A published regulatory-compliance initiative (for example Microsoft's NZISM
v3.8 policySet) already states which built-in policies were mapped to which
control. That gives a reference mapping to score against without collecting
any new data.

The reference is deliberately *not* treated as absolute truth. It reflects one
publisher's judgement at one framework version, so two kinds of drift are
reported separately rather than counted as failures:

  * controls that no longer exist in the framework version under evaluation
  * policies the local catalogue does not carry

Both are reported so a score is always read next to the ceiling it could
possibly have reached.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from ..mapping.store import _norm_id
from ..seeds import extract_initiative_pairs


@dataclass(frozen=True)
class GoldSet:
    """Control → policy pairs exactly as published, before any alignment."""

    source: str
    pairs: dict[str, frozenset[str]]

    @property
    def control_count(self) -> int:
        return len(self.pairs)

    @property
    def pair_count(self) -> int:
        return sum(len(policies) for policies in self.pairs.values())


@dataclass(frozen=True)
class AlignedGoldSet:
    """A gold set reduced to what the current framework and catalogue can reach."""

    source: str
    pairs: dict[str, frozenset[str]]
    missing_controls: tuple[str, ...]
    unreachable_policies: tuple[str, ...]
    published_control_count: int
    published_pair_count: int

    @property
    def control_count(self) -> int:
        return len(self.pairs)

    @property
    def pair_count(self) -> int:
        return sum(len(policies) for policies in self.pairs.values())

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "published_controls": self.published_control_count,
            "published_pairs": self.published_pair_count,
            "evaluated_controls": self.control_count,
            "evaluated_pairs": self.pair_count,
            "controls_absent_from_framework": list(self.missing_controls),
            "policies_absent_from_catalogue": list(self.unreachable_policies),
        }


def load_gold_set(initiative_path: str, *, group_prefix: str = "") -> GoldSet:
    """Read a published initiative into normalised control → policy pairs."""
    extracted = extract_initiative_pairs(initiative_path, group_prefix=group_prefix)
    pairs = {
        control_id: frozenset(_norm_id(ref.policy_id) for ref in refs if ref.policy_id)
        for control_id, refs in extracted.items()
    }
    pairs = {control_id: policies for control_id, policies in pairs.items() if policies}
    return GoldSet(source=os.path.basename(initiative_path), pairs=pairs)


def align_gold_set(gold: GoldSet, *, control_ids: set[str],
                   catalogue_policy_ids: set[str]) -> AlignedGoldSet:
    """Restrict a gold set to controls and policies the current run can actually reach.

    ``control_ids`` are the ids ingested from the framework under evaluation and
    ``catalogue_policy_ids`` the policies available to the retriever, both
    already normalised by the caller for policies.
    """
    aligned: dict[str, frozenset[str]] = {}
    missing_controls: list[str] = []
    unreachable: set[str] = set()

    for control_id, policies in gold.pairs.items():
        reachable = frozenset(p for p in policies if p in catalogue_policy_ids)
        unreachable.update(policies - reachable)
        if control_id not in control_ids:
            missing_controls.append(control_id)
            continue
        if reachable:
            aligned[control_id] = reachable

    return AlignedGoldSet(
        source=gold.source,
        pairs=aligned,
        missing_controls=tuple(sorted(missing_controls)),
        unreachable_policies=tuple(sorted(unreachable)),
        published_control_count=gold.control_count,
        published_pair_count=gold.pair_count,
    )

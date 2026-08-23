"""Scoring for mapping quality against a reference gold set.

Two distinct questions are answered, and keeping them apart matters:

``recall@k`` asks whether the retriever's shortlist even contains the gold
policy. It is the hard ceiling on the whole product, because a policy that is
never shortlisted can never be selected by any classifier, however good. It
needs no model, so it is deterministic and cheap.

Selection precision and recall then ask what the classifier did with that
shortlist. Those numbers only mean something next to the name of the
classifier that produced them.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecallAtK:
    k: int
    matched_pairs: int
    total_pairs: int
    controls_fully_covered: int
    control_count: int

    @property
    def recall(self) -> float:
        return self.matched_pairs / self.total_pairs if self.total_pairs else 0.0

    def to_dict(self) -> dict:
        return {
            "k": self.k,
            "recall": round(self.recall, 4),
            "matched_pairs": self.matched_pairs,
            "total_pairs": self.total_pairs,
            "controls_fully_covered": self.controls_fully_covered,
            "controls": self.control_count,
        }


@dataclass(frozen=True)
class SelectionScore:
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0

    def to_dict(self) -> dict:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
        }


def recall_at_k(shortlists: dict[str, list[str]], gold: dict[str, frozenset[str]],
                k: int) -> RecallAtK:
    """How much of the gold set survives into the top ``k`` of each shortlist."""
    matched = 0
    total = 0
    fully_covered = 0
    for control_id, expected in gold.items():
        head = set(shortlists.get(control_id, [])[:k])
        hits = len(expected & head)
        matched += hits
        total += len(expected)
        if expected and hits == len(expected):
            fully_covered += 1
    return RecallAtK(k=k, matched_pairs=matched, total_pairs=total,
                     controls_fully_covered=fully_covered, control_count=len(gold))


def score_selection(selected: dict[str, set[str]],
                    gold: dict[str, frozenset[str]]) -> SelectionScore:
    """Compare selected policies against the gold set, per control."""
    true_positives = false_positives = false_negatives = 0
    for control_id, expected in gold.items():
        chosen = selected.get(control_id, set())
        true_positives += len(chosen & expected)
        false_positives += len(chosen - expected)
        false_negatives += len(expected - chosen)
    return SelectionScore(true_positives=true_positives,
                          false_positives=false_positives,
                          false_negatives=false_negatives)


def rejected_policy_hits(selected: dict[str, set[str]],
                         rejected: set[str]) -> dict:
    """Count selections that a human previously rejected outright.

    These are not scored against the gold set. A reviewer rejecting a policy is
    a statement about the policy itself, so proposing it again is a signal worth
    watching independently of any published mapping.
    """
    hits: dict[str, list[str]] = {}
    for control_id, chosen in selected.items():
        overlap = sorted(chosen & rejected)
        if overlap:
            hits[control_id] = overlap
    return {
        "rejected_policies_known": len(rejected),
        "controls_proposing_rejected": len(hits),
        "total_rejected_proposals": sum(len(v) for v in hits.values()),
        "detail": hits,
    }

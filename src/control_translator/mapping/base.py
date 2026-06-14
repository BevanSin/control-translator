"""Mapper interface: propose built-in policies for a single control.

A Mapper does NOT decide inclusion on its own — it proposes candidates with a
confidence score. The engine applies the carry-forward, global-ignore, and
auto-approve / human-gate policy on top.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import Control
from ..catalogue import PolicyDefinition


@dataclass
class Proposal:
    policies: list[PolicyDefinition] = field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""
    # policies the classifier flagged as globally out-of-scope for this control
    oos_candidates: list[dict] = field(default_factory=list)


class Mapper(ABC):
    def prepare(self, policies: list[PolicyDefinition]) -> None:
        """Optional one-time setup over the full policy corpus (e.g. fit an index)."""

    def set_oos_context(self, oos: list[dict]) -> None:
        """Pass the existing OOS register so the mapper/classifier can pattern-match."""

    @abstractmethod
    def propose(self, control: Control, policies: list[PolicyDefinition]) -> Proposal:
        ...


def get_mapper(kind: str, options: dict | None = None) -> Mapper:
    options = options or {}
    if kind == "keyword":
        from .keyword import KeywordMapper
        return KeywordMapper()
    if kind == "agentic":
        from .agentic import AgenticMapper
        from .classifier import get_classifier
        classifier = get_classifier(
            options.get("classifier", "anthropic"),
            model=options.get("model", "gpt-4o-mini"),
            effort=options.get("effort", "medium"),
            base_url=options.get("foundry_base_url"),
            api_key=options.get("api_key"),
            api_version=options.get("azure_api_version"))
        return AgenticMapper(classifier=classifier, top_k=options.get("top_k", 12))
    raise ValueError(f"unknown mapper: {kind!r}")

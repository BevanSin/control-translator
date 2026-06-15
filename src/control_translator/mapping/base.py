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

    def set_corrections(self, corrections: list[dict]) -> None:
        """Pass human corrections as few-shot examples. Default: no-op."""

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

        # Retriever: "embedding" uses semantic search; default "tfidf" is lexical
        retrieval_kind = options.get("retrieval", "tfidf").lower()
        if retrieval_kind == "embedding":
            from .embedding import EmbeddingRetriever
            retriever = EmbeddingRetriever(
                endpoint=options.get("embedding_endpoint") or options.get("foundry_base_url"),
                model=options.get("embedding_model", "text-embedding-3-small"),
                cache_base=options.get("embedding_cache", "data/cache/embeddings"),
                api_key=options.get("api_key"),
            )
        else:
            from .retrieval import TfidfRetriever
            retriever = TfidfRetriever()

        return AgenticMapper(classifier=classifier, retriever=retriever,
                             top_k=options.get("top_k", 12))
    raise ValueError(f"unknown mapper: {kind!r}")

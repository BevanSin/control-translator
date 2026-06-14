"""Retrieval stage: shortlist candidate built-in policies for a control.

The agentic mapper runs retrieval FIRST (cheap, recall-oriented) to shortlist the
top-k policies, then sends only that shortlist to the LLM classifier. This keeps the
expensive judgement step bounded regardless of catalogue size.

`TfidfRetriever` is a dependency-free TF-IDF cosine baseline so the pipeline runs and
tests offline. Swap in an embedding-based retriever later for better recall.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import Counter

from ._text import tokenize
from ..catalogue import PolicyDefinition


class Retriever(ABC):
    @abstractmethod
    def fit(self, policies: list[PolicyDefinition]) -> None:
        ...

    @abstractmethod
    def query(self, text: str, k: int) -> list[tuple[PolicyDefinition, float]]:
        ...


class TfidfRetriever(Retriever):
    def __init__(self):
        self._policies: list[PolicyDefinition] = []
        self._idf: dict[str, float] = {}
        self._vectors: list[dict[str, float]] = []
        self._norms: list[float] = []

    def fit(self, policies: list[PolicyDefinition]) -> None:
        self._policies = policies
        docs = [tokenize(f"{p.display_name} {p.description}") for p in policies]
        n = len(docs) or 1
        df = Counter(tok for doc in docs for tok in doc)
        self._idf = {tok: math.log(n / (1 + cnt)) + 1.0 for tok, cnt in df.items()}
        self._vectors, self._norms = [], []
        for doc in docs:
            vec = {tok: self._idf.get(tok, 0.0) for tok in doc}
            self._vectors.append(vec)
            self._norms.append(math.sqrt(sum(w * w for w in vec.values())) or 1.0)

    def query(self, text: str, k: int) -> list[tuple[PolicyDefinition, float]]:
        q_tokens = tokenize(text)
        q_vec = {tok: self._idf.get(tok, 0.0) for tok in q_tokens}
        q_norm = math.sqrt(sum(w * w for w in q_vec.values())) or 1.0

        scored: list[tuple[PolicyDefinition, float]] = []
        for policy, vec, norm in zip(self._policies, self._vectors, self._norms):
            dot = sum(w * vec.get(tok, 0.0) for tok, w in q_vec.items())
            sim = dot / (q_norm * norm)
            if sim > 0:
                scored.append((policy, round(sim, 4)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]

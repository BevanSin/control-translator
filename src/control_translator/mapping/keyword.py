"""Deterministic baseline mapper: token overlap between control and policy text.

No external dependencies — useful as a reproducible baseline and for the offline demo.
The real engine is mapping/agentic.py (retrieval + LLM classify). Keep this as a cheap
regression check against the agentic mapper's output.
"""
from __future__ import annotations

from .base import Mapper, Proposal
from ._text import tokenize, jaccard
from ..models import Control
from ..catalogue import PolicyDefinition


class KeywordMapper(Mapper):
    def propose(self, control: Control, policies: list[PolicyDefinition]) -> Proposal:
        ctrl_tokens = tokenize(f"{control.title} {control.prose}")
        if not ctrl_tokens:
            return Proposal()
        scored = []
        for p in policies:
            score = jaccard(ctrl_tokens, tokenize(f"{p.display_name} {p.description}"))
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda t: t[0], reverse=True)
        if not scored:
            return Proposal()
        best = scored[0][0]
        matched = ", ".join(sorted(
            tokenize(control.title) & tokenize(scored[0][1].display_name))) or "text overlap"
        return Proposal(
            policies=[p for s, p in scored[:5] if s >= best * 0.6],
            confidence=round(best, 3),
            rationale=f"keyword baseline; matched on: {matched}",
        )

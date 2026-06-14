"""Agentic mapper: retrieval shortlist -> LLM classification.

This is the heart of the engine. For each control:
  1. RETRIEVE  - shortlist the top-k built-in policies by similarity (recall-oriented).
  2. CLASSIFY  - ask the classifier which shortlisted policies materially satisfy the
                 control, grounded only in the supplied text, with calibrated
                 confidence + a rationale per decision.
  3. PROPOSE   - return the relevant policies; the engine applies the auto-approve /
                 authority sign-off gate on top.

Built-in policy only. A future variant proposes a custom policy spec when no built-in
adequately covers a control.
"""
from __future__ import annotations

from .base import Mapper, Proposal
from .retrieval import Retriever, TfidfRetriever
from .classifier import Classifier, get_classifier
from ..models import Control
from ..catalogue import PolicyDefinition


class AgenticMapper(Mapper):
    def __init__(self, *, classifier: Classifier | None = None,
                 retriever: Retriever | None = None, top_k: int = 12):
        self.retriever = retriever or TfidfRetriever()
        self.classifier = classifier or get_classifier("anthropic")
        self.top_k = top_k
        self._fitted = False

    def prepare(self, policies: list[PolicyDefinition]) -> None:
        self.retriever.fit(policies)
        self._fitted = True

    def set_oos_context(self, oos: list[dict]) -> None:
        self.classifier.set_oos_context(oos)

    def propose(self, control: Control, policies: list[PolicyDefinition]) -> Proposal:
        if not self._fitted:
            self.prepare(policies)

        ranked = self.retriever.query(f"{control.title}. {control.prose}", self.top_k)
        shortlist = [p for p, _ in ranked]
        if not shortlist:
            return Proposal()

        assessments = self.classifier.classify(control, shortlist)

        # collect OOS candidates flagged by the classifier
        oos_candidates = [
            {"policy_id": shortlist[a.index].id,
             "display_name": shortlist[a.index].display_name,
             "oos_reason": a.oos_reason}
            for a in assessments
            if a.oos_candidate and 0 <= a.index < len(shortlist) and a.oos_reason
        ]

        relevant = [(shortlist[a.index], a) for a in assessments
                    if a.relevant and 0 <= a.index < len(shortlist)]
        if not relevant:
            return Proposal(rationale="no candidate judged relevant by the classifier",
                            oos_candidates=oos_candidates)

        relevant.sort(key=lambda t: t[1].confidence, reverse=True)
        return Proposal(
            policies=[p for p, _ in relevant],
            confidence=max(a.confidence for _, a in relevant),
            rationale="; ".join(f"{p.display_name}: {a.rationale}"
                                for p, a in relevant)[:600],
            oos_candidates=oos_candidates,
        )

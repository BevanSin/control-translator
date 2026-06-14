"""Shared tokenization for the baseline mapper and the offline retriever."""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "and", "for", "with", "that", "this", "are", "must", "should",
    "a", "an", "of", "to", "in", "on", "be", "is", "or", "as", "by", "at", "all",
    "agencies", "agency", "ensure", "used", "use", "using", "from", "which", "via",
}


def tokenize(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

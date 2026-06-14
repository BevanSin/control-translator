"""Load provider policy definitions from a cached JSON file (offline demo/tests)."""
from __future__ import annotations

import json

from .base import PolicyCatalogue, PolicyDefinition


class OfflinePolicyCatalogue(PolicyCatalogue):
    def __init__(self, source: str):
        if not source:
            raise ValueError("offline catalogue requires a source file path")
        self.source = source

    def builtins(self) -> list[PolicyDefinition]:
        with open(self.source, encoding="utf-8") as fh:
            data = json.load(fh)
        return [PolicyDefinition.from_dict(d) for d in data]

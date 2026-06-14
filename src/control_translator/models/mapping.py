"""The durable mapping model: control -> built-in policy decisions.

This is the asset that makes annual revisions cheap. It is version-controlled and
carried forward, so each cycle only reviews new/changed built-ins, not the whole set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    INCLUDE = "include"     # approved: goes into the built initiative
    IGNORE = "ignore"       # deliberately excluded for this framework
    REVIEW = "review"       # proposed by the mapper, awaiting authority sign-off


@dataclass
class PolicyRef:
    policy_id: str                       # provider definition id (e.g. Azure GUID/resource id)
    display_name: str = ""
    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"policy_id": self.policy_id, "display_name": self.display_name,
                "parameters": self.parameters}

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyRef":
        return cls(policy_id=d["policy_id"], display_name=d.get("display_name", ""),
                   parameters=d.get("parameters", {}))


@dataclass
class ControlMapping:
    control_id: str
    decision: Decision = Decision.REVIEW
    policies: list[PolicyRef] = field(default_factory=list)
    rationale: str = ""
    source: str = "auto"                 # "auto" | "human"
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {"control_id": self.control_id, "decision": self.decision.value,
                "policies": [p.to_dict() for p in self.policies],
                "rationale": self.rationale, "source": self.source,
                "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "ControlMapping":
        return cls(control_id=d["control_id"], decision=Decision(d.get("decision", "review")),
                   policies=[PolicyRef.from_dict(p) for p in d.get("policies", [])],
                   rationale=d.get("rationale", ""), source=d.get("source", "auto"),
                   confidence=d.get("confidence", 0.0))


@dataclass
class MappingSet:
    framework_id: str
    version: str
    mappings: dict[str, ControlMapping] = field(default_factory=dict)
    # ephemeral — populated each run by the engine, not persisted to the store
    oos_suggestions: list[dict] = field(default_factory=list)
    preview_excluded: list[dict] = field(default_factory=list)

    def approved(self) -> list[ControlMapping]:
        return [m for m in self.mappings.values()
                if m.decision == Decision.INCLUDE and m.policies]

    def pending_review(self) -> list[ControlMapping]:
        return [m for m in self.mappings.values() if m.decision == Decision.REVIEW]

    def to_dict(self) -> dict:
        return {"framework_id": self.framework_id, "version": self.version,
                "mappings": {k: v.to_dict() for k, v in self.mappings.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "MappingSet":
        return cls(framework_id=d.get("framework_id", ""), version=d.get("version", ""),
                   mappings={k: ControlMapping.from_dict(v)
                             for k, v in d.get("mappings", {}).items()})

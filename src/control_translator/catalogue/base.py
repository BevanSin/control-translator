"""Provider policy catalogue interface: the set of built-in policies to map against."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PolicyDefinition:
    id: str                              # provider definition id (Azure: resource id or GUID)
    display_name: str
    description: str = ""
    category: str = ""
    policy_type: str = "BuiltIn"         # BuiltIn | Static | Custom
    effect: str = ""                     # default effect
    effect_allowed_values: list = field(default_factory=list)  # allowed effect values
    parameters: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyDefinition":
        return cls(id=d["id"], display_name=d.get("display_name", ""),
                   description=d.get("description", ""), category=d.get("category", ""),
                   policy_type=d.get("policy_type", "BuiltIn"),
                   effect=d.get("effect", ""),
                   effect_allowed_values=d.get("effect_allowed_values", []),
                   parameters=d.get("parameters", {}))


class PolicyCatalogue(ABC):
    """Source of provider policy definitions to map controls against."""

    @abstractmethod
    def builtins(self) -> list[PolicyDefinition]:
        ...


def get_catalogue(kind: str, source: str | None = None,
                  options: dict | None = None) -> PolicyCatalogue:
    options = options or {}
    if kind == "offline":
        from .offline import OfflinePolicyCatalogue
        return OfflinePolicyCatalogue(source)
    if kind == "azure":
        from .azure import AzurePolicyCatalogue
        return AzurePolicyCatalogue(
            cache=source or options.get("cache"),
            subscription=options.get("subscription"),
            include_static=options.get("include_static", False),
            exclude_deprecated=options.get("exclude_deprecated", True),
            exclude_manual=options.get("exclude_manual", True),
            exclude_non_auditable=options.get("exclude_non_auditable", True),
            refresh=options.get("refresh", False))
    raise ValueError(f"unknown catalogue: {kind!r}")

"""Control set builder interface: OSCAL catalogue + mapping -> deployable artefacts."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Catalog, MappingSet, ArtifactBundle


class ControlSetBuilder(ABC):
    @abstractmethod
    def build(self, catalog: Catalog, mapping: MappingSet, *,
              framework: dict, options: dict,
              oos: list | None = None,
              oos_suggestions: list | None = None,
              oos_reconsidered: list | None = None) -> ArtifactBundle:
        ...


def get_builder(kind: str) -> ControlSetBuilder:
    if kind == "azure":
        from .azure import AzurePolicySetBuilder
        return AzurePolicySetBuilder()
    raise ValueError(f"unknown builder: {kind!r}")

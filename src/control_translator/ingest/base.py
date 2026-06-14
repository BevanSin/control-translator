"""Framework ingestor interface: published standard -> OSCAL catalogue."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Catalog


class FrameworkIngestor(ABC):
    """Turn a published standard (PDF/CSV/HTML/...) into an OSCAL Catalog."""

    @abstractmethod
    def ingest(self, source: str, *, framework_id: str, version: str, **_options) -> Catalog:
        ...


def get_ingestor(kind: str) -> FrameworkIngestor:
    if kind == "fixture":
        from .fixture import FixtureIngestor
        return FixtureIngestor()
    if kind == "nzism":
        from .nzism import NzismIngestor
        return NzismIngestor()  # implemented — parses the NZISM CSV export
    raise ValueError(f"unknown ingestor: {kind!r}")

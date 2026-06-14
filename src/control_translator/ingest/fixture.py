"""Load an OSCAL catalogue straight from a JSON file on disk.

Used for the offline demo and tests. Real frameworks get their own ingestor.
"""
from __future__ import annotations

import json

from .base import FrameworkIngestor
from ..models import Catalog


class FixtureIngestor(FrameworkIngestor):
    def ingest(self, source: str, *, framework_id: str, version: str, **_options) -> Catalog:
        with open(source, encoding="utf-8") as fh:
            return Catalog.from_oscal(json.load(fh))

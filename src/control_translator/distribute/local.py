"""Local adapter: write the versioned bundle to the output directory."""
from __future__ import annotations

import os

from .base import DistributionAdapter
from ..models import ArtifactBundle


class LocalAdapter(DistributionAdapter):
    def publish(self, bundle: ArtifactBundle, *, out_dir: str = "out", **_) -> str:
        dest = os.path.join(out_dir, bundle.slug)
        os.makedirs(dest, exist_ok=True)
        for rel_path, content in bundle.files.items():
            full = os.path.join(dest, rel_path)
            os.makedirs(os.path.dirname(full) or dest, exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(content)
        return dest

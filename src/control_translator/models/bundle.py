"""An output artefact bundle: the set of files a distribution adapter publishes."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArtifactBundle:
    framework_id: str
    version: str
    files: dict[str, str] = field(default_factory=dict)   # relative path -> text content
    metadata: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return f"{self.framework_id}-{self.version}"

    def add(self, rel_path: str, content: str) -> None:
        self.files[rel_path] = content

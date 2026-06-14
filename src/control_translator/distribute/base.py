"""Distribution adapter interface: publish an artefact bundle to a target."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ArtifactBundle


class DistributionAdapter(ABC):
    @abstractmethod
    def publish(self, bundle: ArtifactBundle, **options) -> str:
        """Publish the bundle. Returns a human-readable location/result string."""
        ...


def get_adapter(kind: str) -> DistributionAdapter:
    if kind == "local":
        from .local import LocalAdapter
        return LocalAdapter()
    if kind == "community-policy":
        from .community_policy import CommunityPolicyAdapter
        return CommunityPolicyAdapter()
    if kind == "gov-repo":
        from .gov_repo import GovRepoAdapter
        return GovRepoAdapter()
    raise ValueError(f"unknown distribution adapter: {kind!r}")

"""Community Policy adapter: produce a PR-ready folder for Azure/Community-Policy.

TODO (real implementation):
  - Lay the bundle out in the Community-Policy repo convention
    (policySetDefinitions/<name>/azurepolicyset.json + README).
  - Clone/branch the fork, copy artefacts, commit, and open a PR via the GitHub API.
"""
from __future__ import annotations

from .base import DistributionAdapter
from ..models import ArtifactBundle


class CommunityPolicyAdapter(DistributionAdapter):
    def publish(self, bundle: ArtifactBundle, **_) -> str:
        raise NotImplementedError(
            "Community-Policy adapter not yet implemented. Format to the repo convention "
            "and open a PR. Use distribute.type=local for the demo."
        )

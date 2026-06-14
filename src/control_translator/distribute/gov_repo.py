"""Owner-hosted government repo adapter.

Models the Government of Canada pattern, where the national authority owns a public
GitHub repo for its cloud control framework (e.g. canada-ca/cloud-guardrails) and a
companion solution-accelerator repo for the tooling. Here, the standard owner could host a repo (e.g.
`authority-org/nzism-azure-policy`).

TODO (real implementation):
  - Scaffold the repo on first publish: LICENSE, README, CONTRIBUTING, SECURITY,
    a vX->vY crosswalk file (mirroring GC's guardrails crosswalk), GitHub Pages config.
  - Place the bundle under a versioned path (e.g. initiatives/nzism-3.9/).
  - Branch, commit, and open a PR against `target` for owner review.
"""
from __future__ import annotations

from .base import DistributionAdapter
from ..models import ArtifactBundle


class GovRepoAdapter(DistributionAdapter):
    def publish(self, bundle: ArtifactBundle, *, target: str | None = None, **_) -> str:
        raise NotImplementedError(
            "Gov-repo adapter not yet implemented. Scaffold + PR to the owner repo "
            f"(target={target!r}). See docstring for the canada-ca/cloud-guardrails model."
        )

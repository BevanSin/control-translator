"""Validate a built bundle.

`lint` is implemented (structural checks that need no cloud access). `sandbox_deploy`
is a TODO that deploys audit-only to a throwaway subscription and confirms the
initiative renders in the Defender Regulatory Compliance dashboard.
"""
from __future__ import annotations

import json

from ..models import ArtifactBundle


class AzureValidator:
    def lint(self, bundle: ArtifactBundle) -> list[str]:
        errors: list[str] = []
        if "policySet.json" not in bundle.files:
            return ["policySet.json missing from bundle"]
        props = json.loads(bundle.files["policySet.json"]).get("properties", {})
        if props.get("metadata", {}).get("category") != "Regulatory Compliance":
            errors.append("metadata.category should be 'Regulatory Compliance'")
        groups = {g["name"] for g in props.get("policyDefinitionGroups", [])}
        defs = props.get("policyDefinitions", [])
        if not defs:
            errors.append("no policyDefinitions in initiative (nothing approved to build)")
        seen = set()
        for d in defs:
            ref = d.get("policyDefinitionReferenceId")
            if ref in seen:
                errors.append(f"duplicate policyDefinitionReferenceId: {ref}")
            seen.add(ref)
            for gn in d.get("groupNames", []):
                if gn not in groups:
                    errors.append(f"policy {ref} references unknown group {gn}")
        return errors

    def sandbox_deploy(self, bundle: ArtifactBundle, subscription_id: str) -> None:
        raise NotImplementedError(
            "Sandbox deploy not yet implemented. Deploy main.bicep audit-only to a test "
            "subscription and confirm it appears in Defender Regulatory Compliance."
        )

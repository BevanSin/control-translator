"""Validated, versioned Azure built-in policy catalogue snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from importlib import resources
import json
from pathlib import Path
import re
from uuid import UUID

from .base import PolicyCatalogue, PolicyDefinition

_ASSET_PACKAGE = "control_translator.catalogue_assets"
_ASSET_NAME = "azure-builtins.json"
_REPOSITORY = "https://github.com/Azure/azure-policy"
_POLICY_ID = re.compile(
    r"^/providers/Microsoft\.Authorization/policyDefinitions/([^/]+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SnapshotMetadata:
    source_repository: str
    source_commit: str
    generated_at: str
    policy_count: int
    content_sha256: str


class BundledPolicyCatalogue(PolicyCatalogue):
    """Load the production Azure catalogue snapshot shipped with the release."""

    def __init__(self, source: str | Path | None = None):
        self.source = Path(source) if source is not None else None
        self.metadata: SnapshotMetadata | None = None

    def builtins(self) -> list[PolicyDefinition]:
        if self.source is not None:
            with self.source.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            asset = resources.files(_ASSET_PACKAGE).joinpath(_ASSET_NAME)
            with asset.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

        policies, metadata = validate_snapshot(payload)
        self.metadata = metadata
        return [PolicyDefinition.from_dict(item) for item in policies]


def validate_snapshot(payload: object) -> tuple[list[dict], SnapshotMetadata]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("catalogue snapshot must use schema version 1")
    if payload.get("provider") != "azure":
        raise ValueError("catalogue snapshot provider must be azure")

    source = payload.get("source")
    policies = payload.get("policies")
    if not isinstance(source, dict) or not isinstance(policies, list):
        raise ValueError("catalogue snapshot source and policies are required")

    repository = source.get("repository")
    commit = source.get("commit")
    generated_at = source.get("generated_at")
    if repository != "https://github.com/Azure/azure-policy":
        raise ValueError("catalogue snapshot source repository is not trusted")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("catalogue snapshot source commit must be a full Git SHA")
    if not isinstance(generated_at, str):
        raise ValueError("catalogue snapshot generation date is required")
    try:
        date.fromisoformat(generated_at)
    except ValueError as exc:
        raise ValueError("catalogue snapshot generation date must use YYYY-MM-DD") from exc

    expected_count = payload.get("policy_count")
    if expected_count != len(policies) or not policies:
        raise ValueError("catalogue snapshot policy count does not match its contents")
    for item in policies:
        _validate_policy(item)

    expected_digest = payload.get("content_sha256")
    actual_digest = snapshot_digest(policies)
    if expected_digest != actual_digest:
        raise ValueError("catalogue snapshot checksum does not match its contents")

    metadata = SnapshotMetadata(
        source_repository=repository,
        source_commit=commit,
        generated_at=generated_at,
        policy_count=len(policies),
        content_sha256=actual_digest,
    )
    return policies, metadata


def snapshot_digest(policies: list[dict]) -> str:
    canonical = json.dumps(
        policies,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_snapshot(source: Path, source_commit: str, generated_at: str) -> dict:
    from .azure import AzurePolicyCatalogue, normalize_definition

    definitions_root = source / "built-in-policies" / "policyDefinitions"
    if not definitions_root.is_dir():
        raise ValueError("source does not contain Azure built-in policy definitions")

    catalogue = AzurePolicyCatalogue()
    policies_by_id = {}
    excluded_invalid_ids = 0
    for path in sorted(definitions_root.rglob("*.json")):
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        definition = normalize_definition(raw)
        if not _is_valid_policy_id(definition.id):
            excluded_invalid_ids += 1
            continue
        if (
            definition.display_name.startswith("[Deprecated]")
            or definition.effect.lower() == "manual"
            or not catalogue._can_audit(definition)
        ):
            continue
        policies_by_id[definition.id] = {
            "id": definition.id,
            "display_name": definition.display_name,
            "description": definition.description,
            "category": definition.category,
            "policy_type": definition.policy_type,
            "effect": definition.effect,
            "effect_allowed_values": definition.effect_allowed_values,
            "parameters": definition.parameters,
        }

    policies = [policies_by_id[key] for key in sorted(policies_by_id)]
    payload = {
        "schema_version": 1,
        "provider": "azure",
        "source": {
            "repository": _REPOSITORY,
            "commit": source_commit,
            "generated_at": generated_at,
            "license": "MIT",
            "excluded_invalid_policy_ids": excluded_invalid_ids,
        },
        "policy_count": len(policies),
        "content_sha256": snapshot_digest(policies),
        "policies": policies,
    }
    validate_snapshot(payload)
    return payload


def _validate_policy(item: object) -> None:
    if not isinstance(item, dict):
        raise ValueError("catalogue snapshot policies must be objects")
    policy_id = item.get("id")
    display_name = item.get("display_name")
    if not isinstance(policy_id, str) or not isinstance(display_name, str) or not display_name:
        raise ValueError("catalogue snapshot policies require an id and display name")
    if not _is_valid_policy_id(policy_id):
        raise ValueError(f"catalogue snapshot contains an invalid Azure policy id: {policy_id!r}")


def _is_valid_policy_id(policy_id: object) -> bool:
    if not isinstance(policy_id, str):
        return False
    match = _POLICY_ID.fullmatch(policy_id)
    if match is None:
        return False
    try:
        UUID(match.group(1))
    except ValueError:
        return False
    return True

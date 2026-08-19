import json

import pytest

from control_translator.catalogue.base import get_catalogue
from control_translator.catalogue.snapshot import (
    BundledPolicyCatalogue,
    build_snapshot,
    snapshot_digest,
    validate_snapshot,
)

_COMMIT = "a" * 40
_POLICY_ID = "/providers/Microsoft.Authorization/policyDefinitions/82067dbb-e53b-4e06-b631-546d197452d9"


def _raw_policy(*, policy_id=_POLICY_ID, name="Audit secure storage", deprecated=False):
    return {
        "id": policy_id,
        "name": policy_id.rsplit("/", 1)[-1],
        "properties": {
            "displayName": f"[Deprecated]: {name}" if deprecated else name,
            "description": "Audit storage settings.",
            "policyType": "BuiltIn",
            "metadata": {"category": "Storage"},
            "parameters": {
                "effect": {
                    "defaultValue": "Audit",
                    "allowedValues": ["Audit", "Disabled"],
                }
            },
            "policyRule": {"then": {"effect": "[parameters('effect')]"}},
        },
    }


def test_builds_and_loads_a_valid_production_snapshot(tmp_path):
    definitions = tmp_path / "azure-policy" / "built-in-policies" / "policyDefinitions" / "Storage"
    definitions.mkdir(parents=True)
    (definitions / "audit.json").write_text(json.dumps(_raw_policy()), encoding="utf-8")
    (definitions / "deprecated.json").write_text(
        json.dumps(_raw_policy(policy_id="/providers/Microsoft.Authorization/policyDefinitions/efbde977-ba53-4479-b8e9-10b53ee64c09", deprecated=True)),
        encoding="utf-8",
    )
    (definitions / "invalid.json").write_text(
        json.dumps(_raw_policy(policy_id="/providers/Microsoft.Authorization/policyDefinitions/not-a-guid")),
        encoding="utf-8",
    )

    payload = build_snapshot(tmp_path / "azure-policy", _COMMIT, "2026-08-14")
    snapshot = tmp_path / "azure-builtins.json"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    catalogue = BundledPolicyCatalogue(snapshot)
    policies = catalogue.builtins()

    assert [policy.id for policy in policies] == [_POLICY_ID]
    assert payload["source"]["excluded_invalid_policy_ids"] == 1
    assert catalogue.metadata is not None
    assert catalogue.metadata.source_commit == _COMMIT
    assert isinstance(get_catalogue("bundled", str(snapshot)), BundledPolicyCatalogue)


def test_rejects_tampered_or_placeholder_snapshots(tmp_path):
    definitions = tmp_path / "azure-policy" / "built-in-policies" / "policyDefinitions"
    definitions.mkdir(parents=True)
    (definitions / "audit.json").write_text(json.dumps(_raw_policy()), encoding="utf-8")
    payload = build_snapshot(tmp_path / "azure-policy", _COMMIT, "2026-08-14")

    payload["policies"][0]["display_name"] = "Tampered"
    with pytest.raises(ValueError, match="checksum"):
        validate_snapshot(payload)

    placeholder = build_snapshot(tmp_path / "azure-policy", _COMMIT, "2026-08-14")
    placeholder["policies"][0]["id"] = "11111111-1111-1111-1111-111111111111"
    with pytest.raises(ValueError, match="invalid Azure policy id"):
        validate_snapshot(placeholder)


def test_rejects_duplicate_policy_ids(tmp_path):
    definitions = tmp_path / "azure-policy" / "built-in-policies" / "policyDefinitions"
    definitions.mkdir(parents=True)
    (definitions / "audit.json").write_text(json.dumps(_raw_policy()), encoding="utf-8")
    payload = build_snapshot(tmp_path / "azure-policy", _COMMIT, "2026-08-14")
    payload["policies"].append(dict(payload["policies"][0]))
    payload["policy_count"] = len(payload["policies"])
    payload["content_sha256"] = snapshot_digest(payload["policies"])

    with pytest.raises(ValueError, match="duplicate policy id"):
        validate_snapshot(payload)


def test_release_contains_a_production_catalogue_snapshot():
    catalogue = BundledPolicyCatalogue()
    policies = catalogue.builtins()

    assert len(policies) > 2000
    assert catalogue.metadata is not None
    assert catalogue.metadata.schema_version == 1
    assert catalogue.metadata.generated_at == "2026-08-14"

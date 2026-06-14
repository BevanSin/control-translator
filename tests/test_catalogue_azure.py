"""Tests for the Azure catalogue's pure parts (normalisation + cache).

The live ARM pull needs credentials + network; these tests cover the deterministic
layer by feeding a synthetic ARM policyDefinition payload and a stubbed pull.
"""
import json

from control_translator.catalogue.azure import normalize_definition, AzurePolicyCatalogue

_RAW = {
    "id": "/providers/Microsoft.Authorization/policyDefinitions/abc-123",
    "name": "abc-123",
    "properties": {
        "displayName": "Storage accounts should use customer-managed key",
        "description": "Audit storage accounts not using CMK.",
        "policyType": "BuiltIn",
        "metadata": {"category": "Storage"},
        "parameters": {"effect": {"type": "String"}},
    },
}


def test_normalize_definition():
    d = normalize_definition(_RAW)
    assert d.id == "/providers/Microsoft.Authorization/policyDefinitions/abc-123"
    assert d.display_name.startswith("Storage accounts")
    assert d.category == "Storage"
    assert d.policy_type == "BuiltIn"
    assert "effect" in d.parameters


def test_excludes_deprecated_and_writes_cache(tmp_path, monkeypatch):
    cache = str(tmp_path / "builtins.json")
    cat = AzurePolicyCatalogue(cache=cache)

    deprecated = {"name": "old", "properties": {"displayName": "[Deprecated]: old policy"}}
    monkeypatch.setattr(cat, "_pull_live", lambda: [_RAW, deprecated])

    defs = cat.builtins()
    assert [d.id for d in defs] == [_RAW["id"]]          # deprecated filtered out

    # cache written in the offline-compatible shape, and reused on the next call
    written = json.load(open(cache))
    assert written[0]["category"] == "Storage"
    reused = AzurePolicyCatalogue(cache=cache).builtins()  # no _pull_live needed
    assert reused[0].id == _RAW["id"]

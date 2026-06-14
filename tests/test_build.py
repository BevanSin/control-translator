"""Build-layer tests — verifies the v3.8-aligned initiative structure."""
import json

from control_translator.models import Catalog, Group, Control, MappingSet, ControlMapping, PolicyRef, Decision
from control_translator.build.azure import AzurePolicySetBuilder, _padded_category


def _fixture():
    ctrl = Control(id="06.2.5.C.01", title="Vulnerability management",
                   prose="Agencies SHOULD conduct vulnerability assessments.",
                   family="6. Information security monitoring",
                   props={"compliance": "Should"})
    catalog = Catalog(uuid="u", title="NZISM", version="3.9",
                      groups=[Group(id="06", title="6. Information security monitoring",
                                    controls=[ctrl])])
    mapping = MappingSet(framework_id="nzism", version="3.9", mappings={
        "06.2.5.C.01": ControlMapping(
            control_id="06.2.5.C.01", decision=Decision.INCLUDE,
            policies=[PolicyRef(policy_id="/providers/Microsoft.Authorization/policyDefinitions/82067dbb-e53b-4e06-b631-546d197452d9",
                                display_name="Keys using RSA cryptography should have a minimum key size")])})
    return catalog, mapping


def test_padded_category():
    assert _padded_category("6. Information security monitoring") == "06. Information security monitoring"
    assert _padded_category("16. Access control and passwords") == "16. Access control and passwords"


def test_build_matches_v38_structure():
    catalog, mapping = _fixture()
    overrides = {"82067dbb-e53b-4e06-b631-546d197452d9": {
        "minimumRSAKeySize": {"initiative_param": "minimumRSAKeySize-1",
                              "definition": {"type": "Integer", "defaultValue": 2048}}}}
    framework = {"id": "nzism", "version": "3.9", "short_name": "NZISM",
                 "display_name": "New Zealand ISM"}
    options = {"type": "azure", "initiative_version": "1.0.0",
               "group_prefix": "New_Zealand_ISM_", "parameter_overrides": overrides}
    oos = [{"policy_id": "x", "reason": "too hard", "oos_date": "2025-09-03"}]

    bundle = AzurePolicySetBuilder().build(catalog, mapping, framework=framework,
                                           options=options, oos=oos)
    ps = json.loads(bundle.files["policySet.json"])["properties"]

    # version model: semver in metadata only (top-level `version` is not a settable
    # property on the policySetDefinitions resource type); ISM version in description
    assert ps["metadata"]["version"] == "1.0.0" and "version" not in ps
    assert ps["description"].startswith("NZISM v3.9.")

    # group: prefixed name, zero-padded chapter category, control text as description
    g = ps["policyDefinitionGroups"][0]
    assert g["name"] == "New_Zealand_ISM_06.2.5.C.01"
    assert g["category"] == "06. Information security monitoring"
    assert g["description"].startswith("Agencies SHOULD conduct")

    # policy ref: reference id = display name; parameter wired to a top-level parameter
    d = ps["policyDefinitions"][0]
    assert d["policyDefinitionReferenceId"].startswith("Keys using RSA")
    assert d["parameters"]["minimumRSAKeySize"]["value"] == "[parameters('minimumRSAKeySize-1')]"
    assert ps["parameters"]["minimumRSAKeySize-1"]["defaultValue"] == 2048

    # out-of-scope register is published
    assert "out-of-scope.json" in bundle.files
    assert json.loads(bundle.files["out-of-scope.json"])[0]["reason"] == "too hard"

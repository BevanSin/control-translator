"""Offline end-to-end smoke test: ingest -> map -> build -> lint -> distribute (local)."""
import json
import os

from control_translator.config import load_config, resolve
from control_translator.pipeline import run_pipeline

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sample_config(tmp_out: str) -> dict:
    path = os.path.join(REPO_ROOT, "config", "sample.json")
    config = resolve(load_config(path), REPO_ROOT)
    # isolate side effects into the test's temp area
    config["out_dir"] = tmp_out
    config["mapping"]["store"] = os.path.join(tmp_out, "mapping.json")
    return config


def test_pipeline_builds_and_publishes(tmp_path):
    config = _sample_config(str(tmp_path))
    result = run_pipeline(config)

    # the high-overlap control is auto-approved; the low-overlap one waits for review
    approved_ids = {m.control_id for m in result.mapping.approved()}
    pending_ids = {m.control_id for m in result.mapping.pending_review()}
    assert "SAMPLE-DP-1" in approved_ids
    assert "SAMPLE-LM-1" in pending_ids

    # a valid, lint-clean initiative was produced
    assert result.lint_errors == []
    policy_set = json.loads(result.bundle.files["policySet.json"])
    props = policy_set["properties"]
    assert props["policyType"] == "Custom"
    assert props["metadata"]["category"] == "Regulatory Compliance"
    assert any(g["name"] == "SAMPLE-DP-1" for g in props["policyDefinitionGroups"])

    # the bundle was written to disk by the local adapter
    assert result.published_to and os.path.exists(
        os.path.join(result.published_to, "policySet.json"))


def test_mapping_carry_forward(tmp_path):
    """Second run reuses the stored decision instead of re-proposing."""
    config = _sample_config(str(tmp_path))
    run_pipeline(config)
    second = run_pipeline(config)
    assert "SAMPLE-DP-1" in {m.control_id for m in second.mapping.approved()}

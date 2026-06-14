"""Offline tests for the agentic mapper (retrieval + heuristic classifier).

The real classifier (AnthropicClassifier) needs network + an API key; these tests
exercise the retrieval shortlist and the retrieve->classify->propose wiring with the
offline heuristic stand-in, so CI can run without credentials.
"""
import os

from control_translator.config import load_config, resolve
from control_translator.pipeline import run_pipeline
from control_translator.catalogue import PolicyDefinition
from control_translator.mapping.retrieval import TfidfRetriever

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_tfidf_retriever_ranks_relevant_policy_first():
    policies = [
        PolicyDefinition(id="enc", display_name="Storage encryption at rest with customer keys"),
        PolicyDefinition(id="https", display_name="App Service apps should require HTTPS"),
        PolicyDefinition(id="logs", display_name="Resource diagnostic logs to a workspace"),
    ]
    r = TfidfRetriever()
    r.fit(policies)
    ranked = r.query("encryption at rest for storage", k=3)
    assert ranked, "expected at least one match"
    assert ranked[0][0].id == "enc"


def test_agentic_pipeline_offline(tmp_path):
    path = os.path.join(REPO_ROOT, "config", "sample-agentic.json")
    config = resolve(load_config(path), REPO_ROOT)
    config["out_dir"] = str(tmp_path)
    config["mapping"]["store"] = os.path.join(str(tmp_path), "mapping.json")

    result = run_pipeline(config)

    # retrieval + heuristic classify reproduces the baseline split:
    # the encryption control gets built-in coverage; the low-signal one waits for review
    assert "SAMPLE-DP-1" in {m.control_id for m in result.mapping.approved()}
    assert "SAMPLE-LM-1" in {m.control_id for m in result.mapping.pending_review()}
    assert result.lint_errors == []
    assert result.published_to and os.path.exists(
        os.path.join(result.published_to, "policySet.json"))

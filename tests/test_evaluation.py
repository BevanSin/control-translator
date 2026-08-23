"""Evaluation harness: gold set extraction, alignment, and scoring."""
import json

import pytest

from control_translator.evaluation.goldset import align_gold_set, load_gold_set
from control_translator.evaluation.metrics import (
    recall_at_k,
    rejected_policy_hits,
    score_selection,
)
from control_translator.evaluation.runner import evaluate

_PREFIX = "/providers/Microsoft.Authorization/policyDefinitions/"
_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
_C = "cccccccc-3333-4333-8333-cccccccccccc"


def _initiative(tmp_path, pairs: dict[str, list[str]]):
    groups = [{"name": f"New_Zealand_ISM_{cid}"} for cid in pairs]
    definitions = [
        {
            "policyDefinitionReferenceId": f"policy {guid[:4]}",
            "policyDefinitionId": f"{_PREFIX}{guid.upper()}",
            "groupNames": [f"New_Zealand_ISM_{cid}"],
        }
        for cid, guids in pairs.items()
        for guid in guids
    ]
    path = tmp_path / "initiative.json"
    path.write_text(json.dumps({
        "properties": {"policyDefinitionGroups": groups,
                       "policyDefinitions": definitions}
    }), encoding="utf-8")
    return str(path)


def test_gold_set_normalises_policy_ids_and_groups(tmp_path):
    path = _initiative(tmp_path, {"06.2.5.C.01": [_A, _B], "07.1.7.C.02": [_C]})

    gold = load_gold_set(path, group_prefix="New_Zealand_ISM_")

    assert gold.control_count == 2
    assert gold.pair_count == 3
    # Published ids are upper-case ARM paths; comparison needs bare lowercase guids.
    assert gold.pairs["06.2.5.C.01"] == frozenset({_A, _B})


def test_alignment_separates_framework_and_catalogue_drift(tmp_path):
    path = _initiative(tmp_path, {"06.2.5.C.01": [_A, _B], "99.9.9.C.99": [_C]})
    gold = load_gold_set(path, group_prefix="New_Zealand_ISM_")

    aligned = align_gold_set(gold, control_ids={"06.2.5.C.01"},
                             catalogue_policy_ids={_A})

    # The withdrawn control is reported, not counted as a miss.
    assert aligned.missing_controls == ("99.9.9.C.99",)
    # Policies the catalogue cannot serve are reported so the ceiling is visible.
    assert set(aligned.unreachable_policies) == {_B, _C}
    assert aligned.pairs == {"06.2.5.C.01": frozenset({_A})}
    assert aligned.published_pair_count == 3
    assert aligned.pair_count == 1


def test_recall_at_k_counts_only_the_shortlist_head():
    gold = {"c1": frozenset({_A, _B}), "c2": frozenset({_C})}
    shortlists = {"c1": [_A, "zzz", _B], "c2": ["zzz", _C]}

    assert recall_at_k(shortlists, gold, 1).matched_pairs == 1
    # k=2 reaches _C in c2 but still not _B in c1.
    assert recall_at_k(shortlists, gold, 2).matched_pairs == 2

    full = recall_at_k(shortlists, gold, 3)
    assert full.matched_pairs == 3
    assert full.total_pairs == 3
    assert full.recall == 1.0
    assert full.controls_fully_covered == 2


def test_recall_at_k_handles_controls_with_no_shortlist():
    result = recall_at_k({}, {"c1": frozenset({_A})}, 12)

    assert result.matched_pairs == 0
    assert result.recall == 0.0
    assert result.controls_fully_covered == 0


def test_selection_score_reports_precision_recall_and_f1():
    gold = {"c1": frozenset({_A, _B})}
    selected = {"c1": {_A, _C}}

    score = score_selection(selected, gold)

    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 1, 1)
    assert score.precision == 0.5
    assert score.recall == 0.5
    assert score.f1 == 0.5


def test_selection_score_is_zero_rather_than_undefined_when_nothing_selected():
    score = score_selection({}, {"c1": frozenset({_A})})

    assert score.precision == 0.0
    assert score.recall == 0.0
    assert score.f1 == 0.0


def test_rejected_policy_hits_flags_previously_rejected_proposals():
    result = rejected_policy_hits({"c1": {_A, _B}, "c2": {_C}}, {_B})

    assert result["controls_proposing_rejected"] == 1
    assert result["total_rejected_proposals"] == 1
    assert result["detail"] == {"c1": [_B]}


def test_evaluate_rejects_engines_without_a_retriever(tmp_path):
    path = _initiative(tmp_path, {"SAMPLE-DP-1": [_A]})
    config = {
        "framework": {"id": "sample", "version": "1.0"},
        "ingest": {"type": "fixture", "source": "tests/fixtures/sample_catalogue.json"},
        "catalogue": {"type": "offline", "source": "tests/fixtures/sample_builtins.json"},
        "mapping": {"engine": "keyword"},
    }

    with pytest.raises(ValueError, match="no retriever"):
        evaluate(config, initiative_path=path)


def test_evaluate_scores_the_sample_fixture_offline(tmp_path):
    builtins = json.load(open("tests/fixtures/sample_builtins.json", encoding="utf-8"))
    first = builtins[0]["id"].rstrip("/").split("/")[-1]
    path = _initiative(tmp_path, {"SAMPLE-DP-1": [first]})

    config = {
        "framework": {"id": "sample", "version": "1.0"},
        "ingest": {"type": "fixture", "source": "tests/fixtures/sample_catalogue.json"},
        "catalogue": {"type": "offline", "source": "tests/fixtures/sample_builtins.json"},
        "mapping": {"engine": "agentic", "classifier": "heuristic",
                    "retrieval": "tfidf", "top_k": 5},
    }

    report = evaluate(config, initiative_path=path, ks=(1, 5),
                      group_prefix="New_Zealand_ISM_")

    assert report["gold_set"]["evaluated_controls"] == 1
    assert report["mapping"]["classifier"] == "heuristic"
    assert [row["k"] for row in report["recall_at_k"]] == [1, 5]
    assert report["recall_at_k"][-1]["total_pairs"] == 1
    assert report["per_control"][0]["control_id"] == "SAMPLE-DP-1"
    assert "precision" in report["selection"]

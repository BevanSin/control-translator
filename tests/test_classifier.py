"""Classifier factory tests — no network (LLM clients are constructed lazily)."""
import pytest

from control_translator.mapping.classifier import (
    get_classifier, HeuristicClassifier, AnthropicClassifier, FoundryClassifier)


def test_factory_returns_expected_types():
    assert isinstance(get_classifier("heuristic"), HeuristicClassifier)
    assert isinstance(get_classifier("anthropic"), AnthropicClassifier)
    assert isinstance(
        get_classifier("foundry", base_url="https://r.services.ai.azure.com/anthropic"),
        FoundryClassifier)


def test_unknown_classifier_raises():
    with pytest.raises(ValueError):
        get_classifier("gpt")


def test_foundry_requires_base_url(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_BASE_URL", raising=False)
    with pytest.raises(ValueError):
        get_classifier("foundry")  # no base_url and no env -> clear error


def test_foundry_deployment_and_auth_config():
    c = get_classifier("foundry", model="my-claude-deployment", effort="high",
                       base_url="https://r.services.ai.azure.com/anthropic",
                       api_key="k")
    assert c.model == "my-claude-deployment" and c.effort == "high" and c.api_key == "k"

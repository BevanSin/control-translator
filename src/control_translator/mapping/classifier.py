"""Classification stage: judge whether each shortlisted policy satisfies a control.

Five classifiers (config `mapping.classifier`):
  - `heuristic`       — offline token-overlap stand-in. No LLM, no cost, no signup.
  - `anthropic`       — Claude via the first-party Anthropic API (`ANTHROPIC_API_KEY`).
  - `foundry`         — Claude via Azure AI Foundry (`ANTHROPIC_FOUNDRY_BASE_URL`).
  - `azure-openai`    — GPT-4o / GPT-4o-mini via Azure OpenAI (recommended if no Claude
                        quota). Keyless via az login or `AZURE_OPENAI_API_KEY`.
  - `azure-inference` — Phi-4, Llama, Mistral, etc. via Azure AI Inference serverless
                        endpoint on Foundry. Good "no Azure OpenAI" alternative.

Each assessment now carries an `oos_candidate` flag: when the classifier recognises that
a policy requires globally-problematic implementation (In-Guest agent, customer-specific
allow-lists, unsupported protocol versions, etc.) it marks the policy as a candidate for
the out-of-scope register — even if the policy is relevant to the control. The pipeline
collects these across all controls and emits an `oos-candidates.json` suggestion file
the reviewer can promote into `global-ignore.json`.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ._text import tokenize, jaccard
from ..models import Control
from ..catalogue import PolicyDefinition


@dataclass
class Assessment:
    index: int
    relevant: bool
    confidence: float
    rationale: str
    oos_candidate: bool = False     # should this policy go on the global OOS register?
    oos_reason: str = ""            # reason it's globally infeasible/out-of-scope


class Classifier(ABC):
    def set_oos_context(self, oos: list[dict]) -> None:
        """Supply the existing OOS register so the classifier can apply the same patterns."""

    @abstractmethod
    def classify(self, control: Control,
                 shortlist: list[PolicyDefinition]) -> list[Assessment]:
        ...


def get_classifier(kind: str, *, model: str = "claude-opus-4-7", effort: str = "medium",
                   base_url: str | None = None, api_key: str | None = None,
                   api_version: str | None = None) -> Classifier:
    if kind == "heuristic":
        return HeuristicClassifier()
    if kind == "anthropic":
        return AnthropicClassifier(model=model, effort=effort)
    if kind == "foundry":
        return FoundryClassifier(base_url=base_url, model=model, effort=effort,
                                 api_key=api_key)
    if kind == "azure-openai":
        return AzureOpenAIClassifier(endpoint=base_url, deployment=model,
                                     api_version=api_version, api_key=api_key)
    if kind == "azure-inference":
        return AzureInferenceClassifier(endpoint=base_url, model=model, api_key=api_key)
    raise ValueError(f"unknown classifier: {kind!r}")


class HeuristicClassifier(Classifier):
    """Offline stand-in: confidence = token overlap; relevant if any overlap."""

    def classify(self, control: Control,
                 shortlist: list[PolicyDefinition]) -> list[Assessment]:
        ctrl = tokenize(f"{control.title} {control.prose}")
        return [Assessment(
            index=i, relevant=(score := jaccard(ctrl, tokenize(f"{p.display_name} {p.description}"))) > 0.0,
            confidence=round(score, 3),
            rationale=f"offline heuristic (token overlap {score:.2f})")
            for i, p in enumerate(shortlist)]


_SYSTEM = """You are a policy mapping assistant. Your task is to assess how well \
Azure built-in cloud governance policies align with security control requirements.

For each candidate policy provided, evaluate whether it materially enforces or audits \
a technical requirement of the given security control. Base your assessment only on \
the control text and the policy name and description provided — do not make assumptions \
about policy behaviour beyond what is stated.

Relevance assessment:
- Mark relevant=true when the policy checks or enforces something the control actually \
requires. A loose topical match is not sufficient.
- Calibrate confidence between 0 and 1: use high confidence only when the policy \
clearly addresses the control; use low confidence when the link is partial or uncertain.
- Provide a one-sentence rationale citing the specific overlap or gap.

Out-of-scope (OOS) assessment:
The oos_candidate flag identifies policies that are structurally unsuitable for a shared \
platform initiative regardless of which control is being assessed. This is a judgment \
about the policy itself, not about whether it matches the current control. A policy \
that is technically irrelevant to the current control should be marked relevant=false, \
but should only be marked oos_candidate=true if it also has a structural reason for \
being excluded from any context.

Structural reasons for marking a policy as an OOS candidate:
- The policy requires each organisation to supply environment-specific values that \
cannot be set generically in a shared policy set (e.g. approved image names, \
account names, package lists, namespace allow-lists)
- The policy requires a pre-deployed agent or extension (e.g. machine configuration, \
monitoring agent) that each organisation must independently deploy
- The policy describes an organisational obligation that cannot be automatically \
evaluated by cloud policy (i.e. a process or management control)
- The policy references a technology or protocol version not universally available \
across all environments
When the existing OOS list is shown, apply the same structural reasoning to identify \
similar candidates. An OOS candidate may still be marked relevant=true — the two \
assessments are independent. Set oos_reason to one sentence describing the structural \
reason, not the relevance to the current control.

Respond with ONLY a JSON object: \
{"assessments": [{"index": int, "relevant": bool, "confidence": number, \
"rationale": string, "oos_candidate": bool, "oos_reason": string}]}"""

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index":         {"type": "integer"},
                    "relevant":      {"type": "boolean"},
                    "confidence":    {"type": "number"},
                    "rationale":     {"type": "string"},
                    "oos_candidate": {"type": "boolean"},
                    "oos_reason":    {"type": "string"},
                },
                "required": ["index", "relevant", "confidence", "rationale",
                             "oos_candidate", "oos_reason"],
            },
        }
    },
    "required": ["assessments"],
}


def _user_message(control: Control, shortlist: list[PolicyDefinition],
                  oos_context: list[dict] | None = None) -> str:
    strength = control.props.get("compliance") or control.props.get("classification") or ""
    candidates = "\n".join(
        f"[{i}] {p.display_name}\n    {p.description}" for i, p in enumerate(shortlist))
    oos_section = ""
    if oos_context:
        lines = "\n".join(
            f"  - {r.get('display_name') or r.get('policy_id','')}: {r.get('reason','')}"
            for r in oos_context if r.get("reason"))
        if lines:
            oos_section = (f"\nEXISTING OUT-OF-SCOPE POLICIES (apply same reasoning to "
                           f"similar candidates):\n{lines}\n")
    return (f"CONTROL {control.id} ({strength})\n"
            f"Title: {control.title}\n"
            f"Requirement: {control.prose}"
            f"{oos_section}\n\n"
            f"CANDIDATE BUILT-IN POLICIES:\n{candidates}")


def _assess(client, model: str, effort: str, control: Control,
            shortlist: list[PolicyDefinition],
            oos_context: list[dict] | None = None) -> list[Assessment]:
    """Shared request/parse for any Anthropic Messages-API client."""
    user = _user_message(control, shortlist, oos_context)

    def call(structured: bool, cache: bool):
        oc = {"effort": effort}
        if structured:
            oc["format"] = {"type": "json_schema", "schema": _SCHEMA}
        sys_block = {"type": "text", "text": _SYSTEM}
        if cache:
            sys_block["cache_control"] = {"type": "ephemeral"}
        return client.messages.create(
            model=model, max_tokens=16000, thinking={"type": "adaptive"},
            output_config=oc, system=[sys_block],
            messages=[{"role": "user", "content": user}])

    try:
        response = call(structured=True, cache=True)
    except Exception:
        response = call(structured=False, cache=False)

    if getattr(response, "stop_reason", None) == "refusal":
        return []
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    out = []
    for a in data.get("assessments", []):
        idx = a.get("index")
        if isinstance(idx, int) and 0 <= idx < len(shortlist):
            out.append(Assessment(
                index=idx, relevant=bool(a.get("relevant")),
                confidence=float(a.get("confidence", 0.0)),
                rationale=str(a.get("rationale", "")),
                oos_candidate=bool(a.get("oos_candidate", False)),
                oos_reason=str(a.get("oos_reason", ""))))
    return out


class AnthropicClassifier(Classifier):
    def __init__(self, model: str = "claude-opus-4-7", effort: str = "medium"):
        self.model = model
        self.effort = effort
        self._client = None
        self._oos: list[dict] = []

    def set_oos_context(self, oos: list[dict]) -> None:
        self._oos = oos or []

    def _client_lazy(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def classify(self, control, shortlist):
        if not shortlist:
            return []
        return _assess(self._client_lazy(), self.model, self.effort,
                       control, shortlist, self._oos or None)


class FoundryClassifier(Classifier):
    """Claude via Azure AI Foundry — billed to your Azure subscription."""

    def __init__(self, base_url: str | None, model: str = "claude-opus-4-7",
                 effort: str = "medium", api_key: str | None = None):
        if not base_url:
            base_url = os.environ.get("ANTHROPIC_FOUNDRY_BASE_URL")
        if not base_url:
            raise ValueError(
                "Foundry classifier needs a base_url "
                "(https://<resource>.services.ai.azure.com/anthropic) — set "
                "mapping.foundry_base_url or ANTHROPIC_FOUNDRY_BASE_URL.")
        self.base_url = base_url
        self.model = model
        self.effort = effort
        self.api_key = api_key or os.environ.get("ANTHROPIC_FOUNDRY_API_KEY") \
            or os.environ.get("AZURE_API_KEY")
        self._client = None
        self._oos: list[dict] = []

    def set_oos_context(self, oos: list[dict]) -> None:
        self._oos = oos or []

    def _client_lazy(self):
        if self._client is None:
            from anthropic import AnthropicFoundry
            if self.api_key:
                self._client = AnthropicFoundry(api_key=self.api_key, base_url=self.base_url)
            else:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                provider = get_bearer_token_provider(
                    DefaultAzureCredential(), "https://ai.azure.com/.default")
                self._client = AnthropicFoundry(
                    azure_ad_token_provider=provider, base_url=self.base_url)
        return self._client

    def classify(self, control, shortlist):
        if not shortlist:
            return []
        return _assess(self._client_lazy(), self.model, self.effort,
                       control, shortlist, self._oos or None)


# ---------------------------------------------------------------------------
# Shared OpenAI-compatible parse helper
# ---------------------------------------------------------------------------

def _is_content_filter(exc: Exception) -> bool:
    """Return True when the exception is an Azure content filter rejection."""
    # openai.BadRequestError carries the response body; check for the filter code
    body = getattr(exc, "response", None)
    if body is not None:
        try:
            data = body.json() if callable(getattr(body, "json", None)) else {}
            err = data.get("error", {})
            if err.get("code") == "content_filter":
                return True
            inner = (err.get("innererror") or {})
            if inner.get("code") == "ResponsibleAIPolicyViolation":
                return True
        except Exception:
            pass
    # also catch by message text as a last resort
    return "content_filter" in str(exc).lower() or "content management policy" in str(exc).lower()


def _parse_assessments(text: str, shortlist_len: int) -> list[Assessment]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for a in data.get("assessments", []):
        idx = a.get("index")
        if isinstance(idx, int) and 0 <= idx < shortlist_len:
            out.append(Assessment(
                index=idx, relevant=bool(a.get("relevant")),
                confidence=float(a.get("confidence", 0.0)),
                rationale=str(a.get("rationale", "")),
                oos_candidate=bool(a.get("oos_candidate", False)),
                oos_reason=str(a.get("oos_reason", ""))))
    return out


def _chat_messages(control: Control, shortlist: list[PolicyDefinition],
                   oos_context: list[dict] | None) -> list[dict]:
    return [{"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _user_message(control, shortlist, oos_context)}]


# ---------------------------------------------------------------------------
# GPT-4o / GPT-4o-mini via Azure OpenAI
# ---------------------------------------------------------------------------

class AzureOpenAIClassifier(Classifier):
    """GPT-4o / GPT-4o-mini via Azure OpenAI or Azure AI Foundry — keyless (az login).

    Handles two endpoint shapes automatically:
      - Foundry OpenAI-compatible: https://<resource>.services.ai.azure.com/openai/v1
        Uses openai.OpenAI client + Entra ID bearer token (scope: ai.azure.com)
        Token is refreshed automatically when it nears expiry — safe for long runs.
      - Classic Azure OpenAI: https://<resource>.openai.azure.com/
        Uses openai.AzureOpenAI client + Entra ID or API key.

    Config keys:
      classifier:      azure-openai
      model:           <deployment-name>   e.g. gpt-4o-mini
      foundry_base_url: <endpoint-url>     either shape above
    Auth: AZURE_OPENAI_API_KEY env var, or keyless via az login.
    Install: pip install "openai>=1.50" azure-identity
    """

    def __init__(self, endpoint: str | None, deployment: str = "gpt-4o-mini",
                 api_version: str | None = None, api_key: str | None = None):
        if not endpoint:
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ValueError(
                "azure-openai classifier needs an endpoint — set mapping.foundry_base_url "
                "or AZURE_OPENAI_ENDPOINT.")
        self.endpoint = endpoint.rstrip("/")
        # detect Foundry OpenAI-compatible endpoint (.services.ai.azure.com)
        self._foundry = "services.ai.azure.com" in self.endpoint
        self.deployment = deployment
        self.api_version = api_version or ("2025-01-01-preview" if not self._foundry else None)
        self.api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
        self._client = None
        self._token_expires_at: float = 0.0
        self._credential = None
        self._oos: list[dict] = []

    def set_oos_context(self, oos: list[dict]) -> None:
        self._oos = oos or []

    def _get_client(self):
        import time
        if self._foundry:
            # Foundry: plain openai.OpenAI + bearer token; refresh before expiry
            now = time.time()
            if self._client is None or now > self._token_expires_at - 300:
                if self._credential is None:
                    from azure.identity import DefaultAzureCredential
                    self._credential = DefaultAzureCredential()
                tok = self._credential.get_token("https://ai.azure.com/.default")
                self._token_expires_at = float(tok.expires_on)
                from openai import OpenAI
                self._client = OpenAI(base_url=self.endpoint, api_key=tok.token)
        else:
            # Classic Azure OpenAI: AzureOpenAI client (built-in token refresh)
            if self._client is None:
                from openai import AzureOpenAI
                if self.api_key:
                    self._client = AzureOpenAI(
                        api_key=self.api_key, azure_endpoint=self.endpoint,
                        api_version=self.api_version)
                else:
                    if self._credential is None:
                        from azure.identity import DefaultAzureCredential
                        self._credential = DefaultAzureCredential()
                    from azure.identity import get_bearer_token_provider
                    self._client = AzureOpenAI(
                        azure_ad_token_provider=get_bearer_token_provider(
                            self._credential,
                            "https://cognitiveservices.azure.com/.default"),
                        azure_endpoint=self.endpoint,
                        api_version=self.api_version)
        return self._client

    def classify(self, control: Control, shortlist: list[PolicyDefinition]) -> list[Assessment]:
        if not shortlist:
            return []
        client = self._get_client()
        messages = _chat_messages(control, shortlist, self._oos or None)
        try:
            resp = client.chat.completions.create(
                model=self.deployment, messages=messages, max_tokens=2048,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "assessments", "strict": True,
                                                 "schema": _SCHEMA}})
        except Exception as exc:
            if _is_content_filter(exc):
                # Content filter hit — fall back to heuristic for this control
                import sys
                print(f"\n   ⚠  content filter: {control.id} — using heuristic fallback",
                      file=sys.stderr, flush=True)
                return HeuristicClassifier().classify(control, shortlist)
            # Other error (e.g. schema not supported) — retry without structured output
            resp = client.chat.completions.create(
                model=self.deployment, messages=messages, max_tokens=2048,
                response_format={"type": "json_object"})
        return _parse_assessments(resp.choices[0].message.content or "", len(shortlist))


# ---------------------------------------------------------------------------
# Phi-4, Llama, Mistral, etc. via Azure AI Inference serverless endpoint
# ---------------------------------------------------------------------------

class AzureInferenceClassifier(Classifier):
    """Any model on the Azure AI Inference serverless endpoint (Phi-4, Llama, Mistral).

    No dedicated deployment quota needed — serverless billing per token.
    Config keys:
      classifier: azure-inference
      model: Phi-4                    (or Llama-3.3-70B-Instruct etc.)
      foundry_base_url: https://<resource>.services.ai.azure.com/models
    Auth: AZURE_INFERENCE_API_KEY env var, or keyless via DefaultAzureCredential.
    Install: pip install azure-ai-inference azure-identity
    """

    def __init__(self, endpoint: str | None, model: str = "Phi-4",
                 api_key: str | None = None):
        if not endpoint:
            endpoint = os.environ.get("AZURE_INFERENCE_ENDPOINT")
        if not endpoint:
            raise ValueError(
                "azure-inference classifier needs an endpoint — set mapping.foundry_base_url "
                "or AZURE_INFERENCE_ENDPOINT to https://<resource>.services.ai.azure.com/models")
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get("AZURE_INFERENCE_API_KEY")
        self._client = None
        self._oos: list[dict] = []

    def set_oos_context(self, oos: list[dict]) -> None:
        self._oos = oos or []

    def _client_lazy(self):
        if self._client is None:
            from azure.ai.inference import ChatCompletionsClient
            if self.api_key:
                from azure.core.credentials import AzureKeyCredential
                self._client = ChatCompletionsClient(
                    endpoint=self.endpoint,
                    credential=AzureKeyCredential(self.api_key))
            else:
                from azure.identity import DefaultAzureCredential
                self._client = ChatCompletionsClient(
                    endpoint=self.endpoint,
                    credential=DefaultAzureCredential())
        return self._client

    def classify(self, control: Control, shortlist: list[PolicyDefinition]) -> list[Assessment]:
        if not shortlist:
            return []
        client = self._client_lazy()
        from azure.ai.inference.models import SystemMessage, UserMessage
        messages = [SystemMessage(content=_SYSTEM),
                    UserMessage(content=_user_message(control, shortlist, self._oos or None))]
        resp = client.complete(
            model=self.model, messages=messages, max_tokens=2048,
            response_format={"type": "json_object"})
        return _parse_assessments(resp.choices[0].message.content or "", len(shortlist))

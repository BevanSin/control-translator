"""Embedding-based retriever: semantic shortlisting using Azure OpenAI embeddings.

Replaces TF-IDF for the retrieval stage. Semantic search finds the right
policies even when there is no word overlap — e.g. "Defender for Containers"
is semantically related to "network monitoring" controls even though neither
phrase appears in the other.

Embeddings are cached to disk so only new/changed policies are re-embedded
on subsequent runs. The cache is a numpy binary (.npy) for fast load times.

Config (in mapping block):
    "retrieval":          "embedding"
    "embedding_model":    "text-embedding-3-small"   (default)
    "embedding_endpoint": "${AZURE_EMBEDDING_ENDPOINT}"   (if different from foundry_base_url)
    "embedding_cache":    "data/cache/embeddings"    (base path; .npy + .json appended)

If embedding_endpoint is not set, falls back to foundry_base_url.
Auth: keyless via az login (DefaultAzureCredential) or AZURE_OPENAI_API_KEY.
"""
from __future__ import annotations

import json
import os

from .retrieval import Retriever
from ..catalogue import PolicyDefinition

_DEFAULT_MODEL  = "text-embedding-3-small"
_DEFAULT_CACHE  = "data/cache/embeddings"
_BATCH_SIZE     = 100   # Azure OpenAI max per request


def _build_client(endpoint: str | None, api_key: str | None):
    """Build an OpenAI-compatible client for embeddings.

    Handles two endpoint shapes automatically:
    - Foundry (services.ai.azure.com/openai/v1): use openai.OpenAI with bearer token.
    - Classic Azure OpenAI (openai.azure.com): use AzureOpenAI with api_version.
    """
    ep = (endpoint or os.environ.get("AZURE_EMBEDDING_ENDPOINT") or
          os.environ.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
    if not ep:
        raise SystemExit(
            "Embedding retriever needs an endpoint. Set AZURE_EMBEDDING_ENDPOINT "
            "or mapping.embedding_endpoint in your config.")
    key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")

    if "services.ai.azure.com" in ep:
        # Foundry OpenAI-compatible endpoint — use plain openai.OpenAI + bearer token.
        # The endpoint already contains /openai/v1; AzureOpenAI would double-prefix it.
        from openai import OpenAI
        if key:
            return OpenAI(base_url=ep, api_key=key)
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        tok  = cred.get_token("https://ai.azure.com/.default")
        return OpenAI(base_url=ep, api_key=tok.token)
    else:
        # Classic Azure OpenAI endpoint (*.openai.azure.com)
        from openai import AzureOpenAI
        if key:
            return AzureOpenAI(api_key=key, azure_endpoint=ep,
                               api_version="2025-01-01-preview")
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        return AzureOpenAI(azure_ad_token_provider=provider, azure_endpoint=ep,
                           api_version="2025-01-01-preview")


class EmbeddingRetriever(Retriever):
    """Semantic retrieval using Azure OpenAI text embeddings.

    Produces cosine-similarity ranked candidates: the LLM then makes the
    final include/ignore decision. Much better than TF-IDF for cross-domain
    terminology (e.g. security control language vs Azure policy display names).
    """

    def __init__(self, *, endpoint: str | None = None, model: str = _DEFAULT_MODEL,
                 cache_base: str = _DEFAULT_CACHE, api_key: str | None = None):
        self.endpoint   = endpoint
        self.model      = model
        self.cache_base = cache_base
        self.api_key    = api_key
        self._client    = None
        self._policies: list[PolicyDefinition] = []
        self._matrix    = None   # numpy float32 (n_policies, dim), unit-normalised
        self._guids: list[str]  = []

    def _client_lazy(self):
        if self._client is None:
            self._client = _build_client(self.endpoint, self.api_key)
        return self._client

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        client = self._client_lazy()
        vecs: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i:i + _BATCH_SIZE]
            resp  = client.embeddings.create(input=batch, model=self.model)
            vecs.extend(d.embedding for d in resp.data)
        return vecs

    def _cache_paths(self):
        return self.cache_base + ".npy", self.cache_base + "_ids.json"

    def _save_cache(self, vecs_raw: list[list[float]], guids: list[str]) -> None:
        import numpy as np
        mat_path, ids_path = self._cache_paths()
        os.makedirs(os.path.dirname(mat_path) or ".", exist_ok=True)
        np.save(mat_path, np.array(vecs_raw, dtype=np.float32))
        with open(ids_path, "w", encoding="utf-8") as fh:
            json.dump(guids, fh)

    def _load_cache(self) -> tuple[list[list[float]], list[str]] | None:
        import numpy as np
        mat_path, ids_path = self._cache_paths()
        if not (os.path.exists(mat_path) and os.path.exists(ids_path)):
            return None
        mat = np.load(mat_path).tolist()
        with open(ids_path, encoding="utf-8") as fh:
            guids = json.load(fh)
        return mat, guids

    def fit(self, policies: list[PolicyDefinition]) -> None:
        import numpy as np
        import sys

        self._policies = policies
        self._guids    = [p.id.split("/")[-1].lower() for p in policies]

        # Try loading from cache
        cached = self._load_cache()
        if cached:
            vecs_raw, cached_guids = cached
            if cached_guids == self._guids:
                mat = np.array(vecs_raw, dtype=np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                self._matrix = mat / np.maximum(norms, 1e-9)
                print(f"  Embeddings loaded from cache ({len(policies)} policies)",
                      file=sys.stderr)
                return

        # Embed — may be slow on first run (~1900 policies = ~19 batches)
        print(f"  Embedding {len(policies)} policies "
              f"(model: {self.model}, batch_size: {_BATCH_SIZE})…",
              file=sys.stderr, end="", flush=True)
        texts   = [f"{p.display_name}. {p.description}" for p in policies]
        vecs_raw = self._embed_texts(texts)
        self._save_cache(vecs_raw, self._guids)

        mat  = np.array(vecs_raw, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        self._matrix = mat / np.maximum(norms, 1e-9)
        print(" done.", file=sys.stderr)

    def query(self, text: str, k: int) -> list[tuple[PolicyDefinition, float]]:
        import numpy as np
        if self._matrix is None or not self._policies:
            return []
        vecs = self._embed_texts([text])
        q    = np.array(vecs[0], dtype=np.float32)
        q   /= max(float(np.linalg.norm(q)), 1e-9)
        sims = self._matrix @ q                       # cosine similarity
        top  = np.argsort(sims)[-k:][::-1]
        return [(self._policies[int(i)], float(sims[i]))
                for i in top if sims[i] > 0]

"""Azure built-in policy catalogue — live pull via Azure Resource Manager.

Pulls built-in `Microsoft.Authorization/policyDefinitions` from ARM, normalises them
to `PolicyDefinition`, and caches the result to a JSON file (same shape the offline
catalogue reads) so subsequent runs are reproducible and don't re-hit ARM.

Auth uses `DefaultAzureCredential` (az CLI login, managed identity, env vars, ...).
Requires `pip install control-translator[azure]`. Built-in policy only for now.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .base import PolicyCatalogue, PolicyDefinition

_ARM = "https://management.azure.com"
_API_VERSION = "2021-06-01"


def _extract_effect(props: dict) -> str:
    """Extract the effective policy effect from the policyRule or parameters.

    Azure policies express their effect in one of two ways:
    - Direct:      policyRule.then.effect = "Manual"
    - Parameterised: policyRule.then.effect = "[parameters('effect')]"
                     with parameters.effect.defaultValue = "Manual"
    """
    rule = props.get("policyRule") or {}
    then = rule.get("then") or {}
    effect = then.get("effect", "")
    if effect.startswith("[parameters("):
        # Resolve via the parameter's default value
        param_name = effect.split("'")[1] if "'" in effect else "effect"
        param_def = (props.get("parameters") or {}).get(param_name) or {}
        effect = str(param_def.get("defaultValue") or "")
    return effect.strip()


def normalize_definition(raw: dict) -> PolicyDefinition:
    """Map one ARM policyDefinition object to a PolicyDefinition. Pure — unit-tested."""
    props = raw.get("properties", {}) or {}
    meta = props.get("metadata", {}) or {}
    return PolicyDefinition(
        id=raw.get("id") or raw.get("name", ""),
        display_name=props.get("displayName") or raw.get("name", ""),
        description=props.get("description", "") or "",
        category=meta.get("category", "") or "",
        policy_type=props.get("policyType", "BuiltIn"),
        effect=_extract_effect(props),
        parameters=props.get("parameters", {}) or {},
    )


class AzurePolicyCatalogue(PolicyCatalogue):
    def __init__(self, cache: str | None = None, *, subscription: str | None = None,
                 include_static: bool = False, exclude_deprecated: bool = True,
                 exclude_manual: bool = True, refresh: bool = False):
        self.cache = cache
        self.subscription = subscription
        self.include_static = include_static
        self.exclude_deprecated = exclude_deprecated
        self.exclude_manual = exclude_manual
        self.refresh = refresh

    def builtins(self) -> list[PolicyDefinition]:
        if self.cache and os.path.exists(self.cache) and not self.refresh:
            with open(self.cache, encoding="utf-8") as fh:
                return [PolicyDefinition.from_dict(d) for d in json.load(fh)]

        defs = [normalize_definition(r) for r in self._pull_live()]
        if self.exclude_deprecated:
            defs = [d for d in defs if not d.display_name.startswith("[Deprecated]")]
        if self.exclude_manual:
            defs = [d for d in defs if d.effect.lower() != "manual"]
        self._write_cache(defs)
        return defs

    def _write_cache(self, defs: list[PolicyDefinition]) -> None:
        if not self.cache:
            return
        os.makedirs(os.path.dirname(self.cache) or ".", exist_ok=True)
        payload = [{"id": d.id, "display_name": d.display_name, "description": d.description,
                    "category": d.category, "policy_type": d.policy_type,
                    "effect": d.effect, "parameters": d.parameters} for d in defs]
        with open(self.cache, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def _pull_live(self) -> list[dict]:
        from azure.identity import DefaultAzureCredential  # optional dependency

        token = DefaultAzureCredential().get_token(f"{_ARM}/.default").token
        ptypes = "policyType eq 'BuiltIn'"
        if self.include_static:
            ptypes = "(policyType eq 'BuiltIn' or policyType eq 'Static')"
        scope = f"/subscriptions/{self.subscription}" if self.subscription else ""
        url = (f"{_ARM}{scope}/providers/Microsoft.Authorization/policyDefinitions?"
               + urllib.parse.urlencode({"api-version": _API_VERSION, "$filter": ptypes}))

        out: list[dict] = []
        while url:
            page = self._get(url, token)
            out.extend(page.get("value", []))
            url = page.get("nextLink")
        return out

    @staticmethod
    def _get(url: str, token: str) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                   "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted ARM host)
            return json.loads(resp.read().decode("utf-8"))

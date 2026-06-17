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


def _extract_effect(props: dict) -> tuple[str, list[str]]:
    """Extract the policy effect default and allowed values.

    Returns (default_effect, allowed_values).

    Azure policies express their effect in one of two ways:
    - Direct:      policyRule.then.effect = "Audit"
    - Parameterised: policyRule.then.effect = "[parameters('effect')]"
                     with parameters.effect.defaultValue = "Modify"
                     and  parameters.effect.allowedValues = ["Audit","Modify","Disabled"]
    """
    rule = props.get("policyRule") or {}
    then = rule.get("then") or {}
    raw  = then.get("effect", "")
    if raw.startswith("[parameters("):
        param_name = raw.split("'")[1] if "'" in raw else "effect"
        param_def  = (props.get("parameters") or {}).get(param_name) or {}
        default    = str(param_def.get("defaultValue") or "").strip()
        allowed    = [str(v) for v in (param_def.get("allowedValues") or [])]
        return default, allowed
    return raw.strip(), []


def normalize_definition(raw: dict) -> PolicyDefinition:
    """Map one ARM policyDefinition object to a PolicyDefinition. Pure — unit-tested."""
    props = raw.get("properties", {}) or {}
    meta = props.get("metadata", {}) or {}
    effect, allowed = _extract_effect(props)
    return PolicyDefinition(
        id=raw.get("id") or raw.get("name", ""),
        display_name=props.get("displayName") or raw.get("name", ""),
        description=props.get("description", "") or "",
        category=meta.get("category", "") or "",
        policy_type=props.get("policyType", "BuiltIn"),
        effect=effect,
        effect_allowed_values=allowed,
        parameters=props.get("parameters", {}) or {},
    )


class AzurePolicyCatalogue(PolicyCatalogue):
    # Effects that produce no evaluation in DoNotEnforce (audit-only) mode.
    # A policy is kept if its default effect is in AUDIT_EFFECTS, or if any
    # of its allowedValues include an audit effect (policy can be used in audit mode).
    _NON_AUDITABLE = {"modify", "deployifnotexists", "append", "disabled"}
    _AUDIT_EFFECTS  = {"audit", "auditifnotexists", "deny"}

    def __init__(self, cache: str | None = None, *, subscription: str | None = None,
                 include_static: bool = False, exclude_deprecated: bool = True,
                 exclude_manual: bool = True, exclude_non_auditable: bool = True,
                 refresh: bool = False):
        self.cache                 = cache
        self.subscription          = subscription
        self.include_static        = include_static
        self.exclude_deprecated    = exclude_deprecated
        self.exclude_manual        = exclude_manual
        self.exclude_non_auditable = exclude_non_auditable
        self.refresh               = refresh

    def _can_audit(self, d: PolicyDefinition) -> bool:
        """Return True if this policy produces evaluation output in audit-only mode."""
        eff = d.effect.lower()
        if eff in self._AUDIT_EFFECTS or eff == "":
            return True   # unknown effect (old cache) → keep conservatively
        if eff in self._NON_AUDITABLE:
            # Keep if any allowed value is an audit-compatible effect
            return any(v.lower() in self._AUDIT_EFFECTS
                       for v in d.effect_allowed_values)
        return True   # unrecognised effect → keep

    def builtins(self) -> list[PolicyDefinition]:
        if self.cache and os.path.exists(self.cache) and not self.refresh:
            defs = [PolicyDefinition.from_dict(d) for d in json.load(
                open(self.cache, encoding="utf-8"))]
            # apply non-auditable filter even on cached data
            if self.exclude_non_auditable:
                defs = [d for d in defs if self._can_audit(d)]
            return defs

        defs = [normalize_definition(r) for r in self._pull_live()]
        n_raw = len(defs)
        if self.exclude_deprecated:
            defs = [d for d in defs if not d.display_name.startswith("[Deprecated]")]
        n_after_deprecated = len(defs)
        if self.exclude_manual:
            defs = [d for d in defs if d.effect.lower() != "manual"]
        n_after_manual = len(defs)
        if self.exclude_non_auditable:
            defs = [d for d in defs if self._can_audit(d)]
        import sys
        removed = []
        if n_raw - n_after_deprecated: removed.append(f"{n_raw - n_after_deprecated} deprecated")
        if n_after_deprecated - n_after_manual: removed.append(f"{n_after_deprecated - n_after_manual} manual-effect")
        if n_after_manual - len(defs): removed.append(f"{n_after_manual - len(defs)} modify/DINE-only (no audit fallback)")
        if removed:
            print(f"  Filtered out: {', '.join(removed)}", file=sys.stderr)
        self._write_cache(defs)
        return defs

    def _write_cache(self, defs: list[PolicyDefinition]) -> None:
        if not self.cache:
            return
        os.makedirs(os.path.dirname(self.cache) or ".", exist_ok=True)
        payload = [{"id": d.id, "display_name": d.display_name, "description": d.description,
                    "category": d.category, "policy_type": d.policy_type,
                    "effect": d.effect, "effect_allowed_values": d.effect_allowed_values,
                    "parameters": d.parameters} for d in defs]
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

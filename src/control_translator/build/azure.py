"""Emit a native Azure custom Regulatory Compliance initiative — no EPAC.

Modelled on the structure of the published NZISM built-in initiative:
  - `version` / `name` is an **independent semver** (e.g. 1.0.0) for policy/deprecation
    changes; the framework's document version (e.g. NZISM v3.9) lives in the description,
    so you can deprecate policies without changing the standard alignment.
  - each control becomes a `policyDefinitionGroups` entry:
        name        = "<group_prefix><control-id>"   (e.g. New_Zealand_ISM_06.2.5.C.01)
        category    = "06. <chapter name>"           (zero-padded chapter)
        description = the control text
    (optionally an `additionalMetadataId` for the built-in submission path).
  - required default parameters are surfaced as top-level initiative parameters and
    wired into the policy references via [parameters('...')].

Output bundle: policySet.json, assignment.json (audit-only), main.bicep, deploy.sh,
and out-of-scope.json (the OOS register) when one is supplied.
"""
from __future__ import annotations

import json

from .base import ControlSetBuilder
from ..models import Catalog, MappingSet, ArtifactBundle

_AZ_BUILTIN_PREFIX = "/providers/Microsoft.Authorization/policyDefinitions/"


def _definition_id(policy_id: str) -> str:
    return policy_id if policy_id.startswith("/") else _AZ_BUILTIN_PREFIX + policy_id


def _guid(policy_id: str) -> str:
    return policy_id.rstrip("/").split("/")[-1].lower()


def _padded_category(chapter: str) -> str:
    """'6. Information security monitoring' -> '06. Information security monitoring'."""
    head, _, tail = chapter.partition(".")
    if head.strip().isdigit():
        return f"{head.strip().zfill(2)}.{tail}"
    return chapter


class AzurePolicySetBuilder(ControlSetBuilder):
    def build(self, catalog: Catalog, mapping: MappingSet, *,
              framework: dict, options: dict,
              oos: list | None = None,
              oos_suggestions: list | None = None,
              oos_reconsidered: list | None = None) -> ArtifactBundle:
        ism_version = framework.get("version", "")
        short = framework.get("short_name") or mapping.framework_id.upper()
        display_name = options.get("display_name") or framework.get("display_name") \
            or mapping.framework_id
        initiative_version = options.get("initiative_version", "1.0.0")
        group_prefix = options.get("group_prefix", "")
        include_meta = options.get("include_metadata_id", False)
        meta_tpl = options.get("metadata_id_template", "")
        overrides = {_guid(k): v for k, v in (options.get("parameter_overrides") or {}).items()}
        scope = options.get("scope", "subscription")
        enforcement = options.get("enforcement_mode", "DoNotEnforce")
        ref_url = framework.get("reference_url")
        slug = options.get("name") or f"{mapping.framework_id}-{ism_version}"

        description = options.get("description") or (
            f"{short} v{ism_version}. {display_name}. This initiative maps a subset of "
            f"{short} controls to Azure built-in policy. Initiative version "
            f"{initiative_version} is independent of the {short} document version."
            + (f" Reference: {ref_url}." if ref_url else ""))

        controls = {c.id: c for c in catalog.controls()}
        approved = sorted(mapping.approved(), key=lambda m: m.control_id)

        groups, top_params = [], {}
        # policy_entries: (normalized_pid, params_key) -> entry dict (mutable so we can
        # grow groupNames when the same policy covers multiple controls)
        policy_entries: dict[tuple, dict] = {}
        used_ref_ids: set[str] = set()

        for m in approved:
            ctrl = controls.get(m.control_id)
            gname = f"{group_prefix}{m.control_id}"
            group = {
                "name": gname,
                "category": _padded_category(ctrl.family if ctrl else ""),
                "description": (ctrl.prose if ctrl else "") or m.control_id,
            }
            if include_meta and meta_tpl:
                group["additionalMetadataId"] = meta_tpl.format(
                    control_id=m.control_id, group_name=gname)
            groups.append(group)

            for ref in m.policies:
                params = dict(ref.parameters) if ref.parameters else {}
                for pol_param, spec in overrides.get(_guid(ref.policy_id), {}).items():
                    init_name = spec["initiative_param"]
                    params[pol_param] = {"value": f"[parameters('{init_name}')]"}
                    top_params.setdefault(init_name, spec["definition"])

                # Dedup key: same policy + same parameters = one entry, multiple groupNames
                pid = _definition_id(ref.policy_id)
                params_key = json.dumps(params, sort_keys=True)
                dedup_key = (pid, params_key)

                if dedup_key in policy_entries:
                    # Policy already in the set — just add this control's group
                    existing = policy_entries[dedup_key]
                    if gname not in existing["groupNames"]:
                        existing["groupNames"].append(gname)
                else:
                    # First time seeing this policy (with these params)
                    ref_id = ref.display_name or ref.policy_id
                    base, n = ref_id, 2
                    while ref_id in used_ref_ids:    # policyDefinitionReferenceId must be unique
                        ref_id, n = f"{base} ({n})", n + 1
                    used_ref_ids.add(ref_id)

                    entry: dict = {"policyDefinitionReferenceId": ref_id,
                                   "policyDefinitionId": pid,
                                   "groupNames": [gname]}
                    if params:
                        entry["parameters"] = params
                    policy_entries[dedup_key] = entry

        definitions = list(policy_entries.values())

        policy_set = {
            "name": slug,
            "type": "Microsoft.Authorization/policySetDefinitions",
            "properties": {
                "displayName": display_name,
                "description": description,
                "policyType": "Custom",
                "metadata": {"category": "Regulatory Compliance", "version": initiative_version},
                "parameters": top_params,
                "policyDefinitionGroups": groups,
                "policyDefinitions": definitions,
            },
        }
        assignment = {
            "name": f"{slug}-audit",
            "type": "Microsoft.Authorization/policyAssignments",
            "properties": {
                "displayName": f"{display_name} (audit)",
                "policyDefinitionId": f"<policySetDefinitionId:{slug}>",
                "enforcementMode": enforcement,
            },
        }

        # build merged OOS list before the bundle so we can use len() in metadata
        all_oos = (list(oos or []) + list(mapping.preview_excluded or [])
                   + list(mapping.pattern_excluded or []))

        bundle = ArtifactBundle(framework_id=mapping.framework_id, version=ism_version,
                                metadata={"initiative_version": initiative_version,
                                          "controls": len(approved),
                                          "policy_references": len(definitions),
                                          "parameters": len(top_params),
                                          "out_of_scope": len(all_oos)})
        bundle.add("policySet.json", json.dumps(policy_set, indent=2, ensure_ascii=False))
        bundle.add("assignment.json", json.dumps(assignment, indent=2, ensure_ascii=False))
        bundle.add("main.bicep", _bicep(slug, display_name, scope, enforcement))
        bundle.add("deploy.sh", _deploy_sh(slug, scope))
        if all_oos:
            bundle.add("out-of-scope.json", json.dumps(all_oos, indent=2, ensure_ascii=False))
        if oos_suggestions:
            bundle.add("oos-candidates.json", json.dumps(oos_suggestions, indent=2, ensure_ascii=False))
        if oos_reconsidered:
            bundle.add("oos-reconsidered.json", json.dumps(oos_reconsidered, indent=2, ensure_ascii=False))
        return bundle


def _bicep(slug: str, name: str, scope: str, enforcement: str) -> str:
    target = "managementGroup" if scope == "managementGroup" else "subscription"
    return f"""// Generated by control-translator. Deploy at {target} scope.
targetScope = '{target}'

resource initiative 'Microsoft.Authorization/policySetDefinitions@2021-06-01' = {{
  name: '{slug}'
  properties: loadJsonContent('policySet.json').properties
}}

resource assignment 'Microsoft.Authorization/policyAssignments@2022-06-01' = {{
  name: '{slug}-audit'
  properties: {{
    displayName: '{name} (audit)'
    policyDefinitionId: initiative.id
    enforcementMode: '{enforcement}'
  }}
}}
"""


def _deploy_sh(slug: str, scope: str) -> str:
    cmd = ("az deployment mg create --management-group-id <MG_ID>"
           if scope == "managementGroup"
           else "az deployment sub create --location <LOCATION>")
    return (f"#!/usr/bin/env bash\nset -euo pipefail\n"
            f"# Deploy the {slug} custom Regulatory Compliance initiative (audit-only).\n"
            f"{cmd} \\\n  --template-file main.bicep \\\n  --name {slug}\n")

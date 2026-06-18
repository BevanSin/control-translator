"""MCP server for control-translator.

Exposes the ct pipeline, mapping store, and review workflow as MCP tools
and resources so any MCP client (Copilot, Claude Desktop, etc.) can interact
with the compliance engine conversationally.

Run:
    ct-mcp                        # stdio transport (default for IDE integration)
    ct-mcp --transport http       # streamable HTTP on port 8000
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import load_config, resolve
from .mapping.store import MappingStore

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "control-translator",
    instructions=(
        "You are interacting with control-translator (ct), an agentic engine that "
        "translates security standards into deployable cloud compliance controls. "
        "Use the tools to run the pipeline, inspect results, review mappings, and "
        "manage the out-of-scope register."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_config(config_path: str | None = None) -> dict:
    """Load and resolve a config file. Falls back to nzism-azure.json."""
    root = str(_PROJECT_ROOT)
    if config_path:
        p = config_path if os.path.isabs(config_path) else os.path.join(root, config_path)
    else:
        p = os.path.join(root, "config", "nzism-azure.json")
    return resolve(load_config(p), root)


def _latest_bundle_dir(config: dict) -> Path | None:
    """Find the latest output bundle directory."""
    out_dir = Path(config.get("out_dir", "out"))
    if not out_dir.is_absolute():
        out_dir = _PROJECT_ROOT / out_dir
    fw = config["framework"]
    bundle = out_dir / f"{fw['id']}-{fw['version']}"
    return bundle if bundle.exists() else None


def _load_json(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# TOOLS — actions that do things
# ---------------------------------------------------------------------------

@mcp.tool()
def run_pipeline(config_path: str | None = None, distribute: bool = True) -> str:
    """Run the full ct pipeline (ingest → catalogue → map → build → validate → distribute).

    Args:
        config_path: Path to config file relative to project root.
                     Defaults to config/nzism-azure.json.
        distribute: Whether to publish the output bundle. Default True.
    """
    from .pipeline import run_pipeline as _run

    config = _find_config(config_path)
    result = _run(config, do_distribute=distribute)

    n_controls = sum(1 for _ in result.catalog.controls())
    approved = result.mapping.approved()
    pending = result.mapping.pending_review()

    summary = {
        "status": "success" if not result.lint_errors else "completed_with_warnings",
        "framework": f"{result.mapping.framework_id} v{result.mapping.version}",
        "controls_total": n_controls,
        "approved": len(approved),
        "pending_review": len(pending),
        "lint_errors": len(result.lint_errors),
        "elapsed": f"{result.elapsed_seconds:.0f}s",
        "published_to": result.published_to,
    }
    if result.lint_errors:
        summary["lint_details"] = result.lint_errors[:10]
    return json.dumps(summary, indent=2)


@mcp.tool()
def approve_controls(control_ids: list[str], config_path: str | None = None) -> str:
    """Approve pending mappings by setting their decision to 'include'.

    Args:
        control_ids: List of control IDs to approve (e.g. ["06.2.5.C.01", "06.2.5.C.02"]).
        config_path: Config file path (default: config/nzism-azure.json).
    """
    config = _find_config(config_path)
    mcfg = config["mapping"]
    fw = config["framework"]
    store = MappingStore(mcfg["store"])
    mapping = store.load(fw["id"], fw["version"])

    approved = []
    not_found = []
    already_approved = []

    for cid in control_ids:
        m = mapping.mappings.get(cid)
        if not m:
            not_found.append(cid)
        elif m.decision.value == "include":
            already_approved.append(cid)
        else:
            from .models.mapping import Decision
            m.decision = Decision.INCLUDE
            m.source = "human"
            approved.append(cid)

    if approved:
        store.save(mapping)

    return json.dumps({
        "approved": approved,
        "already_approved": already_approved,
        "not_found": not_found,
    }, indent=2)


@mcp.tool()
def reject_controls(control_ids: list[str], config_path: str | None = None) -> str:
    """Reject (ignore) pending mappings by setting their decision to 'ignore'.

    Args:
        control_ids: List of control IDs to ignore.
        config_path: Config file path (default: config/nzism-azure.json).
    """
    config = _find_config(config_path)
    mcfg = config["mapping"]
    fw = config["framework"]
    store = MappingStore(mcfg["store"])
    mapping = store.load(fw["id"], fw["version"])

    ignored = []
    not_found = []

    for cid in control_ids:
        m = mapping.mappings.get(cid)
        if not m:
            not_found.append(cid)
        else:
            from .models.mapping import Decision
            m.decision = Decision.IGNORE
            m.source = "human"
            ignored.append(cid)

    if ignored:
        store.save(mapping)

    return json.dumps({"ignored": ignored, "not_found": not_found}, indent=2)


@mcp.tool()
def add_to_oos_register(
    policy_ids: list[str],
    reasons: list[str],
    register: str = "global",
    config_path: str | None = None,
) -> str:
    """Add policies to the out-of-scope register.

    Args:
        policy_ids: List of policy GUIDs or ARM IDs to exclude.
        reasons: Corresponding reasons for each exclusion.
        register: Which register — "global" or "framework" (default: global).
        config_path: Config file path.
    """
    from datetime import date

    config = _find_config(config_path)
    gi = config["mapping"].get("global_ignore", [])
    if isinstance(gi, str):
        gi = [gi]

    if register == "framework" and len(gi) > 1:
        path = gi[-1]  # framework-specific is the last in the list
    elif gi:
        path = gi[0]   # global is the first
    else:
        return json.dumps({"error": "No global_ignore paths configured"})

    abs_path = path if os.path.isabs(path) else os.path.join(str(_PROJECT_ROOT), path)

    existing = []
    if os.path.exists(abs_path):
        with open(abs_path, encoding="utf-8") as fh:
            existing = json.load(fh)

    added = []
    for pid, reason in zip(policy_ids, reasons):
        entry = {
            "policy_id": pid,
            "reason": reason,
            "oos_date": date.today().isoformat(),
        }
        existing.append(entry)
        added.append(pid)

    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2, ensure_ascii=False)

    return json.dumps({"added": added, "register_path": path}, indent=2)


@mcp.tool()
def get_mapping_details(control_id: str, config_path: str | None = None) -> str:
    """Get full mapping details for a specific control.

    Args:
        control_id: The control ID to look up (e.g. "06.2.5.C.01").
        config_path: Config file path.
    """
    config = _find_config(config_path)
    mcfg = config["mapping"]
    fw = config["framework"]
    store = MappingStore(mcfg["store"])
    mapping = store.load(fw["id"], fw["version"])

    m = mapping.mappings.get(control_id)
    if not m:
        return json.dumps({"error": f"Control {control_id} not found in mapping store"})

    return json.dumps(m.to_dict(), indent=2)


@mcp.tool()
def search_controls(
    query: str,
    status: str | None = None,
    limit: int = 20,
    config_path: str | None = None,
) -> str:
    """Search controls by keyword in control ID or rationale.

    Args:
        query: Search term (case-insensitive substring match).
        status: Filter by decision status — "include", "ignore", "review", or None for all.
        limit: Max results to return (default 20).
        config_path: Config file path.
    """
    config = _find_config(config_path)
    mcfg = config["mapping"]
    fw = config["framework"]
    store = MappingStore(mcfg["store"])
    mapping = store.load(fw["id"], fw["version"])

    q = query.lower()
    results = []
    for cid, m in mapping.mappings.items():
        if status and m.decision.value != status:
            continue
        if q in cid.lower() or q in m.rationale.lower() or any(
            q in p.display_name.lower() for p in m.policies
        ):
            results.append({
                "control_id": cid,
                "decision": m.decision.value,
                "confidence": m.confidence,
                "policies": [p.display_name or p.policy_id for p in m.policies],
                "rationale": m.rationale[:200],
            })
            if len(results) >= limit:
                break

    return json.dumps({"count": len(results), "results": results}, indent=2)


# ---------------------------------------------------------------------------
# RESOURCES — read-only data for context
# ---------------------------------------------------------------------------

@mcp.resource("ct://status")
def get_status() -> str:
    """Current pipeline status — framework, mapping store stats, latest bundle."""
    config = _find_config()
    fw = config["framework"]
    mcfg = config["mapping"]
    store = MappingStore(mcfg["store"])

    store_path = mcfg["store"]
    abs_store = store_path if os.path.isabs(store_path) else os.path.join(
        str(_PROJECT_ROOT), store_path)

    info: dict = {
        "framework": f"{fw['id']} v{fw['version']}",
        "display_name": fw.get("display_name", fw["id"]),
        "mapping_store": store_path,
        "store_exists": os.path.exists(abs_store),
    }

    if os.path.exists(abs_store):
        mapping = store.load(fw["id"], fw["version"])
        info["total_mappings"] = len(mapping.mappings)
        info["approved"] = len(mapping.approved())
        info["pending_review"] = len(mapping.pending_review())
        info["ignored"] = sum(
            1 for m in mapping.mappings.values() if m.decision.value == "ignore"
        )

    bundle_dir = _latest_bundle_dir(config)
    info["latest_bundle"] = str(bundle_dir) if bundle_dir else None

    # Run log
    if bundle_dir:
        log_path = bundle_dir / "run-log.jsonl"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                info["last_run"] = json.loads(lines[-1])

    return json.dumps(info, indent=2)


@mcp.resource("ct://pending-review")
def get_pending_review() -> str:
    """All controls currently awaiting authority sign-off."""
    config = _find_config()
    fw = config["framework"]
    mcfg = config["mapping"]
    store = MappingStore(mcfg["store"])
    mapping = store.load(fw["id"], fw["version"])

    pending = mapping.pending_review()
    results = []
    for m in pending:
        results.append({
            "control_id": m.control_id,
            "confidence": m.confidence,
            "policies": [{"id": p.policy_id, "name": p.display_name} for p in m.policies],
            "rationale": m.rationale,
        })

    return json.dumps({"count": len(results), "items": results}, indent=2)


@mcp.resource("ct://oos-candidates")
def get_oos_candidates() -> str:
    """OOS candidates from the latest run (policies the LLM flagged for exclusion)."""
    config = _find_config()
    bundle_dir = _latest_bundle_dir(config)
    if not bundle_dir:
        return json.dumps({"error": "No bundle found. Run the pipeline first."})

    oos_path = bundle_dir / "oos-candidates.json"
    if not oos_path.exists():
        return json.dumps({"count": 0, "items": []})

    items = _load_json(oos_path)
    return json.dumps({"count": len(items), "items": items}, indent=2)


@mcp.resource("ct://oos-reconsidered")
def get_oos_reconsidered() -> str:
    """OOS entries that may need review (preview→GA, removed from catalogue)."""
    config = _find_config()
    bundle_dir = _latest_bundle_dir(config)
    if not bundle_dir:
        return json.dumps({"error": "No bundle found. Run the pipeline first."})

    path = bundle_dir / "oos-reconsidered.json"
    if not path.exists():
        return json.dumps({"count": 0, "items": []})

    items = _load_json(path)
    return json.dumps({"count": len(items), "items": items}, indent=2)


@mcp.resource("ct://bundle-summary")
def get_bundle_summary() -> str:
    """Summary of the latest output bundle (policySet stats, files produced)."""
    config = _find_config()
    bundle_dir = _latest_bundle_dir(config)
    if not bundle_dir:
        return json.dumps({"error": "No bundle found. Run the pipeline first."})

    files = [f.name for f in bundle_dir.iterdir() if f.is_file()]
    summary: dict = {"bundle_path": str(bundle_dir), "files": files}

    ps_path = bundle_dir / "policySet.json"
    if ps_path.exists():
        ps = _load_json(ps_path)
        props = ps.get("properties", {})
        defs = props.get("policyDefinitions", [])
        grps = props.get("policyDefinitionGroups", [])
        summary["policy_definitions"] = len(defs)
        summary["control_groups"] = len(grps)
        summary["multi_control_policies"] = sum(
            1 for d in defs if len(d.get("groupNames", [])) > 1
        )
        summary["parameters"] = len(props.get("parameters", {}))

    return json.dumps(summary, indent=2)


@mcp.resource("ct://run-history")
def get_run_history() -> str:
    """History of pipeline runs (from run-log.jsonl)."""
    config = _find_config()
    bundle_dir = _latest_bundle_dir(config)
    if not bundle_dir:
        return json.dumps({"error": "No bundle found."})

    log_path = bundle_dir / "run-log.jsonl"
    if not log_path.exists():
        return json.dumps({"runs": []})

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    runs = [json.loads(line) for line in lines[-20:]]  # last 20 runs
    return json.dumps({"count": len(runs), "runs": runs}, indent=2)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server."""
    import argparse

    parser = argparse.ArgumentParser(prog="ct-mcp", description="control-translator MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http", "http"],
                        default="stdio",
                        help="Transport type (default: stdio)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port for HTTP transport (default: 8000)")
    args = parser.parse_args()

    transport = args.transport
    if transport == "http":
        transport = "streamable-http"

    if transport == "streamable-http":
        mcp.run(transport=transport, host="127.0.0.1", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

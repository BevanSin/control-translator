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

from mcp.server.fastmcp import FastMCP

from .application import (
    ApplicationServiceError,
    get_application_service,
    to_dict,
)


mcp = FastMCP(
    "control-translator",
    instructions=(
        "You are interacting with control-translator (ct), an agentic engine that "
        "translates security standards into deployable cloud compliance controls. "
        "Use the tools to run the pipeline, inspect results, review mappings, and "
        "manage the out-of-scope register."
    ),
)


def _ok(payload: dict) -> str:
    return json.dumps(payload, indent=2)


def _error(exc: ApplicationServiceError) -> str:
    return _ok({"error": {"code": exc.code, "message": str(exc)}})


@mcp.tool()
def run_pipeline(config_path: str | None = None, distribute: bool = True) -> str:
    """Run the full ct pipeline (ingest → catalogue → map → build → validate → distribute)."""
    try:
        result = get_application_service().run(
            config_path=config_path,
            do_distribute=distribute,
            resolution_root=os.getcwd(),
        )
    except ApplicationServiceError as exc:
        return _error(exc)

    payload = to_dict(result)
    payload["status"] = "success" if not payload["lint_errors"] else "completed_with_warnings"
    payload["elapsed"] = None
    return _ok(payload)


@mcp.tool()
def approve_controls(control_ids: list[str], config_path: str | None = None) -> str:
    """Approve pending mappings by setting their decision to 'include'."""
    try:
        result = get_application_service().approve_controls(
            control_ids=control_ids,
            config_path=config_path,
            resolution_root=os.getcwd(),
        )
    except ApplicationServiceError as exc:
        return _error(exc)

    payload = to_dict(result)
    return _ok(
        {
            "approved": payload["updated"],
            "already_approved": payload["already_updated"],
            "not_found": payload["not_found"],
        }
    )


@mcp.tool()
def reject_controls(control_ids: list[str], config_path: str | None = None) -> str:
    """Reject (ignore) pending mappings by setting their decision to 'ignore'."""
    try:
        result = get_application_service().reject_controls(
            control_ids=control_ids,
            config_path=config_path,
            resolution_root=os.getcwd(),
        )
    except ApplicationServiceError as exc:
        return _error(exc)

    payload = to_dict(result)
    return _ok({"ignored": payload["updated"], "not_found": payload["not_found"]})


@mcp.tool()
def add_to_oos_register(
    policy_ids: list[str],
    reasons: list[str],
    register: str = "global",
    config_path: str | None = None,
) -> str:
    """Add policies to the out-of-scope register."""
    try:
        result = get_application_service().add_to_oos_register(
            policy_ids=policy_ids,
            reasons=reasons,
            register=register,
            config_path=config_path,
            resolution_root=os.getcwd(),
        )
    except ApplicationServiceError as exc:
        return _error(exc)

    return _ok(to_dict(result))


@mcp.tool()
def get_mapping_details(control_id: str, config_path: str | None = None) -> str:
    """Get mapping details for a specific control."""
    try:
        payload = get_application_service().mapping_details(
            control_id=control_id,
            config_path=config_path,
            resolution_root=os.getcwd(),
        )
    except ApplicationServiceError as exc:
        return _error(exc)

    return _ok(payload)


@mcp.tool()
def search_controls(
    query: str,
    status: str | None = None,
    limit: int = 20,
    config_path: str | None = None,
) -> str:
    """Search controls by keyword in control ID, policy name, or rationale."""
    try:
        payload = get_application_service().search_controls(
            query=query,
            status=status,
            limit=limit,
            config_path=config_path,
            resolution_root=os.getcwd(),
        )
    except ApplicationServiceError as exc:
        return _error(exc)

    return _ok(payload)


@mcp.resource("ct://status")
def get_status() -> str:
    """Current pipeline status — framework, mapping store stats, latest bundle."""
    try:
        payload = get_application_service().status(config_path=None, resolution_root=os.getcwd())
    except ApplicationServiceError as exc:
        return _error(exc)
    return _ok(payload)


@mcp.resource("ct://pending-review")
def get_pending_review() -> str:
    """All controls currently awaiting authority sign-off."""
    try:
        payload = get_application_service().pending_review(config_path=None, resolution_root=os.getcwd())
    except ApplicationServiceError as exc:
        return _error(exc)
    return _ok(payload)


@mcp.resource("ct://oos-candidates")
def get_oos_candidates() -> str:
    """OOS candidates from the latest run (policies the LLM flagged for exclusion)."""
    try:
        payload = get_application_service().bundle_json_resource(
            config_path=None,
            resolution_root=os.getcwd(),
            filename="oos-candidates.json",
        )
    except ApplicationServiceError as exc:
        return _error(exc)
    return _ok(payload)


@mcp.resource("ct://oos-reconsidered")
def get_oos_reconsidered() -> str:
    """OOS entries that may need review (preview→GA, removed from catalogue)."""
    try:
        payload = get_application_service().bundle_json_resource(
            config_path=None,
            resolution_root=os.getcwd(),
            filename="oos-reconsidered.json",
        )
    except ApplicationServiceError as exc:
        return _error(exc)
    return _ok(payload)


@mcp.resource("ct://bundle-summary")
def get_bundle_summary() -> str:
    """Summary of the latest output bundle (policySet stats, files produced)."""
    try:
        payload = get_application_service().bundle_summary(config_path=None, resolution_root=os.getcwd())
    except ApplicationServiceError as exc:
        return _error(exc)
    return _ok(payload)


@mcp.resource("ct://run-history")
def get_run_history() -> str:
    """History of pipeline runs (from run-log.jsonl)."""
    try:
        payload = get_application_service().run_history(config_path=None, resolution_root=os.getcwd())
    except ApplicationServiceError as exc:
        return _error(exc)
    return _ok(payload)


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

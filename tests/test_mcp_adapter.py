from __future__ import annotations

import json

import pytest

from control_translator.application import ApplicationServiceError, MappingMutationResult, RunSummary

pytest.importorskip("mcp.server.fastmcp")

from control_translator import mcp_server


class _FakeError(ApplicationServiceError):
    code = "fake_error"


class _FakeService:
    def run(self, **_kwargs):
        return RunSummary(
            framework="demo v1",
            controls_total=2,
            approved=1,
            pending_review=1,
            lint_errors=[],
            published_to=None,
        )

    def approve_controls(self, **_kwargs):
        return MappingMutationResult(updated=["C-1"], already_updated=[], not_found=["C-9"])

    def reject_controls(self, **_kwargs):
        raise _FakeError("bad request")


def test_mcp_tools_use_shared_service_and_return_structured_json(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_application_service", lambda: _FakeService())

    run_payload = json.loads(mcp_server.run_pipeline(config_path="config/sample.json", distribute=False))
    assert run_payload["framework"] == "demo v1"
    assert run_payload["status"] == "success"

    approve_payload = json.loads(mcp_server.approve_controls(["C-1", "C-9"]))
    assert approve_payload["approved"] == ["C-1"]
    assert approve_payload["not_found"] == ["C-9"]

    reject_payload = json.loads(mcp_server.reject_controls(["C-1"]))
    assert reject_payload["error"]["code"] == "fake_error"
    assert "bad request" in reject_payload["error"]["message"]

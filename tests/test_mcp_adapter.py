from __future__ import annotations

import json

from control_translator.application import ApplicationServiceError, MappingMutationResult, RunSummary
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
    assert "bad request" not in reject_payload["error"]["message"]
    assert reject_payload["error"]["message"] == "Request could not be completed."


class _SensitiveConfigError(ApplicationServiceError):
    code = "invalid_project_or_config"


class _ErroringService:
    def run(self, **_kwargs):
        raise _SensitiveConfigError(
            "Failed to load /home/runner/private/config.json with token=abcd and https://contoso.example"
        )


def test_mcp_error_payload_does_not_expose_paths_urls_or_credentials(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_application_service", lambda: _ErroringService())

    payload = json.loads(mcp_server.run_pipeline(config_path="missing.json", distribute=False))
    message = payload["error"]["message"]

    assert payload["error"]["code"] == "invalid_project_or_config"
    assert "/home/runner" not in message
    assert "https://" not in message
    assert "token=" not in message
    assert message == "Invalid project or configuration."


def test_mcp_main_starts_with_stdio_transport(monkeypatch):
    calls = []

    def _fake_run(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mcp_server.mcp, "run", _fake_run)
    monkeypatch.setattr("sys.argv", ["ct-mcp", "--transport", "stdio"])

    mcp_server.main()
    assert calls == [{"transport": "stdio"}]

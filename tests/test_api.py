"""Contract and security tests for the local API (loopback host, session token,
CORS, cross-project isolation, traversal, oversized bodies, cancel authorization).
"""
from __future__ import annotations

import json
import os
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from control_translator.application import ControlTranslatorService  # noqa: E402
from control_translator.api.app import MAX_BODY_BYTES, create_app  # noqa: E402
from control_translator.config import load_config, resolve  # noqa: E402
from control_translator.projects import ProjectStore  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_config(tmp_path, *, framework_id: str = "sample") -> str:
    """A runnable, offline config isolated to tmp_path (mirrors config/sample.json)."""
    os.makedirs(tmp_path, exist_ok=True)
    config = resolve(load_config(os.path.join(REPO_ROOT, "config", "sample.json")), REPO_ROOT)
    config["framework"]["id"] = framework_id
    config["out_dir"] = str(tmp_path / "out")
    config["mapping"]["store"] = str(tmp_path / "mapping.json")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))
    app = create_app(service=service)
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, app.state.session_token, service


def _auth(token: str) -> dict:
    return {"X-CT-Session-Token": token}


def test_health_requires_no_token(api):
    client, _token, _service = api
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_sensitive_route_rejects_missing_or_wrong_token(api):
    client, token, _service = api
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 401

    resp = client.get("/api/v1/projects", headers=_auth("wrong-token"))
    assert resp.status_code == 401

    resp = client.get("/api/v1/projects", headers=_auth(token))
    assert resp.status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "attacker.local:8756"])
def test_unapproved_host_header_is_rejected(tmp_path, monkeypatch, host):
    monkeypatch.chdir(REPO_ROOT)
    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))
    app = create_app(service=service)
    client = TestClient(app, base_url=f"http://{host}")
    resp = client.get("/api/v1/health")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_host"


def test_cors_does_not_allow_arbitrary_origins(api):
    client, token, _service = api
    resp = client.get(
        "/api/v1/projects",
        headers={**_auth(token), "Origin": "https://attacker.example"},
    )
    # The request is still served (no browser preflight involved in a plain GET),
    # but no Access-Control-Allow-Origin is granted to the disallowed origin.
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_oversized_body_is_rejected(api):
    client, token, _service = api
    huge_name = "x" * (MAX_BODY_BYTES + 1)
    resp = client.post(
        "/api/v1/projects", json={"name": huge_name}, headers=_auth(token),
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_malformed_identifiers_are_rejected_with_typed_validation(api):
    client, token, _service = api
    resp = client.post("/api/v1/projects", json={"name": ""}, headers=_auth(token))
    assert resp.status_code == 422

    resp = client.post(
        "/api/v1/projects", json={"name": "ok"}, headers=_auth(token),
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    # control_ids batch must be non-empty
    resp = client.post(
        f"/api/v1/projects/{project_id}/review/approve",
        json={"control_ids": []},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_validation_errors_never_echo_submitted_values(api):
    client, token, _service = api
    private_path = "C:\\private\\" + ("x" * 5000)

    resp = client.post(
        "/api/v1/projects", json={"name": "ok", "config_path": private_path}, headers=_auth(token),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert private_path not in json.dumps(body)
    assert "input" not in json.dumps(body)
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"][0]["loc"]

    # Same guarantee for a query-parameter validation failure.
    resp = client.get(
        "/api/v1/projects/some-project/mappings/search",
        params={"query": "x" * 5000},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    assert ("x" * 5000) not in json.dumps(resp.json())


def test_delete_unknown_project_returns_sanitized_404(api):
    client, token, _service = api
    resp = client.delete("/api/v1/projects/00000000-0000-0000-0000-000000000000", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "project_not_found", "message": "Project not found."}}


def test_project_path_traversal_is_rejected(api):
    client, token, _service = api
    resp = client.delete("/api/v1/projects/..%2f..%2fetc", headers=_auth(token))
    assert resp.status_code in (400, 403, 404)
    body = resp.json()
    assert "/etc" not in json.dumps(body)


def test_artifact_resource_name_is_allow_listed_not_arbitrary_path(api, tmp_path):
    client, token, service = api
    config_path = _write_config(tmp_path)
    project_id = service.project_id_for_config(config_path, resolution_root=REPO_ROOT)
    resp = client.post(
        f"/api/v1/projects/{project_id}/open", json={"config_path": config_path}, headers=_auth(token),
    )
    assert resp.status_code == 200

    resp = client.get(
        f"/api/v1/projects/{project_id}/artifacts/..%2f..%2fetc%2fpasswd",
        params={"config_path": config_path},
        headers=_auth(token),
    )
    assert resp.status_code == 404


def test_cross_project_config_mismatch_is_rejected(api, tmp_path):
    client, token, service = api
    config_a = _write_config(tmp_path / "a", framework_id="sample-a")
    config_b = _write_config(tmp_path / "b", framework_id="sample-b")
    project_a = service.project_id_for_config(config_a, resolution_root=REPO_ROOT)

    # Naming project A's id while supplying project B's config must be refused.
    resp = client.post(
        f"/api/v1/projects/{project_a}/open", json={"config_path": config_b}, headers=_auth(token),
    )
    assert resp.status_code == 403
    assert resp.json() == {
        "error": {"code": "project_mismatch", "message": "Project id does not match the supplied configuration."}
    }


def _wait_for_terminal(client, token, project_id, run_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}", headers=_auth(token))
        state = resp.json()["run"]["state"]
        if state in ("succeeded", "failed", "cancelled"):
            return resp.json()["run"]
        time.sleep(0.05)
    raise AssertionError("run did not reach a terminal state in time")


def test_run_lifecycle_and_review_end_to_end(api, tmp_path):
    client, token, service = api
    config_path = _write_config(tmp_path)
    project_id = service.project_id_for_config(config_path, resolution_root=REPO_ROOT)

    resp = client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"config_path": config_path, "distribute": True},
        headers=_auth(token),
    )
    assert resp.status_code == 202
    run_id = resp.json()["run"]["id"]

    record = _wait_for_terminal(client, token, project_id, run_id)
    assert record["state"] == "succeeded"

    resp = client.get(f"/api/v1/projects/{project_id}/runs", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["count"] == 1

    resp = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}/events", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["count"] > 0

    resp = client.get(
        f"/api/v1/projects/{project_id}/review",
        params={"config_path": config_path},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    pending = {item["control_id"] for item in resp.json()["items"]}
    assert "SAMPLE-LM-1" in pending

    resp = client.post(
        f"/api/v1/projects/{project_id}/review/approve",
        json={"control_ids": ["SAMPLE-LM-1"]},
        params={"config_path": config_path},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == ["SAMPLE-LM-1"]

    resp = client.get(
        f"/api/v1/projects/{project_id}/artifacts",
        params={"config_path": config_path},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["policy_definitions"] >= 1


def test_create_project_binds_to_config_derived_id_then_configure_and_run(api, tmp_path):
    """A project created via POST /projects must use the same id every other
    config-backed route derives from that config — otherwise it could never
    pass the cross-project match check enforced everywhere else."""
    client, token, service = api
    config_path = _write_config(tmp_path)
    expected_project_id = service.project_id_for_config(config_path, resolution_root=REPO_ROOT)

    resp = client.post(
        "/api/v1/projects", json={"name": "acceptance", "config_path": config_path}, headers=_auth(token),
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]
    assert project_id == expected_project_id

    resp = client.post(
        f"/api/v1/projects/{project_id}/open", json={"config_path": config_path}, headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["project_id"] == project_id

    resp = client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"config_path": config_path, "distribute": False},
        headers=_auth(token),
    )
    assert resp.status_code == 202
    run_id = resp.json()["run"]["id"]
    record = _wait_for_terminal(client, token, project_id, run_id)
    assert record["state"] == "succeeded"


def test_status_and_artifact_responses_contain_no_filesystem_paths(api, tmp_path):
    client, token, service = api
    config_path = _write_config(tmp_path)
    project_id = service.project_id_for_config(config_path, resolution_root=REPO_ROOT)

    client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"config_path": config_path, "distribute": True},
        headers=_auth(token),
    )
    time.sleep(0.5)

    resp = client.post(
        f"/api/v1/projects/{project_id}/open", json={"config_path": config_path}, headers=_auth(token),
    )
    assert resp.status_code == 200
    status_body = resp.json()
    assert "mapping_store" not in status_body
    assert "latest_bundle" not in status_body
    assert str(tmp_path) not in json.dumps(status_body)

    resp = client.get(
        f"/api/v1/projects/{project_id}/artifacts",
        params={"config_path": config_path},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    artifact_body = resp.json()
    assert "bundle_path" not in artifact_body
    assert str(tmp_path) not in json.dumps(artifact_body)


def test_cancel_unknown_run_for_project_returns_404_not_cross_project_state(api, tmp_path):
    client, token, service = api
    config_a = _write_config(tmp_path / "a", framework_id="sample-a")
    config_b = _write_config(tmp_path / "b", framework_id="sample-b")
    project_a = service.project_id_for_config(config_a, resolution_root=REPO_ROOT)
    project_b = service.project_id_for_config(config_b, resolution_root=REPO_ROOT)

    resp = client.post(
        f"/api/v1/projects/{project_b}/runs",
        json={"config_path": config_b, "distribute": False},
        headers=_auth(token),
    )
    run_id = resp.json()["run"]["id"]
    _wait_for_terminal(client, token, project_b, run_id)

    # Cancelling project B's run id while addressing project A must not succeed
    # or affect project B's own state — it must be reported as not found.
    resp = client.post(f"/api/v1/projects/{project_a}/runs/{run_id}/cancel", headers=_auth(token))
    assert resp.status_code == 404

    resp = client.get(f"/api/v1/projects/{project_b}/runs/{run_id}", headers=_auth(token))
    assert resp.json()["run"]["state"] == "succeeded"


def test_error_responses_never_include_filesystem_paths(api):
    client, token, _service = api
    resp = client.post(
        f"/api/v1/projects/nonexistent-config/open",
        json={"config_path": "/definitely/not/a/real/path.json"},
        headers=_auth(token),
    )
    body = resp.json()
    assert resp.status_code in (400, 403)
    assert "/definitely/not/a/real/path.json" not in json.dumps(body)

"""Focused offline tests for the installed-dashboard launcher primitives."""
from __future__ import annotations

import socket

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from control_translator.api.app import create_app  # noqa: E402
from control_translator.projects import ProjectStore  # noqa: E402
from control_translator.runs import ProjectRunConflictError  # noqa: E402
from control_translator.runs.lock import DataRootInstanceLock  # noqa: E402
from control_translator.web import reserve_loopback_port, validate_data_root  # noqa: E402


def test_dashboard_assets_are_served_from_the_same_loopback_app(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Control Translator</title>", encoding="utf-8")
    app = create_app(static_assets=tmp_path)
    response = TestClient(app, base_url="http://127.0.0.1").get("/")

    assert response.status_code == 200
    assert "Control Translator" in response.text


def test_port_reservation_is_an_actual_bound_listener():
    listener, port = reserve_loopback_port(0)
    try:
        contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):
                contender.bind(("127.0.0.1", port))
        finally:
            contender.close()
    finally:
        listener.close()


def test_invalid_data_root_has_a_sanitized_error(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="selected data root is unavailable") as error:
        validate_data_root(str(blocked))

    assert str(blocked) not in str(error.value)


def test_data_root_instance_lock_rejects_a_second_live_launcher(tmp_path):
    project_store = ProjectStore(tmp_path)
    project = project_store.create("Visible while locked")
    first = DataRootInstanceLock(tmp_path)
    second = DataRootInstanceLock(tmp_path)
    first.acquire("first")
    try:
        assert project_store.list() == [project]
        with pytest.raises(ProjectRunConflictError):
            second.acquire("second")
    finally:
        first.release()


def test_data_root_instance_lock_reclaims_a_stale_launcher(tmp_path):
    lock_path = tmp_path / ".ct-web-instance-lock.json"
    lock_path.write_text(
        '{"pid": 999999999, "run_id": "stale", "lock_token": "stale-token"}',
        encoding="utf-8",
    )

    lock = DataRootInstanceLock(tmp_path)
    lock.acquire("replacement")
    try:
        assert lock_path.exists()
        assert "replacement" in lock_path.read_text(encoding="utf-8")
    finally:
        lock.release()

    assert not lock_path.exists()

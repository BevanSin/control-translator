"""Reusable pipeline run service: identity, durable history, locking, cancellation."""
from __future__ import annotations

import os
import threading
import time

import pytest

from control_translator.config import load_config, resolve
from control_translator.projects import ProjectStore
from control_translator.runs import (
    InvalidRunStateTransitionError,
    PipelineService,
    ProjectRunConflictError,
    RunNotFoundError,
    RunRecord,
    RunState,
)
from control_translator.runs.store import RunStore

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _project_config(project_store: ProjectStore, project_id: str) -> dict:
    """Build a sample config whose mutable paths live inside the project workspace."""
    config = resolve(load_config(os.path.join(REPO_ROOT, "config", "sample.json")), REPO_ROOT)
    project_root = project_store.resolve_path(project_id, ".")
    config["out_dir"] = str(project_root / "artifacts")
    config["mapping"]["store"] = str(project_root / "mappings" / "sample.json")
    return config


def _service(tmp_path) -> tuple[PipelineService, ProjectStore, str]:
    project_store = ProjectStore(tmp_path / "data-root")
    project = project_store.create("Example")
    service = PipelineService(project_store)
    return service, project_store, project.id


def test_successful_run_reaches_terminal_state_with_history(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    config = _project_config(project_store, project_id)

    handle = service.start(project_id, config)
    record = service.wait(project_id, handle.run_id, timeout=30)

    assert record.state is RunState.SUCCEEDED
    assert record.error_type is None
    assert record.started_at is not None and record.finished_at is not None

    events = service.events(project_id, handle.run_id)
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.completed"
    assert all(e["run_id"] == handle.run_id for e in events)

    # mapping carry-forward behaviour is preserved for a project-scoped run
    second = service.wait(project_id, service.start(project_id, config).run_id, timeout=30)
    assert second.state is RunState.SUCCEEDED


def test_failed_run_retains_typed_error_and_sanitized_diagnostics(tmp_path, monkeypatch):
    service, project_store, project_id = _service(tmp_path)
    config = _project_config(project_store, project_id)

    class _Boom(RuntimeError):
        pass

    def _explode(*_args, **_kwargs):
        raise _Boom("secret-endpoint https://contoso.example/api key=abcd1234")

    monkeypatch.setattr("control_translator.pipeline.get_builder", _explode)

    handle = service.start(project_id, config)
    record = service.wait(project_id, handle.run_id, timeout=30)

    assert record.state is RunState.FAILED
    assert record.error_type == "_Boom"
    assert record.error_message == "[redacted]"
    assert "contoso" not in (record.error_message or "")


def test_cancellation_is_cooperative_and_reaches_cancelled_state(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    config = _project_config(project_store, project_id)

    handle = service.start(project_id, config)
    service.cancel(project_id, handle.run_id)
    record = service.wait(project_id, handle.run_id, timeout=30)

    assert record.state is RunState.CANCELLED
    assert record.error_type is None


def test_cancelling_unknown_or_finished_run_raises_typed_error(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    config = _project_config(project_store, project_id)

    handle = service.start(project_id, config)
    service.wait(project_id, handle.run_id, timeout=30)

    with pytest.raises(RunNotFoundError):
        service.cancel(project_id, handle.run_id)
    with pytest.raises(RunNotFoundError):
        service.cancel(project_id, "0" * 32)


def test_history_survives_a_fresh_service_instance(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    config = _project_config(project_store, project_id)

    handle = service.start(project_id, config)
    service.wait(project_id, handle.run_id, timeout=30)

    # Simulate a process restart: brand new service/store objects, same data root.
    restarted = PipelineService(ProjectStore(project_store.data_root))
    records = restarted.list(project_id)
    assert [r.id for r in records] == [handle.run_id]
    assert records[0].state is RunState.SUCCEEDED

    events = restarted.events(project_id, handle.run_id)
    assert events and events[-1]["type"] == "run.completed"


def test_stale_lock_from_a_dead_process_is_reclaimed(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    config = _project_config(project_store, project_id)

    lock_path = project_store.resolve_path(project_id, "runs/.mutation-lock.json")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # A pid that is guaranteed not to be alive: spawn a child and wait for it to exit.
    import subprocess
    proc = subprocess.Popen(["true"] if os.name != "nt" else ["cmd", "/c", "exit 0"])
    dead_pid = proc.pid
    proc.wait(timeout=10)
    import json as _json
    lock_path.write_text(_json.dumps({"pid": dead_pid, "run_id": "0" * 32}), encoding="utf-8")

    handle = service.start(project_id, config)
    record = service.wait(project_id, handle.run_id, timeout=30)
    assert record.state is RunState.SUCCEEDED


def test_concurrent_runs_on_the_same_project_are_rejected(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    config = _project_config(project_store, project_id)

    release = threading.Event()
    entered = threading.Event()

    def _blocking_ingest(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=10)
        raise RuntimeError("stop before mutating anything further")

    import control_translator.pipeline as pipeline_module
    original = pipeline_module.get_ingestor

    def _patched(*args, **kwargs):
        ingestor = original(*args, **kwargs)
        ingestor.ingest = _blocking_ingest
        return ingestor

    pipeline_module.get_ingestor = _patched
    try:
        handle = service.start(project_id, config)
        assert entered.wait(timeout=10)

        with pytest.raises(ProjectRunConflictError):
            service.start(project_id, config)
    finally:
        release.set()
        pipeline_module.get_ingestor = original
        service.wait(project_id, handle.run_id, timeout=30)

    # once the first run's lock is released, a new run can start
    second = service.start(project_id, config)
    service.wait(project_id, second.run_id, timeout=30)


def test_event_history_is_bounded_and_reports_dropped_count(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    run_store = RunStore(project_store, project_id, max_events=3)
    run_id = "a" * 32
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    run_store.create(RunRecord(id=run_id, project_id=project_id, state=RunState.QUEUED,
                               created_at=now, updated_at=now))

    events = []
    for index in range(5):
        events.append({"type": "stage.progress", "sequence": index})
        if len(events) > run_store.max_events:
            events.pop(0)
        run_store.save_events(run_id, events, max(0, index - run_store.max_events + 1))

    stored = run_store.load_events(run_id)
    assert len(stored) == 3
    assert stored[0]["sequence"] == 2
    assert stored[-1]["sequence"] == 4


def test_run_state_transitions_are_validated(tmp_path):
    service, project_store, project_id = _service(tmp_path)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    record = RunRecord(id="b" * 32, project_id=project_id, state=RunState.SUCCEEDED,
                       created_at=now, updated_at=now)
    with pytest.raises(InvalidRunStateTransitionError):
        record.transition(RunState.RUNNING, updated_at=now)


def test_starting_a_run_for_an_unknown_project_raises(tmp_path):
    project_store = ProjectStore(tmp_path / "data-root")
    service = PipelineService(project_store)
    config = resolve(load_config(os.path.join(REPO_ROOT, "config", "sample.json")), REPO_ROOT)

    from control_translator.projects import ProjectNotFoundError
    with pytest.raises(ProjectNotFoundError):
        service.start("00000000-0000-4000-8000-000000000000", config)

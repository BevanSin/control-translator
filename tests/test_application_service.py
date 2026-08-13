from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from control_translator.application import (
    ControlTranslatorService,
    InvalidIdentifierError,
    PipelineInProgressError,
    ProjectConfigError,
)
from control_translator.models.mapping import ControlMapping, Decision, MappingSet, PolicyRef
from control_translator.projects import ProjectStore
from control_translator.runs import PipelineService, RunRecord, RunState
from control_translator.runs.lock import ProjectMutationLock
from control_translator.runs.store import RunStore


def _write_config(tmp_path, mapping_store: str) -> str:
    config = {
        "framework": {"id": "demo", "version": "1"},
        "ingest": {"type": "fixture", "source": "data/source/NZISM-mini.csv"},
        "catalogue": {"type": "offline", "source": "data/catalogue/azure-builtins.sample.json"},
        "mapping": {
            "store": mapping_store,
            "global_ignore": ["data/mappings/global-ignore.json"],
        },
        "build": {"type": "azure"},
        "distribute": {"type": "local"},
        "out_dir": "out",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def _write_mapping(path: str) -> None:
    mapping = MappingSet(framework_id="demo", version="1")
    mapping.mappings["C-1"] = ControlMapping(
        control_id="C-1",
        decision=Decision.REVIEW,
        policies=[PolicyRef(policy_id="p1", display_name="Policy 1")],
        rationale="sensitive token=abc",
    )
    mapping.mappings["C-2"] = ControlMapping(control_id="C-2", decision=Decision.IGNORE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(mapping.to_dict(), fh)


def test_mapping_mutations_are_shared_and_typed(tmp_path):
    store_path = tmp_path / "mapping.json"
    _write_mapping(str(store_path))
    config_path = _write_config(tmp_path, str(store_path))

    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))
    approved = service.approve_controls(
        control_ids=["C-1", "C-9"], config_path=config_path, resolution_root=tmp_path
    )

    assert approved.updated == ["C-1"]
    assert approved.not_found == ["C-9"]

    rejected = service.reject_controls(
        control_ids=["C-1", "C-2"], config_path=config_path, resolution_root=tmp_path
    )
    assert rejected.updated == ["C-1"]
    assert rejected.already_updated == ["C-2"]


def test_invalid_identifiers_and_config_surface_explicit_errors(tmp_path):
    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))

    with pytest.raises(InvalidIdentifierError):
        service.search_controls(
            query="", status=None, limit=20, config_path=None, resolution_root=tmp_path
        )
    with pytest.raises(ProjectConfigError):
        service.status(config_path="missing.json", resolution_root=tmp_path)


def test_mapping_details_redacts_sensitive_rationale(tmp_path):
    store_path = tmp_path / "mapping.json"
    _write_mapping(str(store_path))
    config_path = _write_config(tmp_path, str(store_path))

    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))
    details = service.mapping_details(control_id="C-1", config_path=config_path, resolution_root=tmp_path)
    assert details["rationale"] == "[redacted]"


def test_run_and_review_raise_typed_error_when_pipeline_not_terminal(tmp_path):
    store_path = tmp_path / "mapping.json"
    _write_mapping(str(store_path))
    config_path = _write_config(tmp_path, str(store_path))

    now = datetime.now(timezone.utc).isoformat()
    running = RunRecord(
        id="a" * 32,
        project_id="project-id",
        state=RunState.RUNNING,
        created_at=now,
        updated_at=now,
        started_at=now,
    )

    class _Handle:
        run_id = "a" * 32

    class _RunningPipelineService:
        def start(self, *_args, **_kwargs):
            return _Handle()

        def wait(self, *_args, **_kwargs):
            return running

        def events(self, *_args, **_kwargs):
            return []

    service = ControlTranslatorService(
        project_store=ProjectStore(tmp_path / "projects"),
        pipeline_service=_RunningPipelineService(),
    )

    with pytest.raises(PipelineInProgressError):
        service.run(config_path=config_path, do_distribute=False, resolution_root=tmp_path)
    with pytest.raises(PipelineInProgressError):
        service.review(config_path=config_path, resolution_root=tmp_path)


def test_status_and_history_use_project_scoped_run_store(tmp_path):
    store_path = tmp_path / "mapping.json"
    _write_mapping(str(store_path))
    config_path = _write_config(tmp_path, str(store_path))
    project_store = ProjectStore(tmp_path / "projects")
    service = ControlTranslatorService(project_store=project_store)
    project_id, _config = service._load_project_config(config_path, resolution_root=tmp_path)

    run_store = RunStore(project_store, project_id)
    now = datetime.now(timezone.utc).isoformat()
    queued = RunRecord(
        id="b" * 32,
        project_id=project_id,
        state=RunState.QUEUED,
        created_at=now,
        updated_at=now,
    )
    run_store.create(queued)
    running = queued.transition(
        RunState.RUNNING,
        updated_at=now,
        started_at=now,
    )
    run_store.save_record(running)
    completed = running.transition(
        RunState.SUCCEEDED,
        updated_at=now,
        finished_at=now,
    )
    run_store.save_record(completed)

    restarted = ControlTranslatorService(
        project_store=ProjectStore(project_store.data_root),
        pipeline_service=PipelineService(ProjectStore(project_store.data_root)),
    )
    status = restarted.status(config_path=config_path, resolution_root=tmp_path)
    history = restarted.run_history(config_path=config_path, resolution_root=tmp_path)

    assert status["last_run"]["id"] == "b" * 32
    assert status["last_run"]["state"] == "succeeded"
    assert history["count"] == 1
    assert history["runs"][0]["id"] == "b" * 32


def test_mutations_are_rejected_while_project_run_lock_is_held(tmp_path):
    store_path = tmp_path / "mapping.json"
    _write_mapping(str(store_path))
    config_path = _write_config(tmp_path, str(store_path))
    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))
    project_id, _config = service._load_project_config(config_path, resolution_root=tmp_path)

    lock = ProjectMutationLock(service.project_store, project_id)
    lock.acquire("f" * 32)
    try:
        with pytest.raises(PipelineInProgressError):
            service.approve_controls(
                control_ids=["C-1"], config_path=config_path, resolution_root=tmp_path
            )
        with pytest.raises(PipelineInProgressError):
            service.reject_controls(
                control_ids=["C-1"], config_path=config_path, resolution_root=tmp_path
            )
        with pytest.raises(PipelineInProgressError):
            service.add_to_oos_register(
                policy_ids=["p1"],
                reasons=["manual exclusion"],
                register="global",
                config_path=config_path,
                resolution_root=tmp_path,
            )
    finally:
        lock.release()


def test_guidance_crud_is_project_local_and_affects_future_runs(tmp_path):
    store_path = tmp_path / "mapping.json"
    _write_mapping(str(store_path))
    config_path = _write_config(tmp_path, str(store_path))
    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))

    created = service.save_guidance(
        guidance_id=None,
        control_id="C-1",
        policy_id="p1",
        display_name="Policy 1",
        guidance="Reviewer-confirmed relevance for similar controls.",
        source="human-review",
        provenance="issue-26-test",
        config_path=config_path,
        resolution_root=tmp_path,
    )

    assert created.affects_future_runs is True
    guidance_id = created.guidance["id"]
    listed = service.list_guidance(config_path=config_path, resolution_root=tmp_path)
    assert listed["count"] == 1
    assert listed["items"][0]["include_reasoning"].startswith("Reviewer-confirmed")

    deleted = service.delete_guidance(
        guidance_ids=[guidance_id], config_path=config_path, resolution_root=tmp_path
    )
    assert deleted.deleted == [guidance_id]
    assert service.list_guidance(config_path=config_path, resolution_root=tmp_path)["count"] == 0


def test_mapping_list_filters_and_paginates_without_leaking_sensitive_rationale(tmp_path):
    store_path = tmp_path / "mapping.json"
    _write_mapping(str(store_path))
    config_path = _write_config(tmp_path, str(store_path))
    service = ControlTranslatorService(project_store=ProjectStore(tmp_path / "projects"))

    page = service.list_mappings(
        query="policy",
        status="review",
        page=1,
        page_size=1,
        config_path=config_path,
        resolution_root=tmp_path,
    )

    assert page["total"] == 1
    assert page["items"][0]["control_id"] == "C-1"
    assert page["items"][0]["rationale"] == "[redacted]"

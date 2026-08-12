from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from control_translator import cli, mcp_server
from control_translator.application import ControlTranslatorService
from control_translator.config import load_config, resolve
from control_translator.mapping import MappingStore
from control_translator.projects import ProjectPathError, ProjectStore
from control_translator.runs import RunNotFoundError, RunState


REPO_ROOT = Path(__file__).resolve().parents[1]


def _prepare_project(
    root: Path,
    store: ProjectStore,
    config_relative: str,
) -> tuple[Path, str]:
    config_path = root / config_relative
    project_id = str(uuid5(NAMESPACE_URL, str(config_path.resolve())))
    workspace = store.data_root / project_id
    config = {
        "framework": {"id": "sample", "version": "1.0"},
        "ingest": {"type": "fixture", "source": str(workspace / "source" / "standard.json")},
        "catalogue": {
            "type": "offline",
            "source": str(workspace / "source" / "builtins.json"),
        },
        "mapping": {
            "engine": "keyword",
            "store": str(workspace / "mappings" / "sample.json"),
            "global_ignore": [str(workspace / "mappings" / "global-ignore.json")],
            "auto_approve": True,
            "confidence_threshold": 0.3,
        },
        "build": {"type": "azure"},
        "distribute": {"type": "local"},
        "out_dir": str(workspace / "artifacts"),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path, project_id


def _seed_project_sources(store: ProjectStore, project_id: str) -> None:
    shutil.copyfile(
        REPO_ROOT / "tests" / "fixtures" / "sample_catalogue.json",
        store.resolve_path(project_id, "source/standard.json"),
    )
    shutil.copyfile(
        REPO_ROOT / "tests" / "fixtures" / "sample_builtins.json",
        store.resolve_path(project_id, "source/builtins.json"),
    )


def test_offline_service_journey_through_cli_and_mcp(tmp_path, monkeypatch, capsys):
    store = ProjectStore(tmp_path / "project-data")
    service = ControlTranslatorService(project_store=store)
    config_path, project_id = _prepare_project(
        tmp_path, store, "config/nzism-azure.json"
    )

    service.status(config_path=str(config_path), resolution_root=tmp_path)
    _seed_project_sources(store, project_id)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_application_service", lambda: service)
    monkeypatch.setattr(mcp_server, "get_application_service", lambda: service)

    assert cli.cmd_run(
        argparse.Namespace(config="config/nzism-azure.json", no_distribute=False)
    ) == 0
    rendered = capsys.readouterr()
    assert "framework      : sample v1.0" in rendered.out
    assert "▶  Ingest" in rendered.err

    history = json.loads(mcp_server.get_run_history())
    assert history["count"] == 1
    assert history["runs"][0]["state"] == "succeeded"
    run_id = history["runs"][0]["id"]
    events = service.pipeline_service.events(project_id, run_id)
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.completed"
    assert [event["sequence"] for event in events] == list(range(len(events)))

    mappings = json.loads(mcp_server.search_controls("sample"))
    control_id = mappings["results"][0]["control_id"]
    mutation = json.loads(mcp_server.reject_controls([control_id]))
    assert mutation["ignored"] == [control_id]
    assert json.loads(mcp_server.get_mapping_details(control_id))["decision"] == "ignore"
    mutation = json.loads(mcp_server.approve_controls([control_id]))
    assert mutation["approved"] == [control_id]
    assert json.loads(mcp_server.get_mapping_details(control_id))["decision"] == "include"

    bundle = json.loads(mcp_server.get_bundle_summary())
    assert "policySet.json" in bundle["files"]
    assert Path(bundle["bundle_path"]).is_relative_to(store.resolve_path(project_id, "artifacts"))


def test_failure_and_cancellation_are_explicit_terminal_states(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "project-data")
    application = ControlTranslatorService(project_store=store)
    config_path, project_id = _prepare_project(tmp_path, store, "config/failure.json")
    application.status(config_path=str(config_path), resolution_root=tmp_path)
    _seed_project_sources(store, project_id)
    config = resolve(load_config(str(config_path)), str(tmp_path))
    service = application.pipeline_service

    class _AcceptanceFailure(RuntimeError):
        pass

    def _fail_build(*_args, **_kwargs):
        raise _AcceptanceFailure("secret token=acceptance-test")

    with monkeypatch.context() as patch:
        patch.setattr("control_translator.pipeline.get_builder", _fail_build)
        failed_handle = service.start(project_id, config)
        failed = service.wait(project_id, failed_handle.run_id, timeout=30)

    assert failed.state is RunState.FAILED
    assert failed.error_type == "_AcceptanceFailure"
    assert failed.error_message == "[redacted]"
    assert service.events(project_id, failed.id)[-1]["type"] == "run.failed"

    cancelled_handle = service.start(project_id, config)
    service.cancel(project_id, cancelled_handle.run_id)
    cancelled = service.wait(project_id, cancelled_handle.run_id, timeout=30)

    assert cancelled.state is RunState.CANCELLED
    event_types = {event["type"] for event in service.events(project_id, cancelled.id)}
    assert "run.failed" not in event_types
    assert "stage.failed" not in event_types
    assert "run.cancelled" in event_types or "stage.cancelled" in event_types


def test_two_projects_isolate_sources_mappings_artifacts_and_deletion(tmp_path):
    store = ProjectStore(tmp_path / "project-data")
    application = ControlTranslatorService(project_store=store)
    config_a, project_a = _prepare_project(tmp_path, store, "config/a.json")
    config_b, project_b = _prepare_project(tmp_path, store, "config/b.json")

    for config_path, project_id in ((config_a, project_a), (config_b, project_b)):
        application.status(config_path=str(config_path), resolution_root=tmp_path)
        _seed_project_sources(store, project_id)
        application.run(
            config_path=str(config_path),
            do_distribute=True,
            resolution_root=tmp_path,
        )

    source_a = store.resolve_path(project_a, "source/standard.json")
    source_b = store.resolve_path(project_b, "source/standard.json")
    source_a.write_text('{"project": "A"}', encoding="utf-8")
    assert source_b.read_text(encoding="utf-8") != source_a.read_text(encoding="utf-8")

    config_a_data = resolve(load_config(str(config_a)), str(tmp_path))
    config_b_data = resolve(load_config(str(config_b)), str(tmp_path))
    mapping_a = MappingStore(config_a_data["mapping"]["store"]).load("sample", "1.0")
    mapping_b = MappingStore(config_b_data["mapping"]["store"]).load("sample", "1.0")
    control_id = next(iter(mapping_a.mappings))
    application.reject_controls(
        control_ids=[control_id],
        config_path=str(config_a),
        resolution_root=tmp_path,
    )
    assert mapping_b.mappings[control_id].decision.value == "include"
    assert MappingStore(config_a_data["mapping"]["store"]).load(
        "sample", "1.0"
    ).mappings[control_id].decision.value == "ignore"

    artifact_a = Path(application.bundle_summary(
        config_path=str(config_a), resolution_root=tmp_path
    )["bundle_path"])
    artifact_b = Path(application.bundle_summary(
        config_path=str(config_b), resolution_root=tmp_path
    )["bundle_path"])
    assert artifact_a.is_relative_to(store.resolve_path(project_a, "artifacts"))
    assert artifact_b.is_relative_to(store.resolve_path(project_b, "artifacts"))

    run_a = application.pipeline_service.list(project_a)[0]
    with pytest.raises(RunNotFoundError):
        application.pipeline_service.get(project_b, run_a.id)
    with pytest.raises(ProjectPathError):
        store.resolve_path(project_a, f"../{project_b}/project.json")

    store.delete(project_a)
    assert store.load(project_b).id == project_b
    assert artifact_b.exists()

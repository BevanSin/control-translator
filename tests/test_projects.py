from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from control_translator.projects import (
    PROJECT_SCHEMA_VERSION,
    WORKSPACE_DIRECTORIES,
    ProjectAlreadyExistsError,
    ProjectMalformedError,
    ProjectNotFoundError,
    ProjectPathError,
    ProjectStore,
    UnsupportedProjectSchemaError,
)


def test_project_lifecycle_is_isolated(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    first = store.create("First")
    second = store.create("Second")

    assert {project.id for project in store.list()} == {first.id, second.id}
    assert all((tmp_path / "projects" / first.id / directory).is_dir()
               for directory in WORKSPACE_DIRECTORIES)
    assert store.update(first.id, name="Renamed").name == "Renamed"
    assert store.load(second.id).name == "Second"

    store.delete(first.id)
    assert [project.id for project in store.list()] == [second.id]
    with pytest.raises(ProjectNotFoundError):
        store.load(first.id)


def test_project_metadata_is_schema_versioned_and_atomically_replaced(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Example")
    metadata = tmp_path / project.id / "project.json"

    assert json.loads(metadata.read_text(encoding="utf-8"))["schema_version"] == PROJECT_SCHEMA_VERSION
    assert not list(metadata.parent.glob(".project-*.tmp"))

    metadata.write_text(json.dumps({"schema_version": PROJECT_SCHEMA_VERSION + 1}), encoding="utf-8")
    with pytest.raises(UnsupportedProjectSchemaError):
        store.load(project.id)


def test_invalid_project_states_raise_typed_errors(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project_id = str(uuid4())
    project = store.create("Example", project_id)

    with pytest.raises(ProjectAlreadyExistsError):
        store.create("Duplicate", project.id)
    with pytest.raises(ProjectPathError):
        store.load("../other")

    (tmp_path / project.id / "project.json").unlink()
    with pytest.raises(ProjectMalformedError):
        store.load(project.id)

    (tmp_path / project.id / "project.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ProjectMalformedError):
        store.load(project.id)


def test_project_metadata_id_must_match_its_directory(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Example")
    metadata = tmp_path / project.id / "project.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    data["id"] = str(uuid4())
    metadata.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ProjectMalformedError):
        store.load(project.id)


def test_resolved_paths_cannot_escape_or_overlap_projects(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    first = store.create("First")
    second = store.create("Second")

    assert store.resolve_path(first.id, "source/standard.csv") == (
        tmp_path / first.id / "source" / "standard.csv"
    )
    with pytest.raises(ProjectPathError):
        store.resolve_path(first.id, "../" + second.id + "/project.json")
    with pytest.raises(ProjectPathError):
        store.resolve_path(first.id, (tmp_path / second.id / "project.json"))

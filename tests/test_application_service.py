from __future__ import annotations

import json

import pytest

from control_translator.application import (
    ControlTranslatorService,
    InvalidIdentifierError,
    ProjectConfigError,
)
from control_translator.models.mapping import ControlMapping, Decision, MappingSet, PolicyRef
from control_translator.projects import ProjectStore


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

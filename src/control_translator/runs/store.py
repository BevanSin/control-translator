"""Durable, bounded run metadata and sanitized event history for one project.

Everything is persisted beneath the project's own ``runs/`` workspace directory
(via ``ProjectStore.resolve_path``), so it is subject to the same isolation and
path-escape protections as the rest of a project's data.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..projects import ProjectStore
from .errors import RunMalformedError, RunNotFoundError, UnsupportedRunSchemaError
from .models import RunRecord, validate_run_id

DEFAULT_MAX_EVENTS = 500
EVENTS_SCHEMA_VERSION = 1


def _atomic_write_json(target: Path, payload: Any) -> None:
    directory = target.parent
    fd, temporary = tempfile.mkstemp(prefix=".run-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RunStore:
    """Persists run metadata and bounded event history for one project."""

    def __init__(self, project_store: ProjectStore, project_id: str,
                 *, max_events: int = DEFAULT_MAX_EVENTS):
        self._project_store = project_store
        self.project_id = project_id
        self.max_events = max_events

    def _run_dir(self, run_id: str, *, must_exist: bool = True) -> Path:
        validate_run_id(run_id)
        path = self._project_store.resolve_path(self.project_id, f"runs/{run_id}")
        if must_exist and not path.exists():
            raise RunNotFoundError(f"Run {run_id} does not exist for project {self.project_id}.")
        return path

    def create(self, record: RunRecord) -> None:
        validate_run_id(record.id)
        path = self._project_store.resolve_path(self.project_id, f"runs/{record.id}")
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.save_record(record)

    def save_record(self, record: RunRecord) -> None:
        run_dir = self._run_dir(record.id)
        _atomic_write_json(run_dir / "run.json", record.to_dict())

    def load_record(self, run_id: str) -> RunRecord:
        run_dir = self._run_dir(run_id)
        metadata = run_dir / "run.json"
        try:
            with metadata.open(encoding="utf-8") as file:
                return RunRecord.from_dict(json.load(file))
        except FileNotFoundError as exc:
            raise RunNotFoundError(f"Run {run_id} has no metadata.") from exc

    def list_records(self) -> list[RunRecord]:
        runs_root = self._project_store.resolve_path(self.project_id, "runs")
        if not runs_root.exists():
            return []
        records = []
        for entry in sorted(runs_root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                validate_run_id(entry.name)
            except Exception:
                continue
            records.append(self.load_record(entry.name))
        return sorted(records, key=lambda r: r.created_at)

    def save_events(self, run_id: str, events: list[dict], dropped: int) -> None:
        run_dir = self._run_dir(run_id)
        _atomic_write_json(run_dir / "events.json", {
            "schema_version": EVENTS_SCHEMA_VERSION,
            "dropped_event_count": dropped,
            "events": events,
        })

    def load_events(self, run_id: str) -> list[dict]:
        _dropped, events = self._load_event_history(run_id)
        return events

    def load_dropped_event_count(self, run_id: str) -> int:
        dropped, _events = self._load_event_history(run_id)
        return dropped

    def _load_event_history(self, run_id: str) -> tuple[int, list[dict]]:
        run_dir = self._run_dir(run_id)
        events_path = run_dir / "events.json"
        if not events_path.exists():
            return 0, []
        with events_path.open(encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise RunMalformedError("Event history must be a JSON object.")
        version = data.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise RunMalformedError("Event history has no integer schema_version.")
        if version != EVENTS_SCHEMA_VERSION:
            raise UnsupportedRunSchemaError(
                f"Event history schema version {version} is unsupported "
                f"(expected {EVENTS_SCHEMA_VERSION}).")
        events = data.get("events")
        if not isinstance(events, list):
            raise RunMalformedError("Event history 'events' must be a list.")
        dropped = data.get("dropped_event_count", 0)
        if not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
            raise RunMalformedError("Event history has an invalid dropped_event_count.")
        return dropped, list(events)

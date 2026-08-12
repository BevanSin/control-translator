"""Run identity, state machine, and schema-versioned run metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from .errors import (InvalidRunStateTransitionError, RunMalformedError,
                     UnsupportedRunSchemaError)

RUN_SCHEMA_VERSION = 1

# run ids are opaque hex tokens (uuid4().hex) — validated to keep them safe as
# path segments beneath a project's runs/ directory.
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class RunState(str, Enum):
    """Explicit, terminal-aware lifecycle states for one pipeline run."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED)


# Valid state transitions. A run may only move forward; terminal states are final.
_VALID_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.RUNNING, RunState.CANCELLED, RunState.FAILED},
    RunState.RUNNING: {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED},
    RunState.SUCCEEDED: set(),
    RunState.FAILED: set(),
    RunState.CANCELLED: set(),
}


def validate_transition(current: RunState, target: RunState) -> None:
    if target == current:
        return
    if target not in _VALID_TRANSITIONS.get(current, set()):
        raise InvalidRunStateTransitionError(
            f"Run cannot move from {current.value!r} to {target.value!r}.")


def validate_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise RunMalformedError("Run identifier must be a 32-character lowercase hex token.")
    return run_id


@dataclass(frozen=True)
class RunRecord:
    """Schema-versioned, durable metadata describing one pipeline run."""

    id: str
    project_id: str
    state: RunState
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    dropped_event_count: int = 0
    schema_version: int = RUN_SCHEMA_VERSION

    def transition(self, target: RunState, *, updated_at: str, **fields: Any) -> "RunRecord":
        validate_transition(self.state, target)
        return replace(self, state=target, updated_at=updated_at, **fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "project_id": self.project_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "dropped_event_count": self.dropped_event_count,
        }

    @classmethod
    def from_dict(cls, data: object) -> "RunRecord":
        if not isinstance(data, dict):
            raise RunMalformedError("Run metadata must be a JSON object.")
        version = data.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise RunMalformedError("Run metadata has no integer schema_version.")
        if version != RUN_SCHEMA_VERSION:
            raise UnsupportedRunSchemaError(
                f"Run schema version {version} is unsupported (expected {RUN_SCHEMA_VERSION}).")
        run_id = validate_run_id(data.get("id"))
        project_id = data.get("project_id")
        state_value = data.get("state")
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        if not all(isinstance(value, str) and value for value in (
            project_id, created_at, updated_at
        )):
            raise RunMalformedError("Run metadata has missing or invalid fields.")
        try:
            state = RunState(state_value)
        except ValueError as exc:
            raise RunMalformedError(f"Run metadata has an invalid state: {state_value!r}.") from exc
        started_at = data.get("started_at")
        finished_at = data.get("finished_at")
        error_type = data.get("error_type")
        error_message = data.get("error_message")
        dropped = data.get("dropped_event_count", 0)
        if not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
            raise RunMalformedError("Run metadata has an invalid dropped_event_count.")
        for name, value in (("started_at", started_at), ("finished_at", finished_at),
                            ("error_type", error_type), ("error_message", error_message)):
            if value is not None and not isinstance(value, str):
                raise RunMalformedError(f"Run metadata field {name!r} must be a string or null.")
        return cls(run_id, project_id, state, created_at, updated_at,
                   started_at, finished_at, error_type, error_message, dropped, version)

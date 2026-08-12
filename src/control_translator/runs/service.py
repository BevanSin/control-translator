"""Project-scoped pipeline run service: start, inspect, list, cancel.

``PipelineService`` wraps ``run_pipeline`` with:

- stable run identifiers and an explicit queued/running/succeeded/failed/cancelled
  state machine,
- durable, bounded run metadata and sanitized event history under the project's
  own workspace (recoverable after a process restart),
- a crash-safe per-project mutation lock so two runs can never mutate the same
  project's mapping store concurrently,
- cooperative cancellation at safe stage boundaries only.

Cancellation limitation: a run only observes a cancellation request at the start
of a stage, or right after a mapping checkpoint has saved the mapping store. A
single external LLM classification call already in flight is never interrupted;
cancelling never leaves the mapping store partially written mid-control.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4

from ..events import PipelineEvent
from ..pipeline import PipelineCancelledError, run_pipeline
from ..projects import ProjectStore
from .errors import RunNotFoundError
from .lock import ProjectMutationLock
from .models import RUN_SCHEMA_VERSION, RunRecord, RunState
from .store import DEFAULT_MAX_EVENTS, RunStore

MAX_ERROR_MESSAGE_LENGTH = 200

# Exception text can embed endpoints, hostnames, or key-shaped substrings from a
# config value. Mirror events.py's caution: redact wholesale rather than parse.
_SENSITIVE_MESSAGE = re.compile(
    r"(key|token|secret|password|passwd|credential|connection[_-]?string|"
    r"signature|authorization|api[_-]?key|https?://)",
    re.IGNORECASE,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_error_message(exc: BaseException) -> str | None:
    text = str(exc).strip()
    if not text:
        return None
    if _SENSITIVE_MESSAGE.search(text):
        return "[redacted]"
    return text[:MAX_ERROR_MESSAGE_LENGTH]


@dataclass(frozen=True)
class RunHandle:
    """Identifies one run started by the service."""

    run_id: str
    project_id: str


class PipelineService:
    """Starts and tracks pipeline runs scoped to one project at a time."""

    def __init__(self, project_store: ProjectStore, *, max_events: int = DEFAULT_MAX_EVENTS):
        self._project_store = project_store
        self._max_events = max_events
        self._guard = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def start(self, project_id: str, config: dict, *, do_distribute: bool = True) -> RunHandle:
        """Start a run for ``project_id``. Raises ``ProjectRunConflictError`` if one is active."""
        self._project_store.load(project_id)  # raises ProjectNotFoundError if missing
        run_store = RunStore(self._project_store, project_id, max_events=self._max_events)
        lock = ProjectMutationLock(self._project_store, project_id)
        run_id = uuid4().hex
        lock.acquire(run_id)
        try:
            now = _timestamp()
            record = RunRecord(id=run_id, project_id=project_id, state=RunState.QUEUED,
                               created_at=now, updated_at=now, schema_version=RUN_SCHEMA_VERSION)
            run_store.create(record)
        except BaseException:
            lock.release()
            raise

        cancel_event = threading.Event()
        with self._guard:
            self._cancel_events[run_id] = cancel_event

        thread = threading.Thread(
            target=self._execute,
            args=(project_id, run_id, config, do_distribute, cancel_event, lock, run_store),
            daemon=True,
            name=f"pipeline-run-{run_id}",
        )
        with self._guard:
            self._threads[run_id] = thread
        thread.start()
        return RunHandle(run_id=run_id, project_id=project_id)

    def get(self, project_id: str, run_id: str) -> RunRecord:
        return RunStore(self._project_store, project_id, max_events=self._max_events).load_record(run_id)

    def list(self, project_id: str) -> list[RunRecord]:
        return RunStore(self._project_store, project_id, max_events=self._max_events).list_records()

    def events(self, project_id: str, run_id: str) -> list[dict]:
        return RunStore(self._project_store, project_id, max_events=self._max_events).load_events(run_id)

    def cancel(self, project_id: str, run_id: str) -> None:
        """Request cooperative cancellation of a running run.

        This only sets a flag observed at the next safe stage boundary — see the
        module docstring for what "safe" means. Requesting cancellation of a run
        that is not tracked in this process (for example after a restart) raises
        ``RunNotFoundError``; the caller can still inspect its persisted state via
        ``get``.
        """
        with self._guard:
            cancel_event = self._cancel_events.get(run_id)
        if cancel_event is None:
            # Confirm the run exists at all before reporting it as not-cancellable.
            self.get(project_id, run_id)
            raise RunNotFoundError(
                f"Run {run_id} is not active in this process and cannot be cancelled.")
        cancel_event.set()

    def wait(self, project_id: str, run_id: str, *, timeout: float | None = None) -> RunRecord:
        """Block until ``run_id`` reaches a terminal state (or ``timeout`` elapses)."""
        with self._guard:
            thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout)
        return self.get(project_id, run_id)

    # ── run execution (background thread) ───────────────────────────────────

    def _execute(self, project_id: str, run_id: str, config: dict, do_distribute: bool,
                cancel_event: threading.Event, lock: ProjectMutationLock,
                run_store: RunStore) -> None:
        events: list[dict] = []
        dropped = 0

        def _sink(event: PipelineEvent) -> None:
            nonlocal dropped
            events.append(event.to_dict())
            if len(events) > self._max_events:
                events.pop(0)
                dropped += 1
            run_store.save_events(run_id, events, dropped)

        def _check_cancel() -> None:
            if cancel_event.is_set():
                raise PipelineCancelledError(f"run {run_id} cancellation requested")

        record = run_store.load_record(run_id)
        record = record.transition(RunState.RUNNING, updated_at=_timestamp(),
                                   started_at=_timestamp())
        run_store.save_record(record)

        error_type: str | None = None
        error_message: str | None = None
        try:
            run_pipeline(config, do_distribute=do_distribute, event_sink=_sink,
                        run_id=run_id, cancel_check=_check_cancel)
        except PipelineCancelledError:
            final_state = RunState.CANCELLED
        except BaseException as exc:  # noqa: BLE001 - faithfully retain any pipeline failure
            final_state = RunState.FAILED
            error_type = type(exc).__name__
            error_message = _sanitize_error_message(exc)
        else:
            final_state = RunState.SUCCEEDED
        finally:
            lock.release()
            with self._guard:
                self._cancel_events.pop(run_id, None)
                self._threads.pop(run_id, None)

        record = run_store.load_record(run_id)
        record = record.transition(final_state, updated_at=_timestamp(), finished_at=_timestamp(),
                                   error_type=error_type, error_message=error_message)
        run_store.save_record(record)

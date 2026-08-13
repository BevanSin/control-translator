"""Project-scoped pipeline run service: start, inspect, list, cancel.

``PipelineService`` wraps ``run_pipeline`` with:

- stable run identifiers and an explicit queued/running/succeeded/failed/cancelled
  state machine,
- durable, bounded run metadata and sanitized event history under the project's
  own workspace (recoverable after a process restart),
- a crash-safe per-project mutation lock so two runs can never mutate the same
  project's mapping store concurrently,
- cooperative cancellation at safe stage boundaries only,
- reconciliation of runs left non-terminal by a crash or restart.

Cancellation limitation: a run only observes a cancellation request at the start
of a stage, or right after a mapping checkpoint has saved the mapping store. A
single external LLM classification call already in flight is never interrupted;
cancelling never leaves the mapping store partially written mid-control.

Active run tracking (threads, cancellation flags) is always keyed by the pair
``(project_id, run_id)`` — a run id from one project must never be able to
observe or cancel a same-named run id in another project.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from ..events import PipelineEvent
from ..pipeline import PipelineCancelledError, run_pipeline
from ..projects import ProjectStore
from .errors import RunNotFoundError
from .lock import ProjectMutationLock, ResourceMutationLock
from .models import RUN_SCHEMA_VERSION, RunRecord, RunState
from .store import DEFAULT_MAX_EVENTS, RunStore

MAX_ERROR_MESSAGE_LENGTH = 200

# Exception text can embed endpoints, hostnames, or key-shaped substrings from a
# config value. Mirror events.py's caution: redact wholesale rather than parse.
_SENSITIVE_MESSAGE = re.compile(
    r"(key|token|secret|password|passwd|credential|connection[_-]?string|"
    r"signature|authorization|api[_-]?key|https?://|"
    r"(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+|/(?:[^/\s]+/)+[^/\s]*))",
    re.IGNORECASE,
)

# Diagnostics recorded on a run that a fresh service instance discovers was left
# non-terminal by a crash, kill, or process restart — never assumed successful.
_INTERRUPTED_ERROR_TYPE = "RunInterrupted"
_INTERRUPTED_ERROR_MESSAGE = (
    "Run did not reach a terminal state before its process exited or restarted.")
_TERMINAL_EVENT_STATES = {
    "run.completed": RunState.SUCCEEDED,
    "run.failed": RunState.FAILED,
    "run.cancelled": RunState.CANCELLED,
}


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
        self._threads: dict[tuple[str, str], threading.Thread] = {}
        self._cancel_events: dict[tuple[str, str], threading.Event] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def start(self, project_id: str, config: dict, *, do_distribute: bool = True) -> RunHandle:
        """Start a run for ``project_id``. Raises ``ProjectRunConflictError`` if one is active."""
        self._project_store.load(project_id)  # raises ProjectNotFoundError if missing
        run_store = RunStore(self._project_store, project_id, max_events=self._max_events)
        self._reconcile_orphaned_runs(project_id, run_store)
        lock = ProjectMutationLock(self._project_store, project_id)
        resource_lock = ResourceMutationLock(config["mapping"]["store"])
        run_id = uuid4().hex
        key = (project_id, run_id)
        lock.acquire(run_id)
        try:
            resource_lock.acquire(run_id)
            now = _timestamp()
            record = RunRecord(id=run_id, project_id=project_id, state=RunState.QUEUED,
                               created_at=now, updated_at=now, schema_version=RUN_SCHEMA_VERSION)
            run_store.create(record)

            cancel_event = threading.Event()
            with self._guard:
                self._cancel_events[key] = cancel_event

            thread = threading.Thread(
                target=self._execute,
                args=(
                    project_id,
                    run_id,
                    config,
                    do_distribute,
                    cancel_event,
                    lock,
                    resource_lock,
                    run_store,
                ),
                daemon=True,
                name=f"pipeline-run-{run_id}",
            )
            with self._guard:
                self._threads[key] = thread
            try:
                thread.start()
            except BaseException:
                with self._guard:
                    self._threads.pop(key, None)
                    self._cancel_events.pop(key, None)
                raise
        except BaseException:
            # Any failure before the worker thread is safely running must not
            # leave the project lock held, or the record stuck non-terminal.
            now = _timestamp()
            try:
                failed = run_store.load_record(run_id)
                failed = failed.transition(
                    RunState.FAILED, updated_at=now, finished_at=now,
                    error_type="RunStartFailed",
                    error_message="Run could not be started; see server logs.")
                run_store.save_record(failed)
            except Exception:
                pass  # record may not exist yet if run_store.create() itself failed
            resource_lock.release()
            lock.release()
            raise
        return RunHandle(run_id=run_id, project_id=project_id)

    def get(self, project_id: str, run_id: str) -> RunRecord:
        run_store = RunStore(self._project_store, project_id, max_events=self._max_events)
        return self._reconcile_record(project_id, run_store, run_store.load_record(run_id))

    def list(self, project_id: str) -> list[RunRecord]:
        run_store = RunStore(self._project_store, project_id, max_events=self._max_events)
        return self._reconcile_orphaned_runs(project_id, run_store)

    def events(self, project_id: str, run_id: str) -> list[dict]:
        return RunStore(self._project_store, project_id, max_events=self._max_events).load_events(run_id)

    def dropped_event_count(self, project_id: str, run_id: str) -> int:
        return RunStore(self._project_store, project_id, max_events=self._max_events).load_dropped_event_count(run_id)

    def cancel(self, project_id: str, run_id: str) -> None:
        """Request cooperative cancellation of a running run.

        This only sets a flag observed at the next safe stage boundary — see the
        module docstring for what "safe" means. Requesting cancellation of a run
        that is not tracked in this process (for example after a restart) raises
        ``RunNotFoundError``; the caller can still inspect its persisted state via
        ``get``. Cancelling is scoped to ``(project_id, run_id)`` — it can never
        affect a same-named run id belonging to a different project.
        """
        key = (project_id, run_id)
        with self._guard:
            cancel_event = self._cancel_events.get(key)
        if cancel_event is None:
            # Confirm the run exists at all (scoped to this project) before
            # reporting it as not-cancellable.
            self.get(project_id, run_id)
            raise RunNotFoundError(
                f"Run {run_id} is not active in this process and cannot be cancelled.")
        cancel_event.set()

    def wait(self, project_id: str, run_id: str, *, timeout: float | None = None) -> RunRecord:
        """Block until ``run_id`` reaches a terminal state (or ``timeout`` elapses)."""
        key = (project_id, run_id)
        with self._guard:
            thread = self._threads.get(key)
        if thread is not None:
            thread.join(timeout)
        return self.get(project_id, run_id)

    # ── crash recovery ───────────────────────────────────────────────────────

    def _reconcile_orphaned_runs(self, project_id: str, run_store: RunStore) -> list[RunRecord]:
        """Resolve every non-terminal run this process is not actively tracking.

        A ``queued``/``running`` record can be left behind by a crash, a kill
        signal, or simply because this ``PipelineService`` was constructed fresh
        (for example after a restart). Such a record is never left to look
        perpetually in-flight: it is moved to ``failed`` with sanitized,
        clearly-labelled diagnostics the first time it is observed again.
        """
        records = run_store.list_records()
        return [self._reconcile_record(project_id, run_store, record) for record in records]

    def _reconcile_record(self, project_id: str, run_store: RunStore,
                          record: RunRecord) -> RunRecord:
        if record.state.is_terminal:
            return record
        with self._guard:
            active = (project_id, record.id) in self._threads
        if active:
            return record
        event_state = self._terminal_state_from_events(run_store, record.id)
        if event_state is not None:
            now = _timestamp()
            error_type = record.error_type if event_state is RunState.FAILED else None
            error_message = record.error_message if event_state is RunState.FAILED else None
            reconciled = record.transition(
                event_state, updated_at=now, finished_at=(record.finished_at or now),
                error_type=error_type, error_message=error_message,
                dropped_event_count=run_store.load_dropped_event_count(record.id))
            run_store.save_record(reconciled)
            return reconciled
        now = _timestamp()
        reconciled = record.transition(
            RunState.FAILED, updated_at=now, finished_at=(record.finished_at or now),
            error_type=_INTERRUPTED_ERROR_TYPE, error_message=_INTERRUPTED_ERROR_MESSAGE)
        run_store.save_record(reconciled)
        return reconciled

    def _terminal_state_from_events(self, run_store: RunStore, run_id: str) -> RunState | None:
        terminal: tuple[int, RunState] | None = None
        for event in run_store.load_events(run_id):
            state = _TERMINAL_EVENT_STATES.get(str(event.get("type", "")))
            if state is None:
                continue
            sequence = event.get("sequence", -1)
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                sequence = -1
            if terminal is None or sequence >= terminal[0]:
                terminal = (sequence, state)
        return terminal[1] if terminal is not None else None

    # ── run execution (background thread) ───────────────────────────────────

    def _execute(self, project_id: str, run_id: str, config: dict, do_distribute: bool,
                cancel_event: threading.Event, lock: ProjectMutationLock,
                resource_lock: ResourceMutationLock,
                run_store: RunStore) -> None:
        key = (project_id, run_id)
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

        try:
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

            record = run_store.load_record(run_id)
            record = record.transition(
                final_state, updated_at=_timestamp(), finished_at=_timestamp(),
                error_type=error_type, error_message=error_message,
                dropped_event_count=dropped)
            run_store.save_record(record)
        finally:
            # The terminal record must already be durable before the run stops
            # being observable as "active": otherwise a concurrent list()/get()
            # racing this cleanup could see a non-terminal record with no active
            # tracking and wrongly reconcile it as interrupted.
            resource_lock.release()
            lock.release()
            with self._guard:
                self._cancel_events.pop(key, None)
                self._threads.pop(key, None)

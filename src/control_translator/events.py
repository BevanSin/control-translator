"""Structured pipeline progress and result events.

The pipeline emits typed events so the CLI, MCP server, and web API can observe
progress without parsing console text. Event payloads carry stable identifiers
and small scalar summary fields only — never source control prose, credentials,
or raw exception text.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, TextIO
from uuid import uuid4

EVENT_SCHEMA_VERSION = 1
MAX_SUMMARY_TEXT = 200

# Summary keys whose values are never safe to emit (config placeholders resolve
# real endpoints/keys into the running config).
_SENSITIVE_KEY = re.compile(
    r"(key|token|secret|password|passwd|credential|connection[_-]?string|"
    r"signature|authorization|api[_-]?key)",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"


class Stage(str, Enum):
    """Stable identifiers for the six pipeline stages."""

    INGEST = "ingest"
    CATALOGUE = "catalogue"
    MAP = "map"
    BUILD = "build"
    VALIDATE = "validate"
    DISTRIBUTE = "distribute"


class EventType(str, Enum):
    """Stable identifiers for the structured pipeline events."""

    RUN_STARTED = "run.started"
    STAGE_STARTED = "stage.started"
    STAGE_PROGRESS = "stage.progress"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    WARNING = "run.warning"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class WarningKind(str, Enum):
    """Stable identifiers for structured warnings."""

    VALIDATION = "validation"
    OOS_STALENESS = "oos-staleness"
    INTERRUPTED = "interrupted"


def _safe_value(key: str, value: Any) -> Any | None:
    """Return a payload-safe value, or ``None`` when the value must be dropped."""
    if _SENSITIVE_KEY.search(key):
        return _REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value[:MAX_SUMMARY_TEXT]
    return None


def safe_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep scalar, non-sensitive summary fields; drop everything else."""
    clean: dict[str, Any] = {}
    for key, value in summary.items():
        safe = _safe_value(key, value)
        if safe is not None or value is None:
            clean[key] = safe
    return clean


@dataclass(frozen=True)
class PipelineEvent:
    """One structured pipeline observation."""

    type: EventType
    run_id: str
    sequence: int
    timestamp: str
    stage: Stage | None = None
    message: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    schema_version: int = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", safe_summary(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": self.type.value,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "stage": self.stage.value if self.stage else None,
            "message": self.message,
            "summary": dict(self.summary),
        }


EventSink = Callable[[PipelineEvent], None]


class EventEmitter:
    """Assigns stable run identifiers and monotonic sequence numbers to events."""

    def __init__(self, sink: EventSink | None = None, run_id: str | None = None) -> None:
        self._sink = sink
        self.run_id = run_id or uuid4().hex
        self._sequence = 0

    def emit(self, event_type: EventType, *, stage: Stage | None = None,
             message: str = "", **summary: Any) -> PipelineEvent:
        event = PipelineEvent(
            type=event_type,
            run_id=self.run_id,
            sequence=self._sequence,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            stage=stage,
            message=message,
            summary=summary,
        )
        self._sequence += 1
        if self._sink is not None:
            self._sink(event)
        return event

    # ── convenience wrappers ─────────────────────────────────────────────────
    def run_started(self, **summary: Any) -> PipelineEvent:
        return self.emit(EventType.RUN_STARTED, **summary)

    def stage_started(self, stage: Stage, message: str = "", **summary: Any) -> PipelineEvent:
        return self.emit(EventType.STAGE_STARTED, stage=stage, message=message, **summary)

    def stage_progress(self, stage: Stage, message: str = "", **summary: Any) -> PipelineEvent:
        return self.emit(EventType.STAGE_PROGRESS, stage=stage, message=message, **summary)

    def stage_completed(self, stage: Stage, message: str = "", **summary: Any) -> PipelineEvent:
        return self.emit(EventType.STAGE_COMPLETED, stage=stage, message=message, **summary)

    def stage_failed(self, stage: Stage | None, error: BaseException) -> PipelineEvent:
        # Only the exception type is emitted — exception text may embed
        # endpoints, file paths, or standard prose.
        return self.emit(EventType.STAGE_FAILED, stage=stage,
                         error_type=type(error).__name__)

    def warning(self, kind: WarningKind, *, stage: Stage | None = None,
                message: str = "", **summary: Any) -> PipelineEvent:
        return self.emit(EventType.WARNING, stage=stage, message=message,
                         kind=kind.value, **summary)

    def run_completed(self, **summary: Any) -> PipelineEvent:
        return self.emit(EventType.RUN_COMPLETED, **summary)

    def run_failed(self, error: BaseException, *, stage: Stage | None = None) -> PipelineEvent:
        return self.emit(EventType.RUN_FAILED, stage=stage,
                         error_type=type(error).__name__)


class ConsoleEventRenderer:
    """Renders events as the human-readable console progress the CLI has always shown."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    @property
    def stream(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stderr

    def _write(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def __call__(self, event: PipelineEvent) -> None:
        handler = getattr(self, f"_on_{event.type.name.lower()}", None)
        if handler is not None:
            handler(event)

    def _on_stage_started(self, event: PipelineEvent) -> None:
        if event.message:
            self._write(f"\n▶  {event.message}")

    def _on_stage_completed(self, event: PipelineEvent) -> None:
        if event.stage is Stage.VALIDATE:
            lint_errors = event.summary.get("lint_errors", 0)
            if lint_errors:
                self._write(f"   ⚠  {lint_errors} lint warning(s)")
            return
        if event.message:
            self._write(f"   ✓  {event.message}")

    def _on_warning(self, event: PipelineEvent) -> None:
        kind = event.summary.get("kind")
        if kind == WarningKind.OOS_STALENESS.value:
            self._write(f"\n   ⚠  {event.summary.get('count', 0)} OOS entries need review "
                        f"(see out/oos-reconsidered.json)")
        elif kind == WarningKind.INTERRUPTED.value:
            self._write("\n\n   ⚠  interrupted — saving progress to mapping store...")

    def _on_run_completed(self, event: PipelineEvent) -> None:
        if event.message:
            self._write(f"\n   {event.message}")
        self._write("")

"""Structured pipeline event contract: ordering, payload safety, console rendering."""
import os

import pytest

from control_translator.config import load_config, resolve
from control_translator.events import (EVENT_SCHEMA_VERSION, ConsoleEventRenderer,
                                       EventEmitter, EventType, PipelineEvent, Stage,
                                       WarningKind, safe_summary)
from control_translator.pipeline import run_pipeline

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sample_config(tmp_out: str) -> dict:
    config = resolve(load_config(os.path.join(REPO_ROOT, "config", "sample.json")), REPO_ROOT)
    config["out_dir"] = tmp_out
    config["mapping"]["store"] = os.path.join(tmp_out, "mapping.json")
    return config


def _kinds(events: list[PipelineEvent]) -> list[tuple[str, str | None]]:
    return [(e.type.value, e.stage.value if e.stage else None) for e in events
            if e.type is not EventType.STAGE_PROGRESS]


def test_successful_run_emits_ordered_stage_events(tmp_path):
    events: list[PipelineEvent] = []
    result = run_pipeline(_sample_config(str(tmp_path)), event_sink=events.append)

    assert _kinds(events) == [
        ("run.started", None),
        ("stage.started", "ingest"),
        ("stage.completed", "ingest"),
        ("stage.started", "catalogue"),
        ("stage.completed", "catalogue"),
        ("stage.started", "map"),
        ("stage.completed", "map"),
        ("stage.started", "build"),
        ("stage.completed", "build"),
        ("stage.started", "validate"),
        ("stage.completed", "validate"),
        ("stage.started", "distribute"),
        ("stage.completed", "distribute"),
        ("run.completed", None),
    ]

    # stable identifiers: one run id, gapless monotonic sequence numbers
    assert {e.run_id for e in events} == {result.run_id}
    assert [e.sequence for e in events] == list(range(len(events)))
    assert {e.schema_version for e in events} == {EVENT_SCHEMA_VERSION}
    catalogue_started = next(
        event
        for event in events
        if event.type is EventType.STAGE_STARTED and event.stage is Stage.CATALOGUE
    )
    assert catalogue_started.summary["from_cache"] is False

    completion = events[-1]
    assert completion.summary["approved"] == len(result.mapping.approved())
    assert completion.summary["pending"] == len(result.mapping.pending_review())
    assert completion.summary["lint_errors"] == 0
    assert completion.summary["published"] is True
    assert completion.to_dict()["type"] == "run.completed"


def test_failed_run_emits_stage_and_run_failure(tmp_path, monkeypatch):
    class _Boom(RuntimeError):
        pass

    def _explode(*_args, **_kwargs):
        raise _Boom("secret-endpoint https://contoso.example/api")

    monkeypatch.setattr("control_translator.pipeline.get_builder", _explode)

    events: list[PipelineEvent] = []
    with pytest.raises(_Boom):
        run_pipeline(_sample_config(str(tmp_path)), event_sink=events.append)

    assert _kinds(events)[-3:] == [
        ("stage.started", "build"),
        ("stage.failed", "build"),
        ("run.failed", "build"),
    ]
    assert not any(e.type is EventType.RUN_COMPLETED for e in events)
    failure = events[-1]
    assert failure.summary == {"error_type": "_Boom"}
    assert "contoso" not in failure.message


def test_validation_warnings_are_structured(tmp_path, monkeypatch):
    monkeypatch.setattr("control_translator.validate.AzureValidator.lint",
                        lambda self, bundle: ["duplicate policyDefinitionReferenceId: p1"])

    events: list[PipelineEvent] = []
    result = run_pipeline(_sample_config(str(tmp_path)), event_sink=events.append,
                          do_distribute=False)

    warnings = [e for e in events if e.type is EventType.WARNING]
    assert [w.summary["kind"] for w in warnings] == [WarningKind.VALIDATION.value]
    assert warnings[0].stage is Stage.VALIDATE
    assert warnings[0].summary["index"] == 0
    assert warnings[0].message == result.lint_errors[0]

    validate_done = [e for e in events if e.type is EventType.STAGE_COMPLETED
                     and e.stage is Stage.VALIDATE][0]
    assert validate_done.summary["lint_errors"] == 1
    assert not any(e.stage is Stage.DISTRIBUTE for e in events)


def test_summary_payloads_drop_prose_and_redact_secrets():
    summary = safe_summary({
        "controls": 12,
        "api_key": "abcd1234",
        "foundry_token": "t",
        "policies": ["a", "b"],
        "rationale": "x" * 500,
        "published": None,
    })
    assert summary["controls"] == 12
    assert summary["api_key"] == "[redacted]"
    assert summary["foundry_token"] == "[redacted]"
    assert "policies" not in summary          # non-scalar payloads are dropped
    assert len(summary["rationale"]) == 200   # long text is truncated
    assert summary["published"] is None


def test_console_renderer_reproduces_cli_progress(capsys):
    emitter = EventEmitter(ConsoleEventRenderer(), run_id="run-1")
    emitter.run_started()
    emitter.stage_started(Stage.INGEST, "Ingest  — SAMPLE v1.0")
    emitter.stage_completed(Stage.INGEST, "2 controls across 2 chapters")
    emitter.stage_started(Stage.VALIDATE)
    emitter.warning(WarningKind.VALIDATION, stage=Stage.VALIDATE, message="bad group")
    emitter.stage_completed(Stage.VALIDATE, "", lint_errors=1)
    emitter.warning(WarningKind.OOS_STALENESS, stage=Stage.BUILD, count=3)
    emitter.run_completed(message="⏱  Completed in 0s")

    assert capsys.readouterr().err == (
        "\n▶  Ingest  — SAMPLE v1.0\n"
        "   ✓  2 controls across 2 chapters\n"
        "   ⚠  1 lint warning(s)\n"
        "\n   ⚠  3 OOS entries need review (see out/oos-reconsidered.json)\n"
        "\n   ⏱  Completed in 0s\n"
        "\n"
    )

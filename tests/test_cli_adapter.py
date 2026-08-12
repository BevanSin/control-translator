from __future__ import annotations

import argparse

from control_translator.application import PendingItem, ReviewSummary, RunSummary
from control_translator.events import EventType, PipelineEvent, Stage
from control_translator import cli


class _FakeService:
    def __init__(self):
        self.run_calls = []
        self.review_calls = []

    def run(self, *, config_path, do_distribute, resolution_root, event_sink=None):
        self.run_calls.append((config_path, do_distribute, resolution_root))
        return RunSummary(
            framework="demo v1",
            controls_total=3,
            approved=1,
            pending_review=2,
            lint_errors=["missing parameter"],
            published_to="out/demo-1",
        )

    def review(self, *, config_path, resolution_root, event_sink=None):
        self.review_calls.append((config_path, resolution_root))
        return ReviewSummary(
            pending=[
                PendingItem(
                    control_id="C-1",
                    confidence=0.8,
                    policies=[{"id": "p1", "name": "Policy 1"}],
                    rationale="review me",
                )
            ],
            preview_excluded=[{"display_name": "[Preview] Example"}],
            oos_reconsidered=[{"policy_id": "p2", "reconsideration_reason": "now GA"}],
        )


def test_cmd_run_uses_shared_service_and_preserves_exit_behavior(monkeypatch, capsys, tmp_path):
    fake = _FakeService()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_application_service", lambda: fake)

    exit_code = cli.cmd_run(argparse.Namespace(config="config/sample.json", no_distribute=False))
    captured = capsys.readouterr()
    out = captured.out

    assert exit_code == 1
    assert "framework      : demo v1" in out
    assert "lint errors:" in out
    assert fake.run_calls


def test_cmd_review_uses_shared_service(monkeypatch, capsys, tmp_path):
    fake = _FakeService()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_application_service", lambda: fake)

    exit_code = cli.cmd_review(argparse.Namespace(config="config/sample.json"))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "OOS RECONSIDERED (1)" in out
    assert "PREVIEW-EXCLUDED (1)" in out
    assert "PENDING REVIEW (1)" in out
    assert fake.review_calls


class _FakeStreamingService:
    def run(self, *, event_sink, **_kwargs):
        event_sink(
            PipelineEvent(
                type=EventType.STAGE_STARTED,
                run_id="a" * 32,
                sequence=0,
                timestamp="2026-01-01T00:00:00Z",
                stage=Stage.INGEST,
                message="Ingest stage",
            )
        )
        event_sink(
            PipelineEvent(
                type=EventType.WARNING,
                run_id="a" * 32,
                sequence=1,
                timestamp="2026-01-01T00:00:01Z",
                summary={"kind": "oos-staleness", "count": 1},
            )
        )
        return RunSummary(
            framework="demo v1",
            controls_total=1,
            approved=1,
            pending_review=0,
            lint_errors=[],
            published_to=None,
        )

    def review(self, *, event_sink, **_kwargs):
        event_sink(
            PipelineEvent(
                type=EventType.STAGE_COMPLETED,
                run_id="b" * 32,
                sequence=0,
                timestamp="2026-01-01T00:00:00Z",
                stage=Stage.CATALOGUE,
                message="Catalogue done",
            )
        )
        return ReviewSummary(pending=[], preview_excluded=[], oos_reconsidered=[])


def test_cmd_run_and_review_render_event_stream_to_stderr(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_application_service", lambda: _FakeStreamingService())

    assert cli.cmd_run(argparse.Namespace(config="config/sample.json", no_distribute=False)) == 0
    assert cli.cmd_review(argparse.Namespace(config="config/sample.json")) == 0

    captured = capsys.readouterr()
    assert "▶  Ingest stage" in captured.err
    assert "OOS entries need review" in captured.err
    assert "✓  Catalogue done" in captured.err

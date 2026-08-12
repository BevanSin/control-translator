from __future__ import annotations

import argparse

from control_translator.application import PendingItem, ReviewSummary, RunSummary
from control_translator import cli


class _FakeService:
    def __init__(self):
        self.run_calls = []
        self.review_calls = []

    def run(self, *, config_path, do_distribute, resolution_root):
        self.run_calls.append((config_path, do_distribute, resolution_root))
        return RunSummary(
            framework="demo v1",
            controls_total=3,
            approved=1,
            pending_review=2,
            lint_errors=["missing parameter"],
            published_to="out/demo-1",
        )

    def review(self, *, config_path, resolution_root):
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
    out = capsys.readouterr().out

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

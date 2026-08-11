"""Tests for the pull-request policy workflow helper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="pull-request policy tests require Bash",
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_SCRIPT = ROOT / ".github" / "scripts" / "enforce-pr-policy.sh"


def run_policy(body: str, labels: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "BASE_SHA": "HEAD",
        "HEAD_SHA": "HEAD",
        "LABELS": labels,
        "PR_BODY": body,
    }
    return subprocess.run(
        ["bash", str(POLICY_SCRIPT)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("evidence", ["", "N/A", "- TBD."])
def test_security_sensitive_pr_requires_substantive_evidence(evidence: str) -> None:
    result = run_policy(
        f"""## Security review

- [x] Security review completed

Security evidence or reviewer notes:
{evidence}
## Documentation
""",
        "security-sensitive",
    )

    assert result.returncode == 1
    assert "substantive content under" in result.stdout


def test_security_sensitive_pr_accepts_multiline_evidence() -> None:
    result = run_policy(
        """## Security review

- [x] Security review completed

Security evidence or reviewer notes:
- Reviewed the additional pull-request write permission.
- It is required only for dependency-review failure summaries.
## Documentation
""",
        "security-sensitive",
    )

    assert result.returncode == 0


def test_security_sensitive_pr_requires_review_checkbox() -> None:
    result = run_policy(
        """## Security review

- [ ] Security review completed

Security evidence or reviewer notes:
- Permission scope was reviewed.
## Documentation
""",
        "security-sensitive",
    )

    assert result.returncode == 1
    assert "complete the security review checkbox" in result.stdout


def test_non_security_sensitive_pr_is_unaffected() -> None:
    result = run_policy(
        """## Security review

- [ ] Security review completed

Security evidence or reviewer notes:
## Documentation
""",
        "",
    )

    assert result.returncode == 0

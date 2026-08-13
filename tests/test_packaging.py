from __future__ import annotations

import pytest

from build_support import resolve_npm_executable


def test_npm_executable_uses_the_platform_resolved_command():
    assert resolve_npm_executable(lambda command: f"C:/node/{command}.cmd") == "C:/node/npm.cmd"


def test_missing_npm_has_a_deterministic_build_error():
    with pytest.raises(RuntimeError, match="Node.js / npm is required"):
        resolve_npm_executable(lambda _command: None)

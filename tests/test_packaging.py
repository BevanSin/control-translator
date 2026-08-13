from __future__ import annotations

from pathlib import Path
import os
import runpy
from unittest.mock import Mock, patch

import pytest

from build_support import resolve_npm_executable


def test_npm_executable_uses_the_platform_resolved_command():
    assert resolve_npm_executable(lambda command: f"C:/node/{command}.cmd") == "C:/node/npm.cmd"


def test_missing_npm_has_a_deterministic_build_error():
    with pytest.raises(RuntimeError, match="Node.js / npm is required"):
        resolve_npm_executable(lambda _command: None)


def test_package_inspection_accepts_wheel_and_sdist_asset_paths():
    verifier = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / ".github" / "scripts" / "verify_web_package.py")
    )
    assert_assets = verifier["_assert_assets"]

    assert_assets(
        [
            "control_translator/web_assets/index.html",
            "control_translator/web_assets/assets/index-abc123.js",
        ],
        Path("control_translator.whl"),
    )
    assert_assets(
        [
            "control_translator-0.1.0/src/control_translator/web_assets/index.html",
            "control_translator-0.1.0/src/control_translator/web_assets/assets/index-abc123.js",
        ],
        Path("control_translator.tar.gz"),
    )


def test_windows_control_break_exit_is_accepted_as_clean_shutdown():
    verifier = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / ".github" / "scripts" / "verify_web_package.py")
    )
    process = Mock()
    process.wait.return_value = 3

    with patch.object(os, "name", "nt"):
        verifier["_stop_launcher"](process)

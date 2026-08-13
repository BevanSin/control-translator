"""Build-time helpers shared by setuptools commands and packaging tests."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


def resolve_npm_executable(which=shutil.which) -> str:
    """Resolve npm's platform-specific executable, including npm.cmd on Windows."""
    executable = which("npm")
    if executable is None:
        raise RuntimeError("Node.js / npm is required to build the dashboard assets.")
    return executable


def build_dashboard(root: Path, output: Path) -> None:
    frontend = root / "frontend"
    output.mkdir(parents=True, exist_ok=True)
    npm = resolve_npm_executable()
    subprocess.run([npm, "ci"], cwd=frontend, check=True)
    subprocess.run(
        [npm, "run", "build", "--", "--outDir", str(output.resolve())],
        cwd=frontend,
        check=True,
    )

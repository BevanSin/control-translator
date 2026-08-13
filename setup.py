from __future__ import annotations

from pathlib import Path
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class build_py(_build_py):
    """Build the offline dashboard into the wheel after Python modules are copied."""

    def run(self) -> None:
        super().run()
        root = Path(__file__).parent
        frontend = root / "frontend"
        output = (Path(self.build_lib) / "control_translator" / "web_assets").resolve()
        output.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["npm", "ci"], cwd=frontend, check=True)
            subprocess.run(
                ["npm", "run", "build", "--", "--outDir", str(output)],
                cwd=frontend,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Node.js / npm is required to build the dashboard assets.") from exc


setup(cmdclass={"build_py": build_py})

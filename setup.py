from __future__ import annotations

from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_support import build_dashboard
from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist


class build_py(_build_py):
    """Build the offline dashboard into the wheel after Python modules are copied."""

    def run(self) -> None:
        super().run()
        root = Path(__file__).parent
        output = Path(self.build_lib) / "control_translator" / "web_assets"
        build_dashboard(root, output)


class sdist(_sdist):
    """Build dashboard assets inside the temporary source release tree."""

    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        super().make_release_tree(base_dir, files)
        root = Path(base_dir)
        try:
            build_dashboard(root, root / "src" / "control_translator" / "web_assets")
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(root / "frontend" / "node_modules", ignore_errors=True)


setup(cmdclass={"build_py": build_py, "sdist": sdist})

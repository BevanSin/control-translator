from __future__ import annotations

import os
import subprocess
import sys


def test_cli_imports_without_httpx_or_openpyxl():
    script = """
import builtins
import importlib

blocked = {"httpx", "openpyxl"}
original_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root in blocked:
        raise ModuleNotFoundError(name=root)
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
module = importlib.import_module("control_translator.cli")
assert callable(module.main)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath("src")
    completed = subprocess.run([sys.executable, "-c", script], env=env, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr

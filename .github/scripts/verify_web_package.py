"""Inspect built distributions and smoke-test the installed local dashboard."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.request import Request, urlopen
import venv
import zipfile


TOKEN_PREFIX = "Local session token: "
URL_PREFIX = "Local dashboard: "
API_PREFIX = "api/v1"


def _assert_assets(names: list[str], artifact: Path) -> None:
    normalized = [name.replace("\\", "/") for name in names]
    if not any(name.endswith("control_translator/web_assets/index.html") for name in normalized):
        raise RuntimeError(f"{artifact.name} does not contain the dashboard index")
    if not any(
        re.search(r"(?:^|/)control_translator/web_assets/assets/.*\.js$", name)
        for name in normalized
    ):
        raise RuntimeError(f"{artifact.name} does not contain a dashboard JavaScript bundle")


def _inspect_distributions(dist_dir: Path) -> tuple[Path, Path]:
    wheels = list(dist_dir.glob("*.whl"))
    sdists = list(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("Expected exactly one wheel and one source distribution")
    with zipfile.ZipFile(wheels[0]) as archive:
        _assert_assets(archive.namelist(), wheels[0])
    with tarfile.open(sdists[0], "r:gz") as archive:
        _assert_assets(archive.getnames(), sdists[0])
    return wheels[0].resolve(), sdists[0].resolve()


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _launcher(root: Path) -> Path:
    return root / ("Scripts/ct-web.exe" if os.name == "nt" else "bin/ct-web")


def _install(artifact: Path, environment: Path) -> None:
    venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run(
        [str(_venv_python(environment)), "-m", "pip", "install", f"{artifact}[web]"],
        check=True,
    )


def _start_launcher(executable: Path, data_root: Path) -> tuple[subprocess.Popen[str], str, str]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(executable), "--no-browser", "--print-token", "--data-root", str(data_root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    assert process.stderr is not None
    lines: queue.Queue[str] = queue.Queue()

    def _read_stderr() -> None:
        for line in process.stderr:
            lines.put(line.rstrip())

    threading.Thread(target=_read_stderr, daemon=True).start()
    token = ""
    url = ""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (not token or not url):
        if process.poll() is not None:
            raise RuntimeError(f"Installed launcher exited during startup with code {process.returncode}")
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if line.startswith(TOKEN_PREFIX):
            token = line.removeprefix(TOKEN_PREFIX)
        elif line.startswith(URL_PREFIX):
            url = line.removeprefix(URL_PREFIX)
    if not token or not url:
        process.kill()
        raise RuntimeError("Installed launcher did not report its manual connection details")
    return process, token, url


def _api_json(url: str, token: str, *, method: str = "GET", body: dict | None = None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", "X-CT-Session-Token": token},
    )
    deadline = time.monotonic() + 20
    while True:
        try:
            with urlopen(request, timeout=2) as response:
                return json.load(response)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _stop_launcher(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)
    try:
        return_code = process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        raise RuntimeError("Installed launcher did not shut down after an interrupt") from None
    if not _is_clean_shutdown(return_code):
        raise RuntimeError(f"Installed launcher exited with code {return_code}")


def _is_clean_shutdown(return_code: int) -> bool:
    expected_codes = {0, 3} if os.name == "nt" else {0}
    return return_code in expected_codes


def _smoke_launcher(environment: Path, data_root: Path) -> None:
    executable = _launcher(environment)
    process, token, url = _start_launcher(executable, data_root)
    try:
        projects = _api_json(f"{url}{API_PREFIX}/projects", token)
        if projects != {"count": 0, "projects": []}:
            raise RuntimeError("Fresh installed launcher returned unexpected project state")
        created = _api_json(
            f"{url}{API_PREFIX}/projects",
            token,
            method="POST",
            body={"name": "Package smoke project"},
        )
        if created.get("name") != "Package smoke project":
            raise RuntimeError("Installed launcher could not create a project")

        second = subprocess.run(
            [str(executable), "--no-browser", "--data-root", str(data_root)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if second.returncode != 1 or "Another local dashboard" not in second.stderr:
            raise RuntimeError("Installed launcher did not reject a second data-root owner")
        if str(data_root) in second.stderr:
            raise RuntimeError("Second-instance diagnostics exposed the selected data-root path")
    finally:
        _stop_launcher(process)

    restarted, restarted_token, restarted_url = _start_launcher(executable, data_root)
    try:
        projects = _api_json(f"{restarted_url}{API_PREFIX}/projects", restarted_token)
        if projects.get("count") != 1:
            raise RuntimeError("Project state did not survive an installed-launcher restart")
    finally:
        _stop_launcher(restarted)


def main() -> None:
    dist_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    wheel, sdist = _inspect_distributions(dist_dir)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        wheel_environment = root / "wheel-venv"
        sdist_environment = root / "sdist-venv"
        _install(wheel, wheel_environment)
        _install(sdist, sdist_environment)
        subprocess.run(
            [
                str(_venv_python(sdist_environment)),
                "-c",
                "from control_translator.web import packaged_assets; assert packaged_assets().joinpath('index.html').is_file()",
            ],
            check=True,
        )
        _smoke_launcher(wheel_environment, root / "project-data")


if __name__ == "__main__":
    main()

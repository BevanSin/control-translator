"""A crash-safe, cross-process exclusive lock guarding one project's mutable state.

The lock file records the holder's PID, run id, and a random ownership token. A
fresh process (for example after a restart following a crash) can safely reclaim
the lock once it verifies the recorded PID is no longer alive — it never assumes
a stale lock is safe to remove just because the file is old. The ownership token
lets a holder (or a reclaiming process) delete only the exact lock file content
it has just re-verified, so a lock replaced by another process between read and
delete is never removed out from under its new owner.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from ..projects import DATA_ROOT_INSTANCE_LOCK_NAME, ProjectStore
from .errors import ProjectRunConflictError

_LOCK_RELATIVE_PATH = "runs/.mutation-lock.json"

# The Win32 STILL_ACTIVE sentinel returned by GetExitCodeProcess for a process
# that has not yet exited. Defined unconditionally so the liveness logic below
# can be exercised deterministically on any platform.
_STILL_ACTIVE = 259


def _win32_pid_alive(open_process, get_exit_code_process, close_handle, pid: int) -> bool:
    """Portable, dependency-injected core of the Windows liveness check.

    ``os.kill(pid, 0)`` is not a reliable way to confirm a process has exited on
    Windows — Python's implementation cannot reliably distinguish a live process
    from a PID a parent process is still holding a handle to after the child has
    exited. Querying the exit code via the Win32 API (comparing against the
    ``STILL_ACTIVE`` sentinel) is the documented, reliable way to tell. The
    Win32 calls are injected as parameters so this logic can be tested
    deterministically without requiring an actual Windows host.
    """
    handle = open_process(pid)
    if not handle:
        return False  # no such process (or, rarely, no permission to query it)
    try:
        exit_code = get_exit_code_process(handle)
        if exit_code is None:
            return False
        return exit_code == _STILL_ACTIVE
    finally:
        close_handle(handle)


if sys.platform == "win32":  # pragma: no cover - exercised via _win32_pid_alive tests
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _open_process(pid: int):
        return ctypes.windll.kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)

    def _get_exit_code_process(handle) -> int | None:
        exit_code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        return exit_code.value

    def _close_handle(handle) -> None:
        ctypes.windll.kernel32.CloseHandle(handle)

    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        return _win32_pid_alive(_open_process, _get_exit_code_process, _close_handle, pid)

else:
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists, owned by someone else
        except OSError:
            return False
        return True


class _MutationLock:
    """Ownership-safe lock backed by one canonical lock file."""

    def __init__(self, lock_path: Path, conflict_label: str):
        self._path = lock_path
        self._conflict_label = conflict_label
        self._token: str | None = None

    def _lock_path(self) -> Path:
        return self._path

    def acquire(self, run_id: str) -> None:
        path = self._lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        payload = json.dumps(
            {"pid": os.getpid(), "run_id": run_id, "lock_token": token}).encode("utf-8")
        for _attempt in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                self._reclaim_if_stale(path)
                continue
            else:
                try:
                    os.write(fd, payload)
                finally:
                    os.close(fd)
                self._token = token
                return
        raise ProjectRunConflictError(
            f"{self._conflict_label} already has a mutation in progress.")

    def _read(self, path: Path) -> dict | None:
        try:
            with path.open(encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _reclaim_if_stale(self, path: Path) -> None:
        holder = self._read(path)
        if holder is None:
            # Corrupt or already-removed lock file: nothing verifiable to remove,
            # and nothing safe to remove without risking another holder's lock.
            return
        pid = holder.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and _pid_alive(pid):
            raise ProjectRunConflictError(
                f"{self._conflict_label} already has mutation {holder.get('run_id', '?')} "
                "in progress.")
        # The recorded holder process is gone — the lock is stale from a crash.
        # Re-read immediately before deleting and only remove the file if its
        # content still matches what we just judged stale: another process may
        # have reclaimed and replaced it between our read and this delete.
        self._remove_if_matches(path, holder.get("lock_token"))

    def _remove_if_matches(self, path: Path, expected_token: object) -> None:
        current = self._read(path)
        if current is None:
            return  # already gone; nothing to do
        if current.get("lock_token") != expected_token:
            return  # replaced by someone else since we last read it; leave it alone
        path.unlink(missing_ok=True)

    def release(self) -> None:
        if self._token is None:
            return
        self._remove_if_matches(self._lock_path(), self._token)
        self._token = None


class ProjectMutationLock(_MutationLock):
    """Acquire/release the single mutation lock for one project's workspace."""

    def __init__(self, project_store: ProjectStore, project_id: str):
        self.project_id = project_id
        super().__init__(
            project_store.resolve_path(project_id, _LOCK_RELATIVE_PATH),
            f"Project {project_id}",
        )


class ResourceMutationLock(_MutationLock):
    """Serialize mutations to a shared file across projects and processes."""

    def __init__(self, resource_path: str | Path):
        canonical = Path(resource_path).resolve()
        super().__init__(
            canonical.with_name(f".{canonical.name}.mutation-lock.json"),
            f"Resource {canonical.name}",
        )

    def acquire(self, run_id: str, *, wait: bool = False, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                super().acquire(run_id)
                return
            except ProjectRunConflictError:
                if not wait or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)


class DataRootInstanceLock(_MutationLock):
    """Allow only one local dashboard process to own a canonical data root."""

    def __init__(self, data_root: str | Path):
        canonical = Path(data_root).resolve()
        super().__init__(
            canonical / DATA_ROOT_INSTANCE_LOCK_NAME,
            "The selected data root",
        )

"""A crash-safe, cross-process exclusive lock guarding one project's mutable state.

The lock file records the holder's PID and run id. A fresh process (for example
after a restart following a crash) can safely reclaim the lock once it verifies
the recorded PID is no longer alive — it never assumes a stale lock is safe to
remove just because the file is old.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..projects import ProjectStore
from .errors import ProjectRunConflictError

_LOCK_RELATIVE_PATH = "runs/.mutation-lock.json"


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


class ProjectMutationLock:
    """Acquire/release the single mutation lock for one project's workspace."""

    def __init__(self, project_store: ProjectStore, project_id: str):
        self._project_store = project_store
        self.project_id = project_id

    def _lock_path(self) -> Path:
        return self._project_store.resolve_path(self.project_id, _LOCK_RELATIVE_PATH)

    def acquire(self, run_id: str) -> None:
        path = self._lock_path()
        payload = json.dumps({"pid": os.getpid(), "run_id": run_id}).encode("utf-8")
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
                return
        raise ProjectRunConflictError(
            f"Project {self.project_id} already has a run in progress.")

    def _reclaim_if_stale(self, path: Path) -> None:
        try:
            with path.open(encoding="utf-8") as file:
                holder = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            # Corrupt or already-removed lock file: safe to drop and retry.
            path.unlink(missing_ok=True)
            return
        pid = holder.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and _pid_alive(pid):
            raise ProjectRunConflictError(
                f"Project {self.project_id} already has run {holder.get('run_id', '?')} "
                "in progress.")
        # The recorded holder process is gone — the lock is stale from a crash.
        path.unlink(missing_ok=True)

    def release(self) -> None:
        self._lock_path().unlink(missing_ok=True)

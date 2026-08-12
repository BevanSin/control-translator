"""Ownership-safe, cross-platform behaviour of the project mutation lock."""
from __future__ import annotations

import json
import os

from control_translator.projects import ProjectStore
from control_translator.runs.lock import ProjectMutationLock, _win32_pid_alive


def _lock_path(project_store: ProjectStore, project_id: str):
    return project_store.resolve_path(project_id, "runs/.mutation-lock.json")


def test_win32_pid_alive_reports_still_active_process_as_alive():
    """A live process's exit code is STILL_ACTIVE (259) — the reliable Win32 signal."""
    STILL_ACTIVE = 259

    def _open_process(_pid):
        return 1234  # any truthy handle

    def _get_exit_code_process(_handle):
        return STILL_ACTIVE

    closed = []
    assert _win32_pid_alive(_open_process, _get_exit_code_process, closed.append, 4242) is True
    assert closed == [1234]


def test_win32_pid_alive_reports_exited_process_as_dead_even_if_pid_reused_by_os_kill():
    """This is the regression: an exited child's handle still opens, but its exit
    code is no longer STILL_ACTIVE — unlike a naive ``os.kill(pid, 0)`` check,
    which cannot make this distinction on Windows and would misreport the exited
    process as alive."""
    EXITED_CODE = 0

    def _open_process(_pid):
        return 5678

    def _get_exit_code_process(_handle):
        return EXITED_CODE

    closed = []
    assert _win32_pid_alive(_open_process, _get_exit_code_process, closed.append, 4242) is False
    assert closed == [5678]


def test_win32_pid_alive_reports_no_such_process_as_dead():
    def _open_process(_pid):
        return 0  # OpenProcess returns NULL when the process does not exist

    def _get_exit_code_process(_handle):
        raise AssertionError("must not query exit code without a valid handle")

    assert _win32_pid_alive(_open_process, _get_exit_code_process, lambda _h: None, 9999) is False


def test_stale_lock_is_not_removed_if_replaced_before_reclaim_completes(tmp_path):
    """Simulates the replacement race: process A judges a lock stale, but before it
    deletes the file, process B has already reclaimed and overwritten it with its
    own (live) ownership token. A must not remove B's lock."""
    project_store = ProjectStore(tmp_path / "data-root")
    project = project_store.create("Example")
    lock_path = _lock_path(project_store, project.id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    stale_holder = {"pid": 999999999, "run_id": "0" * 32, "lock_token": "stale-token"}
    lock_path.write_text(json.dumps(stale_holder), encoding="utf-8")

    lock = ProjectMutationLock(project_store, project.id)

    # Monkeypatch the read used inside _reclaim_if_stale's second (pre-delete) read
    # to simulate another process having replaced the file in between.
    original_read = lock._read
    calls = {"count": 0}

    def _read_then_replace(path):
        calls["count"] += 1
        result = original_read(path)
        if calls["count"] == 1:
            # Simulate process B reclaiming and replacing the lock right after A's
            # first read but before A's compare-before-delete re-read.
            replacement = {"pid": os.getpid(), "run_id": "1" * 32, "lock_token": "b-token"}
            lock_path.write_text(json.dumps(replacement), encoding="utf-8")
        return result

    lock._read = _read_then_replace
    lock._reclaim_if_stale(lock_path)

    # B's lock must survive untouched.
    surviving = json.loads(lock_path.read_text(encoding="utf-8"))
    assert surviving["lock_token"] == "b-token"


def test_release_only_removes_the_lock_it_owns(tmp_path):
    """A holder must never delete a lock file that has since been replaced by
    another process (for example after this holder's own lock was judged stale
    and reclaimed elsewhere due to an unrelated bug, or a race)."""
    project_store = ProjectStore(tmp_path / "data-root")
    project = project_store.create("Example")

    lock = ProjectMutationLock(project_store, project.id)
    lock.acquire("a" * 32)

    # Simulate another process replacing the lock file after we acquired it.
    lock_path = _lock_path(project_store, project.id)
    other = {"pid": os.getpid(), "run_id": "b" * 32, "lock_token": "someone-elses-token"}
    lock_path.write_text(json.dumps(other), encoding="utf-8")

    lock.release()

    # The replacement lock must survive: release() must not blindly unlink.
    assert lock_path.exists()
    surviving = json.loads(lock_path.read_text(encoding="utf-8"))
    assert surviving["lock_token"] == "someone-elses-token"

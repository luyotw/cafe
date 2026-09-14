"""Process-safe lease for workspace validation and controlled consumption."""

from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()


def _thread_lock(repo: Path) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(repo, threading.RLock())


@contextmanager
def workspace_execution_lock(repo: Path) -> Iterator[None]:
    """Serialize workspace writers and consumers for one active repository.

    The thread lock covers concurrent CAFE work in one process. The adjacent
    file lock covers a second process without making the lock file an artifact
    or authority source; it is only a coordination lease.
    """
    root = Path(repo).resolve()
    lock_dir = root / ".cafe"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "workspace-use.lock"
    with _thread_lock(root):
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

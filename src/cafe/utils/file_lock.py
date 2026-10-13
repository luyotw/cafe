"""Descriptor-based opening for private process lock files."""

import errno
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import IO, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - available only on Windows.
    msvcrt = None  # type: ignore[assignment]


def open_lock_file(path: Path) -> TextIO:
    """Anchor each component without following links, then validate the opened inode."""
    path = Path(path).absolute()
    directory_fd = None
    file_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_fd = os.open(path.anchor, flags)
        for component in path.parent.parts[1:]:
            child_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        file_fd = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("lock must be a regular file with one directory entry")
        handle = os.fdopen(file_fd, "r+", encoding="utf-8")
        file_fd = None  # Ownership transferred to the caller's context manager.
        return handle
    except OSError as error:
        raise ValueError("cannot safely open lock file") from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _try_advancement_process_lock(path: Path) -> IO[str] | None:
    """Acquire the callable-duration lock without waiting for another worker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        if msvcrt is None:
            raise RuntimeError("cross-process file locking is unavailable")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return None
        raise
    except BaseException:
        handle.close()
        raise


def _release_advancement_process_lock(handle: IO[str]) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no branch - platform-specific.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


class WorkerAlreadyRunningError(RuntimeError):
    """Another live worker owns workflow advancement."""


_advancement_scopes = threading.local()


@contextmanager
def advancement_execution_lock(issue_dir: Path, *, reuse_existing: bool = True):
    """Reuse only this thread's actual live process-lock handle for this issue."""
    path = Path(issue_dir).resolve() / ".workflow-advancement.lock"
    scopes = getattr(_advancement_scopes, 'handles', {})
    existing = scopes.get(path)
    if reuse_existing and existing is not None and existing[0] == os.getpid() and not existing[1].closed:
        yield False
        return
    handle = _try_advancement_process_lock(path)
    if handle is None:
        raise WorkerAlreadyRunningError("workflow advancement is owned by another worker")
    _advancement_scopes.handles = scopes
    scopes[path] = (os.getpid(), handle)
    try:
        yield True
    finally:
        scopes.pop(path, None)
        _release_advancement_process_lock(handle)

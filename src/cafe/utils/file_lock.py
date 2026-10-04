"""Descriptor-based opening for private process lock files."""

import os
import stat
from pathlib import Path
from typing import TextIO


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
            path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600, dir_fd=directory_fd,
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

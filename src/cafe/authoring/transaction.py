"""Confined publication, source checks, rollback and bounded interrupted recovery."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from cafe.utils.file_lock import open_lock_file


def storage(root):
    key = hashlib.sha256(str(root).encode()).hexdigest()
    return Path(tempfile.gettempdir()) / f"cafe-authoring-{os.getuid()}" / key


def confined(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or str(path) != relative:
        raise ValueError("Target must be an exact confined repository-relative path")
    parts = path.parts
    project = parts[:2] in ((".cafe", "skills"), (".cafe", "playbooks"))
    builtin = parts[:4] in (("src", "cafe", "data", "skills"), ("src", "cafe", "data", "playbooks"))
    if not project and not builtin:
        raise ValueError("Only project or verified CAFE builtin source catalogs are writable")
    if builtin:
        metadata = root / "pyproject.toml"
        if (
            not metadata.is_file()
            or 'name = "cafe-engine"' not in metadata.read_text()
            or not (root / "src/cafe/core/playbook.py").is_file()
        ):
            raise ValueError("Builtin authoring requires the selected CAFE source repository")
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                "pyproject.toml",
                "src/cafe/core/playbook.py",
            ],
            capture_output=True,
        )
        if tracked.returncode != 0:
            raise ValueError("Builtin targets require versioned CAFE source markers")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Target path traverses a symlink")
    if current.exists() and (not current.is_file() or current.stat().st_nlink != 1):
        raise ValueError("Target must be a regular source file with one directory entry")
    return current


def _parent(root, relative):
    """Open every component without links and anchor writes to directory descriptors."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in Path(relative).parent.parts:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def publish(root, relative, content, mode):
    confined(root, relative)
    fd = _parent(root, relative)
    name = Path(relative).name
    temporary = f".authoring-{secrets.token_hex(16)}"
    created = False
    try:
        if content is None:
            os.unlink(name, dir_fd=fd)
        else:
            file_fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd
            )
            created = True
            with os.fdopen(file_fd, "wb") as file:
                file.write(content)
                file.flush()
                os.fchmod(file.fileno(), mode)
                os.fsync(file.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            if created:
                os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.close(fd)


def _decode(value):
    return base64.b64decode(value) if value is not None else None


def recover(root, journal):
    record = json.loads(journal.read_text())
    if record.get("root") != str(root) or record.get("version") != 1:
        raise ValueError("Ambiguous recovery journal")
    items = record["files"]
    if len(items) > 64:
        raise ValueError("Recovery exceeds transaction bound")
    # Check the entire set before restoring anything; preserve concurrent modifications.
    for relative, item in items.items():
        path = confined(root, relative)
        current = path.read_bytes() if path.exists() else None
        if current not in (_decode(item["old"]), _decode(item["new"])):
            raise ValueError(f"Concurrent edit prevents recovery: {relative}")
    for relative, item in reversed(list(items.items())):
        path = confined(root, relative)
        old = _decode(item["old"])
        if (path.read_bytes() if path.exists() else None) != old:
            publish(root, relative, old, item["mode"])
    for relative in reversed(record.get("created_directories", [])):
        path = root / relative
        if path.is_symlink():
            raise ValueError("Recovery directory was replaced by a symlink")
        try:
            path.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            if any(path.iterdir()):
                continue
            raise
    journal.unlink()


def _journal(path, record):
    temporary = path.with_suffix(".new")
    with temporary.open("w") as file:
        json.dump(record, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def commit(root, result, final_check):
    private = storage(root)
    journal = private / "pending.json"
    record = {"version": 1, "root": str(root), "files": {}, "created_directories": []}
    for relative in result.files:
        if not any(change["target"] == relative for change in result.changes):
            continue
        path = confined(root, relative)
        old = path.read_bytes() if path.exists() else None
        record["files"][relative] = {
            "old": base64.b64encode(old).decode() if old is not None else None,
            "new": base64.b64encode(result.files[relative].encode()).decode(),
            "mode": stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644,
        }
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(str(parent.relative_to(root)))
            parent = parent.parent
        for directory in reversed(missing):
            if directory not in record["created_directories"]:
                record["created_directories"].append(directory)
    _journal(journal, record)
    try:
        for directory in record["created_directories"]:
            path = root / directory
            # Publication guard checks every ancestor before creating directories.
            confined(
                root,
                next(
                    relative for relative in record["files"] if relative.startswith(directory + "/")
                ),
            )
            path.mkdir(exist_ok=True)
        for relative, item in record["files"].items():
            publish(root, relative, _decode(item["new"]), item["mode"])
        final_check()
        journal.unlink()
    except Exception:
        recover(root, journal)
        raise


def locked(root):
    private = storage(root)
    private.parent.mkdir(mode=0o700, exist_ok=True)
    private.mkdir(mode=0o700, exist_ok=True)
    for path in (private.parent, private):
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError("Unsafe authoring recovery storage")
    handle = open_lock_file(private / "write.lock")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle

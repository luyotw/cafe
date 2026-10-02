"""Reuse existing iteration telemetry without resolving caller authority."""

import ctypes
import errno
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

from cafe.core.types import TokenUsage
from cafe.core.workspace_lock import workspace_execution_lock


def merge_token_usage_stats(existing: Any, incoming: TokenUsage) -> Dict[str, Any]:
    """Merge one raw attempt into the existing iteration stats shape."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    incoming_data = incoming.model_dump()
    additive_fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "cache_read_input_tokens",
        "reasoning_output_tokens",
        "total_cost_usd",
    )
    for field in additive_fields:
        prior = merged.get(field, 0)
        value = incoming_data.get(field, 0)
        merged[field] = (prior if isinstance(prior, (int, float)) else 0) + (
            value if isinstance(value, (int, float)) else 0
        )

    for field in ("duration_ms", "duration_api_ms"):
        prior = merged.get(field)
        value = incoming_data.get(field)
        if isinstance(value, int):
            merged[field] = (prior if isinstance(prior, int) else 0) + value
        elif field not in merged:
            merged[field] = None

    prior_turns = merged.get("turn_usages")
    incoming_turns = incoming_data.get("turn_usages")
    merged["turn_usages"] = (list(prior_turns) if isinstance(prior_turns, list) else []) + (
        list(incoming_turns) if isinstance(incoming_turns, list) else []
    )
    return merged


def _inode(info):
    return info.st_dev, info.st_ino


@contextmanager
def _usage_parent(target: Path):
    """Reuse no-follow descriptor traversal across the complete absolute path."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.sep, flags)
    identities = [_inode(os.fstat(descriptor))]
    try:
        for part in target.parent.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            identities.append(_inode(os.fstat(descriptor)))
        yield descriptor, tuple(identities)
    finally:
        os.close(descriptor)


def _read_usage_file(parent_fd, name, *, retain_descriptor=False):
    mode = os.O_RDWR if retain_descriptor else os.O_RDONLY
    descriptor = os.open(name, mode | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or (retain_descriptor and info.st_nlink != 1):
            raise ValueError("usage target must be an unaliased regular file")
        data = json.load(handle)
        if retain_descriptor:
            return data, _inode(info), os.dup(handle.fileno())
        return data, _inode(info)


def _exchange_usage_file(parent_fd, source, destination):
    """Atomically publish while retaining the displaced inode for validation.

    Ordinary replace cannot conditionally protect a destination substituted at
    the syscall boundary. Exchange keeps that object intact for verification and
    rollback, including symlinks, without following it or publishing partial JSON.
    Unsupported platforms/filesystems fail before modifying either file.
    """
    library = ctypes.CDLL(None, use_errno=True)
    if hasattr(library, "renameat2"):
        operation, flag = library.renameat2, 2  # Linux RENAME_EXCHANGE
    elif hasattr(library, "renameatx_np"):
        operation, flag = library.renameatx_np, 2  # Darwin RENAME_SWAP
    else:
        raise OSError(errno.ENOTSUP, "atomic usage exchange is unavailable")
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_uint]
    operation.restype = ctypes.c_int
    if operation(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), flag):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def iteration_usage_sink(repository_root: Path, context_file: Path):
    """Pin an existing caller-admitted metadata target; never create an iteration."""
    root = Path(repository_root).resolve()
    target = Path(os.path.abspath(context_file))
    if target.resolve() != target or not target.is_relative_to(root):
        raise ValueError("usage target must remain within its admitted workspace")
    try:
        with _usage_parent(target) as (parent_fd, parents):
            original, _ = _read_usage_file(parent_fd, target.name)
    except FileNotFoundError:
        return None
    if not isinstance(original, dict) or not isinstance(original.get("iteration"), int):
        return None
    identity = (original.get("iteration"), original.get("timestamp"))

    def persist(usage: TokenUsage):
        with workspace_execution_lock(root), _usage_parent(target) as (parent_fd, current_parents):
            if current_parents != parents:
                raise ValueError("usage target parent changed")
            # A cooperating writer can atomically replace this same iteration.
            # Read its latest counts under the shared lock; pin this read's inode
            # only through publication, not across independent provider calls.
            current, current_inode, current_fd = _read_usage_file(
                parent_fd, target.name, retain_descriptor=True
            )
            staging_fd = None
            cleanup_fd = None
            temporary = ".usage-" + target.name
            try:
                if (not isinstance(current, dict)
                        or (current.get("iteration"), current.get("timestamp")) != identity):
                    raise ValueError("admitted iteration identity changed")
                current["stats"] = merge_token_usage_stats(current.get("stats"), usage)
                # Reuse one empty publication slot per metadata file. It is never
                # a stats source. Recovery objects are retained, never consumed.
                staging_fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     0o600, dir_fd=parent_fd)
                staged = os.fstat(staging_fd)
                if (not stat.S_ISREG(staged.st_mode) or staged.st_nlink != 1
                        or staged.st_size != 0):
                    raise ValueError("usage publication slot needs recovery")
                cleanup_fd = staging_fd
                published_inode = _inode(staged)
                with os.fdopen(os.dup(staging_fd), "w", encoding="utf-8") as handle:
                    json.dump(current, handle, ensure_ascii=False, indent=2)
                _exchange_usage_file(parent_fd, temporary, target.name)
                cleanup_fd = None
                displaced = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                published = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                if _inode(displaced) != current_inode or _inode(published) != published_inode:
                    _exchange_usage_file(parent_fd, temporary, target.name)
                    restored = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                    if _inode(restored) == published_inode:
                        cleanup_fd = staging_fd
                    raise ValueError("usage target changed during publication")
                cleanup_fd = current_fd
            finally:
                try:
                    if cleanup_fd is not None:
                        # Truncate the authorized open object, not a later name
                        # resolution. A substituted entry is preserved even if
                        # it arrives at the final cleanup syscall boundary.
                        os.ftruncate(cleanup_fd, 0)
                        remaining = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                        if _inode(remaining) != _inode(os.fstat(cleanup_fd)):
                            raise ValueError("usage publication slot changed during cleanup")
                finally:
                    if staging_fd is not None:
                        os.close(staging_fd)
                    os.close(current_fd)

    return persist

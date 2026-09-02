"""Atomic, single-authority persistence for workflow driver policy."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO

import yaml

from cafe.core.driver_policy import (
    POLICY_KEYS,
    REJECTED_POLICY_KEYS,
    DriverPolicyContract,
    extract_driver_policy,
    policy_dict,
)
from cafe.core.packet_io import atomic_write_bytes
from cafe.utils.issue_config import resolve_issue_config_path

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - available only on Windows.
    msvcrt = None  # type: ignore[assignment]


class PrepareWouldClobberError(RuntimeError):
    """Preparing an issue would overwrite durable workflow state."""


def _read_existing_config_strict(path: Path) -> dict[str, Any]:
    """Read policy authority without collapsing corruption into an empty mapping."""
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read existing issue configuration: {path}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"cannot read existing issue configuration: {path}")
    return loaded


@contextmanager
def _exclusive_lock(handle: TextIO) -> Iterator[None]:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is None:
        raise RuntimeError("cross-process file locking is unavailable")
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class IssuePolicyStore:
    """Replace only the policy slice in the active-worktree issue authority."""

    _thread_locks: dict[Path, threading.RLock] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(self, config_path: Path) -> None:
        self.requested_path = Path(config_path)

    @property
    def config_path(self) -> Path:
        return resolve_issue_config_path(
            self.requested_path,
            require_registered_worktree=True,
        )

    def replace(self, proposed: Mapping[str, Any] | DriverPolicyContract) -> dict[str, Any]:
        policy = (
            proposed
            if isinstance(proposed, DriverPolicyContract)
            else DriverPolicyContract.model_validate(dict(proposed))
        )
        with self._locked_authority() as (path, current):
            original = path.read_bytes() if path.exists() else None
            updated = {
                key: value
                for key, value in current.items()
                if key not in POLICY_KEYS and key not in REJECTED_POLICY_KEYS
            }
            updated.update(policy_dict(policy))
            if extract_driver_policy(updated) != policy:
                raise ValueError("replacement did not produce the requested v2 policy")
            content = yaml.safe_dump(
                updated, allow_unicode=True, default_flow_style=False, sort_keys=False
            ).encode("utf-8")
            try:
                serialized = yaml.safe_load(content.decode("utf-8"))
                if not isinstance(serialized, dict):
                    raise ValueError("replacement did not serialize as issue configuration")
                if extract_driver_policy(serialized) != policy:
                    raise ValueError("serialized replacement is not the requested v2 policy")
                atomic_write_bytes(path, content)
                published = _read_existing_config_strict(path)
                if extract_driver_policy(published) != policy:
                    raise ValueError("published replacement is not the requested v2 policy")
            except Exception as exc:
                try:
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(path, original)
                except Exception as rollback_exc:
                    raise ValueError(
                        "policy replacement failed and the prior authority could not be restored"
                    ) from rollback_exc
                raise ValueError(
                    "policy replacement was not durably readable and was rolled back"
                ) from exc
        return published

    @contextmanager
    def locked_policy(self) -> Iterator[DriverPolicyContract]:
        """Hold policy authority stable while a validated policy is in use."""
        with self._locked_authority() as (_, current):
            yield extract_driver_policy(current)

    @contextmanager
    def _locked_authority(self) -> Iterator[tuple[Path, dict[str, Any]]]:
        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / ".issue.yaml.policy.lock"
        with self._thread_lock(path):
            with lock_path.open("a+", encoding="utf-8") as handle:
                with _exclusive_lock(handle):
                    yield path, _read_existing_config_strict(path)

    @staticmethod
    def ensure_prepare_target_available(issue_dir: Path) -> None:
        if (Path(issue_dir) / "blackboard.json").exists():
            raise PrepareWouldClobberError(
                "active workflow state exists; use cafe update-driver-policy "
                "with complete v2 choices"
            )

    @classmethod
    def _thread_lock(cls, path: Path) -> threading.RLock:
        resolved = path.resolve()
        with cls._thread_locks_guard:
            return cls._thread_locks.setdefault(resolved, threading.RLock())


def write_issue_inventory(config_path: Path, *, issue_name: str, worktree_path: Path) -> None:
    """Write the policy-free repository inventory pointer used by ``cafe ls``."""
    value = {"issue_name": issue_name, "worktree_path": str(worktree_path)}
    atomic_write_bytes(
        config_path,
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8"),
    )

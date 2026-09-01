"""Atomic, single-authority persistence for workflow driver policy."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO

import yaml

from cafe.core.driver_policy import (
    POLICY_KEYS,
    DriverPolicyContract,
    extract_driver_policy,
    policy_dict,
)
from cafe.core.packet_io import atomic_write_bytes
from cafe.utils.issue_config import read_issue_config, resolve_issue_config_path

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


class PolicyActivationBlockedError(RuntimeError):
    """The atomic v2 activation preconditions are not satisfied."""


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
        return resolve_issue_config_path(self.requested_path)

    def replace(self, proposed: Mapping[str, Any] | DriverPolicyContract) -> dict[str, Any]:
        policy = (
            proposed
            if isinstance(proposed, DriverPolicyContract)
            else DriverPolicyContract.model_validate(dict(proposed))
        )
        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / ".issue.yaml.policy.lock"
        with self._thread_lock(path):
            with lock_path.open("a+", encoding="utf-8") as handle:
                with _exclusive_lock(handle):
                    current = read_issue_config(path) or {}
                    updated = {
                        key: value
                        for key, value in current.items()
                        if key not in POLICY_KEYS and key != "driver_execution"
                    }
                    updated.update(policy_dict(policy))
                    content = yaml.safe_dump(
                        updated, allow_unicode=True, default_flow_style=False, sort_keys=False
                    ).encode("utf-8")
                    atomic_write_bytes(path, content)
        return updated

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


class GuardedPolicyActivation:
    """Apply and verify one fail-closed policy cutover at a durable boundary."""

    def __init__(self, config_path: Path) -> None:
        self.requested_path = Path(config_path).resolve()
        self.active_path = resolve_issue_config_path(self.requested_path)

    def apply(self, proposed: Mapping[str, Any] | DriverPolicyContract) -> dict[str, Any]:
        policy = (
            proposed
            if isinstance(proposed, DriverPolicyContract)
            else DriverPolicyContract.model_validate(dict(proposed))
        )
        if not self.active_path.exists():
            raise PolicyActivationBlockedError("authoritative issue configuration is missing")
        active_bytes = self.active_path.read_bytes()
        current = read_issue_config(self.active_path) or {}
        non_policy = {
            key: value
            for key, value in current.items()
            if key not in POLICY_KEYS and key != "driver_execution"
        }
        blackboard_path = self.active_path.parent / "blackboard.json"
        blackboard_bytes = blackboard_path.read_bytes() if blackboard_path.exists() else None
        if blackboard_bytes is not None:
            try:
                blackboard = json.loads(blackboard_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PolicyActivationBlockedError("blackboard state is not valid JSON") from exc
            driver_state = (
                blackboard.get("driver_state", {})
                if isinstance(blackboard, dict)
                else {}
            )
            if isinstance(driver_state, dict) and driver_state.get("advancement_lease") is not None:
                raise PolicyActivationBlockedError("an advancement lease is currently held")

        inventory_path = self._inventory_path()
        inventory_bytes = (
            inventory_path.read_bytes()
            if inventory_path is not None and inventory_path.exists()
            else None
        )
        try:
            updated = IssuePolicyStore(self.active_path).replace(policy)
            if inventory_path is not None and inventory_path != self.active_path:
                write_issue_inventory(
                    inventory_path,
                    issue_name=self.active_path.parent.name,
                    worktree_path=self._worktree_root(),
                )
            persisted = read_issue_config(self.active_path) or {}
            extract_driver_policy(persisted)
            persisted_non_policy = {
                key: value for key, value in persisted.items() if key not in POLICY_KEYS
            }
            if persisted_non_policy != non_policy:
                raise PolicyActivationBlockedError("non-policy issue metadata changed")
            if blackboard_bytes is not None and blackboard_path.read_bytes() != blackboard_bytes:
                raise PolicyActivationBlockedError("blackboard state changed during policy update")
            if inventory_path is not None and inventory_path != self.active_path:
                inventory = read_issue_config(inventory_path) or {}
                if set(inventory) != {"issue_name", "worktree_path"}:
                    raise PolicyActivationBlockedError("root inventory contains policy fields")
            return updated
        except Exception:
            atomic_write_bytes(self.active_path, active_bytes)
            if inventory_path is not None and inventory_path != self.active_path:
                if inventory_bytes is None:
                    inventory_path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(inventory_path, inventory_bytes)
            raise

    def _inventory_path(self) -> Path | None:
        if self.requested_path != self.active_path:
            return self.requested_path
        worktree_root = self._worktree_root()
        git_marker = worktree_root / ".git"
        if not git_marker.is_file():
            return None
        try:
            marker = git_marker.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not marker.startswith("gitdir:"):
            return None
        git_dir = Path(marker.split(":", 1)[1].strip()).resolve()
        if git_dir.parent.name != "worktrees":
            return None
        repository_root = git_dir.parent.parent.parent
        return (
            repository_root
            / ".cafe"
            / "issues"
            / self.active_path.parent.name
            / "issue.yaml"
        )

    def _worktree_root(self) -> Path:
        for parent in self.active_path.parents:
            if parent.name == ".cafe":
                return parent.parent
        return self.active_path.parent

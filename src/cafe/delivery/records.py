"""Atomic per-action attempts; unknown effects require positive reconciliation."""

from __future__ import annotations

import fcntl
import json
import re
from contextlib import contextmanager
from pathlib import Path

from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.delivery.contracts import ActionSnapshot


class ActionStore:
    def __init__(self, issue_dir: Path, snapshot: ActionSnapshot):
        self.directory = issue_dir / "delivery" / snapshot.digest
        self.snapshot = snapshot

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory.parent / "actions.lock").open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield self
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def path(self, action: str) -> Path:
        if not re.fullmatch(r"integration|FUP-[0-9]{3}", action):
            raise ValueError("invalid action identity")
        return self.directory / f"{action}.json"

    def read(self, action: str) -> dict | None:
        path = self.path(action)
        if not path.exists():
            return None
        if path.stat().st_size > 65536:
            raise ValueError("oversized action receipt")
        record = json.loads(path.read_text())
        if record.get("snapshot") != self.snapshot.digest or record.get("action") != action:
            raise ValueError("action receipt identity changed")
        if record.get("state") not in {"unknown", "succeeded", "not_dispatched", "blocked"}:
            raise ValueError("invalid action state")
        return record

    def start(self, action: str):
        prior = self.read(action)
        if prior is not None and prior["state"] != "not_dispatched":
            raise ValueError("action needs reconciliation before retry")
        self.finish(action, {"state": "unknown"})

    def finish(self, action: str, result: dict):
        atomic_write_bytes(
            self.path(action),
            canonical_json(
                {
                    **result,
                    "snapshot": self.snapshot.digest,
                    "action": action,
                }
            ),
        )

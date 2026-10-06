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
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                manifest = self.directory / "actions.json"
                content = canonical_json(self.snapshot.model_dump(mode="json"))
                if manifest.exists() and manifest.read_bytes() != content:
                    raise ValueError("action manifest changed")
                atomic_write_bytes(manifest, content)
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

    def correlated_attempt(self, action: str):
        """Revised human approval does not erase an earlier uncertain identical effect."""
        directories = list(self.directory.parent.glob("*/actions.json"))
        if len(directories) > 100:
            raise ValueError("delivery history requires bounded manual reconciliation")
        for manifest in directories:
            if manifest.parent == self.directory:
                continue
            if manifest.stat().st_size > 1024 * 1024:
                raise ValueError("oversized historical action manifest")
            other = ActionSnapshot.model_validate_json(manifest.read_bytes())
            if other.proposal.workflow_id != self.snapshot.proposal.workflow_id:
                continue
            if action != "integration":
                if action not in {p.id for p in other.selected} or other.marker(
                    action
                ) != self.snapshot.marker(action):
                    continue
            else:
                keys = (
                    "mode",
                    "repository",
                    "source_oid",
                    "source_branch",
                    "target_branch",
                    "strategy",
                    "pr_number",
                    "destination",
                )
                if any(
                    getattr(other.proposal, key) != getattr(self.snapshot.proposal, key)
                    for key in keys
                ):
                    continue
            record = ActionStore(self.directory.parent.parent, other).read(action)
            if record and record["state"] in {"unknown", "succeeded"}:
                return record
        return None

    def start(self, action: str):
        prior = self.read(action)
        if prior is not None and prior["state"] not in {"not_dispatched", "blocked"}:
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

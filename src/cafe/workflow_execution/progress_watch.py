"""Observe meaningful advances while the host waits for a provider."""

from __future__ import annotations

import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from cafe.core.checklist import _checklist_item_blocks
from cafe.core.packet_io import read_regular_bytes

_COMPLETED = re.compile(r"^### ([A-Za-z][\w-]*)\s*\n- Status: completed\s*$", re.MULTILINE)


class ProgressWatch:
    """One monotonic deadline per active owner, independent of its output rate."""

    def __init__(self, output: Path, *, clock=time.monotonic):
        self.output = output
        self.clock = clock
        self.last_advance = clock()
        self.progress_at = datetime.now(timezone.utc).isoformat()
        self.source = "worker_start"
        self.next_check = self.last_advance
        self.reported = False
        self.seen = set()
        self.stamps = {}
        self.checklist_items = None
        self._observe_items()

    def _changed_text(self, path):
        try:
            info = path.lstat()
            stamp = (info.st_ctime_ns, info.st_dev, info.st_ino)
            if self.stamps.get(path) == stamp or not stat.S_ISREG(info.st_mode):
                return ""
            text = read_regular_bytes(path, 256 * 1024).decode()
            self.stamps[path] = stamp
            return text
        except (OSError, ValueError):
            return ""

    def _observe_items(self):
        content = self._changed_text(self.output)
        _, section, ledger = content.partition("## Todo Progress\n")
        completed = {("completed_todo", key) for key in _COMPLETED.findall(ledger.split("\n## ", 1)[0])} if section else set()
        checklist = self._changed_text(self.output.with_name("checklist.md"))
        if checklist:
            items = _checklist_item_blocks(checklist)
            if self.checklist_items is None and items:
                self.checklist_items = {block for _, block, done in items if not done}
            elif self.checklist_items:
                completed.update(("completed_checklist_item", block) for _, block, done in items
                                 if done and block in self.checklist_items)
        new = completed - self.seen
        self.seen.update(new)
        return next((source for source, _ in new), None)

    def due(self, limit: float) -> dict | None:
        now = self.clock()
        if now < self.next_check and now - self.last_advance < limit:
            return None
        if self.reported and now < self.next_check:
            return None
        self.next_check = now + min(2, limit / 8)
        advance = self._observe_items()
        if advance:
            self.last_advance = now
            self.progress_at = datetime.now(timezone.utc).isoformat()
            self.source = advance
            self.reported = False
        elapsed = now - self.last_advance
        if elapsed < limit or self.reported:
            return None
        self.reported = True
        return {"wait_seconds": limit, "elapsed_seconds": int(elapsed),
                "progress_at": self.progress_at, "progress_source": self.source}

"""Observe meaningful advances while the host waits for a provider."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

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
        self._stamp = None
        self._observe_items()

    def _observe_items(self) -> bool:
        try:
            stat = self.output.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if stamp == self._stamp or self.output.is_symlink() or stat.st_size > 256 * 1024:
                return False
            self._stamp = stamp
            content = self.output.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        _, section, ledger = content.partition("## Todo Progress\n")
        items = set(_COMPLETED.findall(ledger.split("\n## ", 1)[0])) if section else set()
        new = items - self.seen
        self.seen.update(new)
        return bool(new)

    def due(self, limit: float) -> dict | None:
        now = self.clock()
        if now < self.next_check and now - self.last_advance < limit:
            return None
        if self.reported and now < self.next_check:
            return None
        self.next_check = now + min(2, limit / 8)
        if self._observe_items():
            self.last_advance = now
            self.progress_at = datetime.now(timezone.utc).isoformat()
            self.source = "completed_todo"
            self.reported = False
        elapsed = now - self.last_advance
        if elapsed < limit or self.reported:
            return None
        self.reported = True
        return {"wait_seconds": limit, "elapsed_seconds": int(elapsed),
                "progress_at": self.progress_at, "progress_source": self.source}

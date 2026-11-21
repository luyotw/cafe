"""Observe meaningful advances while the host waits for a provider."""

from __future__ import annotations

import re
import stat
import time
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from cafe.core.checklist import MAX_CHECKLIST_CONTENT_BYTES, MAX_CHECKLIST_GATES, _CHECKBOX_LINE
from cafe.core.packet_io import read_regular_bytes

_COMPLETED = re.compile(r"^### ([A-Za-z][\w-]*)\s*\n- Status: completed\s*$", re.MULTILINE)


def _gate_states(checklist):
    """Identify each occurrence without copying its descendants into every parent block."""
    groups = defaultdict(list)
    section = ""
    active = []
    gate_count = 0

    def close_gate():
        _, digest, checked = active.pop()
        block = digest.digest()
        groups[section, block].append(checked)
        if active:
            active[-1][1].update(b"C" + block)

    for line in checklist.splitlines():
        match = _CHECKBOX_LINE.match(line)
        if match:
            gate_count += 1
            if gate_count > MAX_CHECKLIST_GATES:
                return {}
            indent = len(match["indent"].expandtabs(4))
            while active and active[-1][0] >= indent:
                close_gate()
            normalized = line[:match.start("state")] + " " + line[match.end("state"):]
            active.append((indent, sha256((normalized + "\n").encode()), match["state"].lower() == "x"))
        elif line.strip() and not line.startswith((" ", "\t")):
            while active:
                close_gate()
            if line.startswith("#"):
                section = line
        elif active and line.strip():
            # Frame each subordinate rule so a child and a literal line stay distinct.
            active[-1][1].update(b"L" + sha256(line.encode()).digest())
    while active:
        close_gate()
    return groups


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

    def _changed_text(self, path, limit):
        try:
            info = path.lstat()
            stamp = (info.st_ctime_ns, info.st_dev, info.st_ino)
            if self.stamps.get(path) == stamp or not stat.S_ISREG(info.st_mode):
                return ""
            text = read_regular_bytes(path, limit).decode()
            self.stamps[path] = stamp
            return text
        except (OSError, ValueError):
            return ""

    def _observe_items(self):
        content = self._changed_text(self.output, 256 * 1024)
        _, section, ledger = content.partition("## Todo Progress\n")
        completed = {("completed_todo", key) for key in _COMPLETED.findall(ledger.split("\n## ", 1)[0])} if section else set()
        checklist = self._changed_text(self.output.with_name("checklist.md"), MAX_CHECKLIST_CONTENT_BYTES)
        if checklist:
            groups = _gate_states(checklist)
            if self.checklist_items is None and groups:
                self.checklist_items = groups
            elif self.checklist_items:
                for group, states in groups.items():
                    original = self.checklist_items.get(group, ())
                    # An insertion or removal makes identical occurrences ambiguous.
                    if len(states) == len(original):
                        completed.update(
                            ("completed_checklist_item", (group, index))
                            for index, checked in enumerate(states) if checked and not original[index]
                        )
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

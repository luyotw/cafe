"""Observe meaningful advances while the host waits for a provider."""

from __future__ import annotations

import re
import stat
import time
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from cafe.core.checklist import MAX_CHECKLIST_CONTENT_BYTES, _CHECKBOX_LINE
from cafe.core.packet_io import read_regular_bytes

_COMPLETED = re.compile(r"^### ([A-Za-z][\w-]*)\s*\n- Status: completed\s*$", re.MULTILINE)


def _gate_states(lines, pending_rows):
    """Identify each occurrence without copying its descendants into every parent block."""
    groups = defaultdict(bytearray)
    section = ""
    active = []

    def close_gate():
        _, digest, checked, tracked = active.pop()
        block = digest.digest()
        if tracked:
            groups[section, block].append(checked)
        if active:
            active[-1][1].update(b"C" + block)

    for line in lines:
        match = _CHECKBOX_LINE.match(line)
        if match:
            indent = len(match["indent"].expandtabs(4))
            while active and active[-1][0] >= indent:
                close_gate()
            normalized = line[:match.start("state")] + " " + line[match.end("state"):]
            tracked = normalized in pending_rows
            if tracked or active:
                digest = sha256((normalized + "\n").encode())
                active.append((indent, digest, match["state"] != " ", tracked))
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
        self.pending_rows = set()
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

    def _checklist_advanced(self):
        if self.checklist_items == {}:
            return False
        path = self.output.with_name("checklist.md")
        checklist = self._changed_text(path, MAX_CHECKLIST_CONTENT_BYTES)
        if not checklist:
            return False
        lines = checklist.splitlines()
        if self.checklist_items is None:
            # A live checklist can contain arbitrarily many supplemental notes
            # within the byte bound. Only initially outstanding rows can advance.
            has_items = False
            for line in lines:
                match = _CHECKBOX_LINE.match(line)
                if match:
                    has_items = True
                    if match["state"] == " ":
                        self.pending_rows.add(line)
            if has_items:
                self.checklist_items = _gate_states(lines, self.pending_rows) if self.pending_rows else {}
            return False

        advanced = False
        for group, states in _gate_states(lines, self.pending_rows).items():
            original = self.checklist_items.get(group)
            # An insertion or removal makes identical occurrences ambiguous.
            if original is None or len(states) != len(original):
                continue
            for index, checked in enumerate(states):
                if checked and not original[index]:
                    original[index] = 1
                    advanced = True
            if 0 not in original:
                del self.checklist_items[group]
        return advanced

    def _observe_items(self):
        content = self._changed_text(self.output, 256 * 1024)
        _, section, ledger = content.partition("## Todo Progress\n")
        completed = {("completed_todo", key) for key in _COMPLETED.findall(ledger.split("\n## ", 1)[0])} if section else set()
        advanced = self._checklist_advanced()
        new = completed - self.seen
        self.seen.update(new)
        return "completed_checklist_item" if advanced else next((source for source, _ in new), None)

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

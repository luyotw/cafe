"""Read-only boot and process evidence for recovering workflow workers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping


@lru_cache(maxsize=1)
def host_boot() -> tuple[str | None, int | None]:
    """Return the current boot identity and the start time for legacy records."""
    try:
        if sys.platform == "darwin":
            boot = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid", "kern.boottime"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=2,
            ).stdout.splitlines()
            boot_id = str(uuid.UUID(boot[0].strip()))
            start = re.search(r"sec\s*=\s*(\d+)", boot[1])
            return boot_id, int(start[1]) if start else None
        if sys.platform.startswith("linux"):
            boot_id = str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
            start = next(
                (int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                 if line.startswith("btime ")), None,
            )
            return boot_id, start
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return None, None


def _process_absent(pid: object, probe: Callable[[int, int], None]) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        probe(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False


def worker_abandoned(record: Mapping[str, Any]) -> bool:
    """Check process ownership before retiring a worker record."""
    boot_id, boot_started_at = host_boot()
    saved_boot = record.get("boot_id")
    if boot_id and saved_boot:
        if saved_boot != boot_id:
            return True
    elif boot_started_at is not None:
        # Old releases did not record a boot ID. Require both the older
        # timestamp and an absent PID to avoid retiring an unverified live worker.
        try:
            last_update = datetime.fromisoformat(record.get("updated_at", ""))
            if (last_update.tzinfo is not None
                    and last_update.timestamp() < boot_started_at
                    and _process_absent(record.get("pid"), os.kill)):
                return True
        except (TypeError, ValueError, OverflowError):
            pass
    if not _process_absent(record.get("pid"), os.kill):
        return False
    provider = record.get("provider")
    if not isinstance(provider, dict):
        return False
    return provider.get("state") == "stopped" or (
        hasattr(os, "killpg") and _process_absent(provider.get("pid"), os.killpg)
    )

"""Stop only the provider session created by this execution, then verify it."""

import os
from pathlib import Path
import signal
import time


def live_processes():
    result = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = path.read_text()
            fields = raw[raw.rindex(")") + 2 :].split()
            if fields[0] != "Z":
                result[int(path.parent.name)] = (int(fields[1]), int(fields[2]), fields[19])
        except (OSError, ValueError, IndexError):
            continue
    return result


def group_stopped(pid):
    if not Path("/proc/self/stat").is_file():
        return False  # No verified process-group observation on this host.
    if any(pgid == pid for _, pgid, _ in live_processes().values()):
        return False
    # Zombies have no effects. If the group exists but its members cannot be
    # observed, fail closed instead of treating unreadable /proc data as absence.
    observed = False
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = path.read_text()
            fields = raw[raw.rindex(")") + 2 :].split()
            if int(fields[2]) == pid:
                if fields[0] != "Z":
                    return False
                observed = True
        except (OSError, ValueError, IndexError):
            continue
    if observed:
        return True
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False


def stop_provider(process):
    """The unreaped direct child pins its PID while its own process group is signalled."""
    if os.name != "posix" or not Path("/proc/self/stat").is_file():
        raise RuntimeError("provider quiescence cannot be verified on this host")
    with process._waitpid_lock:
        if process.returncode is not None:
            if group_stopped(process.pid):
                return
            raise RuntimeError("provider was already reaped; process-group ownership is unverified")
        before = live_processes()
        owned = {process.pid}
        while True:
            children = {pid for pid, (parent, _, _) in before.items() if parent in owned}
            if children <= owned:
                break
            owned |= children
        escaped = {
            pid: before[pid][2] for pid in owned if pid in before and before[pid][1] != process.pid
        }
        for sig, timeout in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + timeout
            while not group_stopped(process.pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            if group_stopped(process.pid):
                break
    process.wait(timeout=2)
    after = live_processes()
    if not group_stopped(process.pid) or any(
        pid in after and after[pid][2] == born for pid, born in escaped.items()
    ):
        raise RuntimeError("owned descendants remain unverified; correction is frozen")


def process_birth(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw[raw.rindex(")") + 2 :].split()[19]
    except (OSError, ValueError, IndexError):
        return None

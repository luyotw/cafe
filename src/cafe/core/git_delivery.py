"""Shared Git endpoint identity for resolved delivery authority."""

import hashlib
from pathlib import Path
import subprocess


def git_text(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, timeout=20).stdout.strip()


def remote_identity(root: Path, remote: str, route: str) -> str:
    push = git_text(root, "remote", "get-url", "--push", "--all", remote).splitlines()
    if len(push) != 1 or not push[0]:
        raise ValueError("delivery requires one exact remote push endpoint")
    if route == "direct":
        return hashlib.sha256(push[0].encode()).hexdigest()
    fetch = git_text(root, "remote", "get-url", "--all", remote).splitlines()
    if len(fetch) != 1 or not fetch[0]:
        raise ValueError("publication requires one exact fetch endpoint")
    from cafe.core.packet_io import canonical_json
    return hashlib.sha256(canonical_json({"fetch": fetch[0], "push": push[0]})).hexdigest()

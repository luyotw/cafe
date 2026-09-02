#!/usr/bin/env python3
"""Send one secret-free Slack summary after the coverage test command exits."""

from __future__ import annotations

import json
import os
import pwd
import re
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Test fixtures can create many HumanTasks and use the dedicated test channel.
# One completed test-run summary is useful in the normal HumanTask channel.
WEBHOOK_PATH = Path(".slack-webhook")
WEBHOOK_HOST = "hooks.slack.com"
MAX_CREDENTIAL_BYTES = 8192
MAX_LOG_BYTES = 64 * 1024
MAX_SUMMARY_CHARS = 1024
MAX_MESSAGE_CHARS = 4096
SUMMARY_PATTERN = re.compile(r"^(.+\b(?:passed|failed|error|skipped|xfailed)\b.+)$")
COVERAGE_PATTERN = re.compile(r"^TOTAL\s+\d+\s+\d+\s+(\d+%)$")


class NoRedirect(HTTPRedirectHandler):
    """Fail closed instead of following a redirect from a trusted webhook host."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def trusted_user_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def load_test_webhook_url() -> str:
    """Read the local test-results webhook without accepting project input."""
    credential_file = trusted_user_home() / WEBHOOK_PATH
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(credential_file, flags)
    except OSError as exc:
        raise RuntimeError("test_webhook_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or metadata.st_size > MAX_CREDENTIAL_BYTES
        ):
            raise RuntimeError("test_webhook_unsafe")
        raw_url = os.read(descriptor, MAX_CREDENTIAL_BYTES + 1).decode("utf-8").strip()
    except UnicodeError as exc:
        raise RuntimeError("test_webhook_invalid") from exc
    finally:
        os.close(descriptor)
    if not raw_url:
        raise RuntimeError("test_webhook_invalid")
    parsed = urlparse(raw_url)
    path_parts = parsed.path.removeprefix("/").split("/")
    if not (
        parsed.scheme == "https"
        and parsed.hostname == WEBHOOK_HOST
        and parsed.port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and len(path_parts) == 4
        and path_parts[0] == "services"
        and all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in path_parts[1:])
    ):
        raise RuntimeError("test_webhook_invalid")
    return raw_url


def read_test_summary(log_path: Path) -> tuple[str, str | None]:
    """Extract only the compact pytest result and aggregate coverage from the run log."""
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            log_size = log_file.tell()
            log_file.seek(max(0, log_size - MAX_LOG_BYTES))
            raw_tail = log_file.read(MAX_LOG_BYTES)
    except OSError:
        return "pytest output unavailable", None
    lines = raw_tail.decode("utf-8", errors="replace").splitlines()
    summary = "pytest output unavailable"
    coverage = None
    for line in reversed(lines):
        stripped = line.strip(" =")
        if SUMMARY_PATTERN.fullmatch(stripped):
            summary = (
                stripped
                if len(stripped) <= MAX_SUMMARY_CHARS
                else f"…{stripped[-(MAX_SUMMARY_CHARS - 1):]}"
            )
            break
    for line in reversed(lines):
        match = COVERAGE_PATTERN.fullmatch(line.strip())
        if match:
            coverage = match.group(1)
            break
    return summary, coverage


def format_duration(seconds: int) -> str:
    minutes, remainder = divmod(max(seconds, 0), 60)
    return f"{minutes}m {remainder}s" if minutes else f"{remainder}s"


def post_message(webhook_url: str, message: dict[str, str]) -> None:
    request = Request(
        webhook_url,
        data=json.dumps(message).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=5.0) as response:
            if response.getcode() != 200 or response.read() != b"ok":
                raise RuntimeError("test_webhook_delivery_failed")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("test_webhook_delivery_failed") from exc


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        return 2
    test_status, elapsed_seconds, report_path, log_path = argv[1:]
    try:
        elapsed = int(elapsed_seconds)
    except ValueError:
        return 2
    summary, coverage = read_test_summary(Path(log_path))
    outcome = "passed" if test_status == "0" else "failed"
    text = [
        f"CAFE coverage test run {outcome}",
        f"Repository: {Path.cwd().name}",
        f"Duration: {format_duration(elapsed)}",
        f"Pytest: {summary}",
    ]
    if coverage is not None:
        text.append(f"Coverage: {coverage}")
    text.append(f"Durations: {report_path}")
    message_text = "\n".join(text)
    if len(message_text) > MAX_MESSAGE_CHARS:
        message_text = f"{message_text[: MAX_MESSAGE_CHARS - 1]}…"
    try:
        post_message(load_test_webhook_url(), {"text": message_text})
    except RuntimeError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

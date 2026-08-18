"""Minimal Git-hook entrypoint for global helper skill synchronization."""

from __future__ import annotations

import sys

from cafe.skills.global_installer import sync_global_skills


def main() -> int:
    """Verify managed copies without importing the full interactive CLI."""
    try:
        summary = sync_global_skills()
    except Exception as exc:
        print(f"Global helper skill auto-sync failed: {exc}", file=sys.stderr)
        return 1

    if summary.failed_count:
        print(
            f"Global helper skill auto-sync failed for {summary.failed_count} installation(s):",
            file=sys.stderr,
        )
        for result in summary.results:
            if result.status == "failed":
                print(f"  {result.cli}/{result.skill}: {result.reason}", file=sys.stderr)
        return 1
    if summary.changed_count:
        print(f"Synchronized {summary.changed_count} global helper skill installation(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

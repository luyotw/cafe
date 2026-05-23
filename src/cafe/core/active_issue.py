"""Runtime marker for active issue fallback when Git branch detection is unhealthy."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

MARKER_FILENAME = "active_issue"


def marker_path(cafe_dir: Path) -> Path:
    """Return the path to the active issue marker file."""
    return cafe_dir / MARKER_FILENAME


def read_marker(cafe_dir: Path) -> Optional[str]:
    """Read the active issue name from the marker file, or None if missing/empty."""
    path = marker_path(cafe_dir)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def write_marker(cafe_dir: Path, issue_name: str) -> None:
    """Write the active issue marker for the given issue name."""
    cafe_dir.mkdir(parents=True, exist_ok=True)
    marker_path(cafe_dir).write_text(f"{issue_name.strip()}\n", encoding="utf-8")


def clear_marker(cafe_dir: Path) -> None:
    """Remove the active issue marker file if it exists."""
    path = marker_path(cafe_dir)
    if path.is_file():
        path.unlink()


def clear_marker_if_matches(cafe_dir: Path, issue_name: str) -> bool:
    """Clear the marker only when it matches the given issue name."""
    current = read_marker(cafe_dir)
    if current != issue_name.strip():
        return False
    clear_marker(cafe_dir)
    return True


def issue_exists(cafe_dir: Path, issue_name: str) -> bool:
    """Return True when a prepared issue directory exists."""
    return (cafe_dir / "issues" / issue_name.strip()).is_dir()

"""Safe relative issue identities, including branch-style namespaces."""

import re
from collections.abc import Iterator
from pathlib import Path

_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def validate_issue_name(value: str) -> str:
    """Reject aliases, traversal, absolute paths and empty path components."""
    if not isinstance(value, str) or not all(
        _COMPONENT.fullmatch(part) for part in value.split("/")
    ):
        raise ValueError("issue name is invalid")
    return value


def issue_name_from_dir(issue_dir: Path) -> str:
    """Preserve namespaces under .cafe/issues; retain standalone legacy paths."""
    issue_dir = Path(issue_dir)
    for parent in issue_dir.parents:
        if parent.name == "issues" and parent.parent.name == ".cafe":
            return validate_issue_name(issue_dir.relative_to(parent).as_posix())
    return validate_issue_name(issue_dir.name)


def issue_project_root(issue_dir: Path) -> Path:
    for parent in Path(issue_dir).parents:
        if parent.name == "issues" and parent.parent.name == ".cafe":
            return parent.parent.parent
    return Path(issue_dir).parent


def validate_issue_directory(issue_dir: Path) -> None:
    """Do not follow namespace or authority-directory symlinks."""
    root = issue_project_root(issue_dir)
    current = Path(issue_dir)
    while current != root:
        if current.is_symlink() or not current.is_dir():
            raise ValueError("prepared workflow checkout is unsafe")
        current = current.parent


def iter_issue_directories(root: Path) -> Iterator[Path]:
    """Walk namespaces, but never traverse links or contents of prepared issues."""
    if root.is_symlink() or not root.is_dir():
        return
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_dir():
            continue
        if (path / "blackboard.json").exists() or (path / "human_tasks.json").exists():
            yield path
        else:
            yield from iter_issue_directories(path)

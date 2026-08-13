"""Policy tests for guided Git initialization during workflow kickoff."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KICKOFF_REFERENCE = (
    PROJECT_ROOT
    / "src"
    / "cafe"
    / "data"
    / "skills"
    / "use-cafe-workflow"
    / "references"
    / "kickoff.md"
)


def test_driver_kickoff_requires_approval_before_guided_git_initialization() -> None:
    content = KICKOFF_REFERENCE.read_text(encoding="utf-8")
    normalized = " ".join(content.split())

    assert "does not create GitHub resources or upload files" in normalized
    assert "require the kickoff confirmation before mutation" in normalized
    assert "pass `--init-git` to `cafe prepare`" in normalized
    assert "Do not request a worktree for that first task" in normalized
    assert "replace the final `--worktree ...` argument with `--current-checkout`" in normalized
    assert "For an existing initialized repository, recommend a worktree" in normalized
    assert "instead record and render the `current checkout` choice" in normalized

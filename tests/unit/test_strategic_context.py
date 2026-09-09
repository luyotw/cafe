"""Tests for strategic context loading and document metadata."""

from pathlib import Path

from cafe.core.strategic_context import load_strategic_context


def test_loads_repo_context_and_document_hashes(tmp_path: Path) -> None:
    (tmp_path / ".cafe").mkdir()
    (tmp_path / "docs").mkdir()
    roadmap = tmp_path / "docs" / "roadmap.md"
    roadmap.write_text("# Roadmap\n\nCurrent product direction.\n", encoding="utf-8")
    (tmp_path / ".cafe" / "strategic_context.yaml").write_text(
        """
version: 1
documents:
  roadmap:
    path: docs/roadmap.md
    status: exists
  positioning:
    path: docs/positioning.md
    status: missing
mandate:
  playbook_id: standard
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap]
    technical:
      level: agent
  out_of_mandate:
    - pricing
issues:
  issue-1:
    playbook_id: tdd
    axes:
      product_scope:
        level: agent
        grounds: [roadmap]
""",
        encoding="utf-8",
    )

    context = load_strategic_context(tmp_path, issue_name="issue-1")

    assert context.playbook_id is None
    assert context.axes["product_scope"].level == "agent"
    assert context.axes["technical"].level == "agent"
    assert context.out_of_mandate == ("pricing",)
    assert context.document("roadmap").sha256
    assert context.document("positioning").status == "missing"
    assert context.document("principles").path is None


def test_missing_config_degrades_to_empty_context(tmp_path: Path) -> None:
    context = load_strategic_context(tmp_path, issue_name="missing")

    assert context.version == 1
    assert context.axes == {}
    assert context.document("principles").status == "missing"

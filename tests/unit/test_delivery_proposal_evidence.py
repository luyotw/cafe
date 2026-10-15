"""Open draft evidence stays distinct from references to fixed findings."""

import json
from types import SimpleNamespace

import pytest

import cafe.core.hooks  # Initialize the public hook registry first.
from cafe.delivery.phase_hooks import _proposals


def read_proposals(tmp_path, narrative, rows, drafts, *, table=None):
    path = tmp_path / "custom-review.md"
    if table is None:
        table = ("| ID | title | status |\n| --- | --- | --- |\n"
                 + "\n".join(f"| {identity} | Draft | {status} |" for identity, status in rows))
    path.write_text(
        "## Follow-up Proposals\n\n" + narrative + "\n\n"
        + table
        + "\n\n```json\n" + json.dumps({"proposals": [
            {"id": identity, "title": "Draft", "body": "Original body", "evidence": "file:1",
             "evidence_head": "a" * 40, "impact": "Important", "confidence": 99}
            for identity in drafts
        ]}) + "\n```\n"
    )
    state = SimpleNamespace(artifacts={"custom_findings": SimpleNamespace(
        path=str(path), version=7, updated_by="custom_inspector",
    )})
    binding = SimpleNamespace(proposals_artifact="custom_findings")
    before = path.read_bytes()
    result = _proposals(state, binding, tmp_path)
    assert path.read_bytes() == before
    return result


def test_fixed_prose_reference_is_not_an_open_draft(tmp_path):
    selected, source = read_proposals(
        tmp_path, "FUP-006 was fixed and removed from open proposals.",
        [("FUP-002", "open"), ("FUP-003", "open"), ("FUP-005", "open")],
        ["FUP-002", "FUP-003", "FUP-005"],
    )
    assert [item.id for item in selected] == ["FUP-002", "FUP-003", "FUP-005"]
    assert source.artifact == "custom_findings" and source.producer == "custom_inspector"
    assert len(source.sha256) == 64


def test_fixed_table_row_and_cross_reference_are_not_selected(tmp_path):
    selected, _ = read_proposals(
        tmp_path, "FUP-006 is fixed; FUP-002 remains independent of FUP-006.",
        [("FUP-002", "open"), ("FUP-006", "fixed")], ["FUP-002"],
    )
    assert [item.id for item in selected] == ["FUP-002"]


@pytest.mark.parametrize("drafts", [[], ["FUP-006"], ["FUP-002", "FUP-006"]])
def test_missing_or_extra_open_drafts_still_fail_closed(tmp_path, drafts):
    with pytest.raises(ValueError, match="draft IDs"):
        read_proposals(tmp_path, "FUP-006 is fixed.",
                       [("FUP-002", "open"), ("FUP-006", "fixed")], drafts)


def test_all_fixed_table_can_have_no_open_drafts(tmp_path):
    selected, _ = read_proposals(
        tmp_path, "FUP-006 is fixed.", [("FUP-006", "fixed")], [],
    )
    assert selected == ()


@pytest.mark.parametrize("rows", [
    [("FUP-002", "unknown")],
    [("FUP-002", "open"), ("FUP-002", "fixed")],
])
def test_ambiguous_table_status_still_fails_closed(tmp_path, rows):
    with pytest.raises(ValueError, match="status is ambiguous"):
        read_proposals(tmp_path, "", rows, ["FUP-002"])


@pytest.mark.parametrize("table", [
    "| ID | status | status |\n| --- | --- | --- |\n| FUP-002 | fixed | open |",
    "| ID | status |\n| --- | --- |\n| [FUP-002](#finding) | open |",
    "ID | status\n--- | ---\nFUP-002 | open",
    "| ID | status |\n| --- | --- |\n| invalid | open |",
])
def test_open_rows_cannot_be_silently_dropped(tmp_path, table):
    with pytest.raises(ValueError):
        read_proposals(tmp_path, "", [], [], table=table)


@pytest.mark.parametrize("table", [
    "| ID | title | status |\n| --- | --- | --- |\n| FUP-002 | Handle A \\| B | open |",
    "ID | status\n--- | ---\n[FUP-002](#finding) | open",
])
def test_valid_markdown_rows_keep_their_original_drafts(tmp_path, table):
    selected, _ = read_proposals(tmp_path, "FUP-006 is fixed.", [], ["FUP-002"], table=table)
    assert [item.id for item in selected] == ["FUP-002"]

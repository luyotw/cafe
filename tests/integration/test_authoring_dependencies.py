"""U4–U10, I2/I3/I4/I6: selective preparation through public authoring callers."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from cafe.authoring import apply, decode_request, prepare

FIXTURE = Path(__file__).parents[1] / "fixtures/authoring/pair.yaml"
PHASE = ".cafe/skills/cafe-observe/SKILL.md"


def pair():
    return decode_request(FIXTURE.read_text())


def book_patch(request):
    return {
        "version": 1,
        "target": request["target"],
        "mode": "patch",
        "operations": [
            {
                "op": "replace",
                "path": ["playbook", "applicability", "summary"],
                "expected": request["declaration"]["playbook"]["applicability"]["summary"],
                "value": "Collect an author-reviewed observation.",
                "overwrite": True,
            }
        ],
    }


@pytest.mark.parametrize(
    "unrelated", ["skill", "resource", "playbook", "invalid_skill", "unused_resource"]
)
def test_unrelated_content_does_not_change_digest_or_stale_reject_apply(tmp_path, unrelated):
    request = pair()
    assert apply(request, root=tmp_path).status == "applied"
    extra = deepcopy(request["companions"][0])
    extra["target"] = ".cafe/skills/cafe-unrelated/SKILL.md"
    extra["declaration"]["name"] = "cafe-unrelated"
    extra["references"] = {"references/notes.md": "Unrelated notes.\n"}
    assert apply(extra, root=tmp_path).status == "applied"
    other = deepcopy(request)
    other.pop("companions")
    other["target"] = ".cafe/playbooks/unrelated.yaml"
    other["declaration"]["playbook"]["id"] = "unrelated"
    other["declaration"]["steps"]["observe"]["skill"] = "cafe-unrelated"
    assert apply(other, root=tmp_path).status == "applied"
    path = {
        "skill": extra["target"],
        "invalid_skill": extra["target"],
        "resource": ".cafe/skills/cafe-unrelated/references/notes.md",
        "playbook": other["target"],
        "unused_resource": ".cafe/skills/cafe-observe/references/unused.md",
    }[unrelated]
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        source.write_text("Unreferenced owner notes.\n")
    patch = book_patch(request)
    preview = prepare(patch, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    before = (tmp_path / request["target"]).read_bytes()
    if unrelated == "invalid_skill":
        source.write_text("---\nname: different-directory\nworkflow: [invalid\n---\n")
    else:
        source.write_text(source.read_text() + "\n# Unrelated owner change\n")
    after = prepare(patch, root=tmp_path)
    assert after.status == "ready", after.to_dict()
    assert after.change_digest == preview.change_digest
    assert after.diff == preview.diff
    assert (tmp_path / request["target"]).read_bytes() == before
    result = apply(patch, root=tmp_path, expect_change=preview.change_digest)
    assert result.status == "applied", result.to_dict()
    assert result.diff == preview.diff


@pytest.mark.parametrize("dependency", ["skill", "reference"])
def test_used_source_content_still_rejects_stale_apply_before_writes(tmp_path, dependency):
    request = pair()
    phase = request["companions"][0]
    phase["declaration"]["workflow"]["prompt_references"] = {"notes_file": "notes.md"}
    phase["references"] = {"references/notes.md": "Used notes.\n"}
    assert apply(request, root=tmp_path).status == "applied"
    patch = book_patch(request)
    preview = prepare(patch, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    source = tmp_path / (
        PHASE if dependency == "skill" else ".cafe/skills/cafe-observe/references/notes.md"
    )
    source.write_text(source.read_text() + "\n# Used source changed\n")
    before = (tmp_path / request["target"]).read_bytes()
    result = apply(patch, root=tmp_path, expect_change=preview.change_digest)
    assert result.status == "rejected"
    assert any(d["code"] == "stale_preview" for d in result.diagnostics)
    assert (tmp_path / request["target"]).read_bytes() == before


def test_changed_phase_still_revalidates_affected_consumer_routes(tmp_path):
    request = pair()
    assert apply(request, root=tmp_path).status == "applied"
    book = tmp_path / request["target"]
    book.write_text(book.read_text().replace("await_agent: _done", "await_agent: missing"))
    before = (tmp_path / PHASE).read_bytes()
    patch = {
        "version": 1,
        "target": PHASE,
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["metadata", "workflow", "required_tools"], "value": []}
        ],
    }
    result = apply(patch, root=tmp_path)
    assert result.status == "rejected"
    assert any(d["target"] == request["target"] for d in result.diagnostics)
    assert (tmp_path / PHASE).read_bytes() == before


def test_missing_neighbor_skill_does_not_hide_an_affected_consumer(tmp_path):
    request = pair()
    assert apply(request, root=tmp_path).status == "applied"
    book = tmp_path / request["target"]
    raw = deepcopy(request["declaration"])
    raw["entry_point"] = "neighbor"
    raw["steps"] = {
        "neighbor": {"skill": "cafe-missing", "role": "observer", "on": {"await_agent": "observe"}},
        **raw["steps"],
    }
    book.write_text(yaml.safe_dump(raw, sort_keys=False))
    before = (tmp_path / PHASE).read_bytes()
    patch = {
        "version": 1,
        "target": PHASE,
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["metadata", "workflow", "required_tools"], "value": []}
        ],
    }
    result = apply(patch, root=tmp_path)
    assert result.status == "rejected", result.to_dict()
    assert any(d["target"] == request["target"] for d in result.diagnostics)
    assert (tmp_path / PHASE).read_bytes() == before


@pytest.mark.parametrize("malformed", ["primary_variant", "workflow_overlay"])
def test_malformed_neighbor_does_not_hide_known_affected_references(tmp_path, malformed):
    request = pair()
    if malformed == "workflow_overlay":
        primary = deepcopy(request["companions"][0])
        primary["target"] = ".cafe/skills/cafe-primary/SKILL.md"
        primary["declaration"]["name"] = "cafe-primary"
        request["companions"].append(primary)
        request["declaration"]["steps"]["observe"]["skill"] = "cafe-primary"
        request["declaration"]["skills"]["workflow"]["shared"] = ["cafe-observe"]
    assert apply(request, root=tmp_path).status == "applied"
    raw = deepcopy(request["declaration"])
    if malformed == "primary_variant":
        raw["steps"]["observe"]["skill"] = {"1": "cafe-observe", "default": ""}
    else:
        raw["skills"]["workflow"]["roles"] = {"observer": {"mode": "extend"}}
    book = tmp_path / request["target"]
    book.write_text(yaml.safe_dump(raw, sort_keys=False))
    before = {path: path.read_bytes() for path in (tmp_path / PHASE, book)}
    patch = {
        "version": 1,
        "target": PHASE,
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["metadata", "workflow", "required_tools"], "value": []}
        ],
    }
    preview = prepare(patch, root=tmp_path)
    assert preview.status == "rejected", preview.to_dict()
    assert any(d["target"] == request["target"] for d in preview.diagnostics)
    assert {path: path.read_bytes() for path in before} == before
    result = apply(patch, root=tmp_path)
    assert result.status == "rejected", result.to_dict()
    assert {path: path.read_bytes() for path in before} == before


def test_todo_producer_corpus_remains_a_used_dependency(tmp_path):
    request = pair()
    producer = deepcopy(request["companions"][0])
    producer["target"] = ".cafe/skills/cafe-record/SKILL.md"
    producer["declaration"]["name"] = "cafe-record"
    producer["references"] = {
        "references/contract.md": (
            "## Todo List\nSource: record\nWork: observe\nClosure: recorded\nEvidence: source\n"
        )
    }
    request["companions"].append(producer)
    request["declaration"]["entry_point"] = "record"
    request["declaration"]["steps"]["record"] = {
        "skill": "cafe-record",
        "role": "observer",
        "output_artifact": "record",
        "allowed_tools": [],
        "on": {"await_agent": "observe"},
    }
    request["declaration"]["steps"]["observe"]["input_artifacts"] = ["record"]
    workflow = request["companions"][0]["declaration"]["workflow"]
    workflow["prompt_inputs"] = [
        {"placeholder": "record_file", "artifacts": ["record"], "required": True}
    ]
    workflow["checklist_overlay"] = {
        "variants": [
            {
                "when": {},
                "sections": [{"todo_projection": {"artifact": "record", "source": "record"}}],
            }
        ]
    }
    assert apply(request, root=tmp_path).status == "applied"
    patch = book_patch(request)
    preview = prepare(patch, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    contract = tmp_path / ".cafe/skills/cafe-record/references/contract.md"
    contract.write_text(contract.read_text() + "\n# Used corpus changed\n")
    before = (tmp_path / request["target"]).read_bytes()
    assert apply(patch, root=tmp_path, expect_change=preview.change_digest).status == "rejected"
    assert (tmp_path / request["target"]).read_bytes() == before
    contract.write_text("Incomplete producer contract.\n")
    result = prepare(patch, root=tmp_path)
    assert result.status == "rejected"
    assert any(d["code"] == "incomplete_todo_contract" for d in result.diagnostics)


def test_declared_template_catalog_remains_a_used_dependency(tmp_path):
    request = pair()
    assert apply(request, root=tmp_path).status == "applied"
    phase = tmp_path / PHASE
    phase.write_text(
        phase.read_text().replace(
            "workflow:\n", "workflow:\n  output_templates:\n    catalog: field-record\n", 1
        )
    )
    template = phase.parent / "assets/templates/record.md"
    template.parent.mkdir(parents=True)
    template.write_text("## Observation\nRecord the observed evidence.\n")
    patch = book_patch(request)
    preview = prepare(patch, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    template.write_text(template.read_text() + "\n# Used template changed\n")
    before = (tmp_path / request["target"]).read_bytes()
    result = apply(patch, root=tmp_path, expect_change=preview.change_digest)
    assert result.status == "rejected"
    assert any(d["code"] == "stale_preview" for d in result.diagnostics)
    assert (tmp_path / request["target"]).read_bytes() == before

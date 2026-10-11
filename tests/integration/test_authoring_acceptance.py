"""U1–U5/U9, I2/I3/I7: public authoring preservation and binding corrections."""

from copy import deepcopy
from pathlib import Path

import pytest

from cafe.authoring import apply, decode_request, prepare

FIXTURE = Path(__file__).parents[1] / "fixtures/authoring/pair.yaml"
PHASE = ".cafe/skills/cafe-observe/SKILL.md"


def pair():
    return decode_request(FIXTURE.read_text())


@pytest.mark.parametrize("kind", ["playbook", "metadata", "section", "resource"])
def test_focused_crlf_edits_preserve_untouched_bytes(tmp_path, kind):
    request = pair()
    resource = ".cafe/skills/cafe-observe/references/notes.md"
    request["companions"][0]["references"] = {"references/notes.md": "Owner notes.\n"}
    assert apply(request, root=tmp_path).status == "applied"
    paths = [request["target"], PHASE, resource]
    for relative in paths:
        path = tmp_path / relative
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    original = {p: (tmp_path / p).read_bytes() for p in paths}
    target = request["target"] if kind == "playbook" else PHASE
    operation = {
        "playbook": {
            "op": "upsert",
            "path": ["steps", "observe", "allowed_goto"],
            "value": "observe",
        },
        "metadata": {
            "op": "upsert",
            "path": ["metadata", "workflow", "required_tools"],
            "value": [],
        },
        "section": {
            "op": "replace",
            "path": ["sections", "Instructions"],
            "value": "Record the supplied observation.\r\nVerify its provenance.",
            "expected": request["companions"][0]["sections"]["Instructions"],
            "overwrite": True,
        },
        "resource": {
            "op": "replace",
            "path": ["references", "references/notes.md"],
            "value": "Updated owner notes.\r\n",
            "expected": "Owner notes.\r\n",
            "overwrite": True,
        },
    }[kind]
    patch = {"version": 1, "target": target, "mode": "patch", "operations": [operation]}
    preview = prepare(patch, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    assert {p: (tmp_path / p).read_bytes() for p in paths} == original
    result = apply(patch, root=tmp_path, expect_change=preview.change_digest)
    assert result.status == "applied", result.to_dict()
    assert result.diff == preview.diff
    assert result.changes == preview.changes
    changed = resource if kind == "resource" else target
    for relative in paths:
        actual = (tmp_path / relative).read_bytes()
        if relative != changed:
            assert actual == original[relative]
        else:
            assert b"\n" not in actual.replace(b"\r\n", b"")
            if kind == "playbook":
                assert actual.split(b"steps:", 1)[0] == original[relative].split(b"steps:", 1)[0]
            elif kind == "metadata":
                assert (
                    actual.split(b"  required_tools:")[0]
                    == original[relative].rsplit(b"---\r\n", 1)[0]
                )
                assert actual.split(b"---\r\n", 2)[2] == original[relative].split(b"---\r\n", 2)[2]
            elif kind == "section":
                start, end = b"## Instructions\r\n", b"## Output\r\n"
                assert actual.split(start)[0] == original[relative].split(start)[0]
                assert actual.split(end)[1] == original[relative].split(end)[1]
    assert apply(patch, root=tmp_path).status == "noop"


@pytest.mark.parametrize("shape", [b"\r", b"\r\n"])
def test_unsupported_mixed_newlines_reject_before_writes(tmp_path, shape):
    request = pair()
    assert apply(request, root=tmp_path).status == "applied"
    path = tmp_path / request["target"]
    before = path.read_bytes().replace(b"\n", shape, 1)
    path.write_bytes(before)
    patch = {
        "version": 1,
        "target": request["target"],
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["steps", "observe", "allowed_goto"], "value": "observe"}
        ],
    }
    preview = prepare(patch, root=tmp_path)
    assert preview.status == "rejected"
    assert apply(patch, root=tmp_path).status == "rejected"
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", ["First.\rSecond.\n", "First.\r\nSecond.\n"])
def test_unsupported_candidate_newlines_reject_in_preview(tmp_path, content):
    request = pair()["companions"][0]
    request["references"] = {"references/notes.md": content}
    assert prepare(request, root=tmp_path).status == "rejected"
    assert apply(request, root=tmp_path).status == "rejected"
    assert not (tmp_path / ".cafe").exists()


@pytest.mark.parametrize(
    "failure,field",
    [
        ("placeholder", "sections.Instructions.undeclared_file"),
        ("resource", "workflow.prompt_references.ref_file"),
        ("model", "workflow.execution_profile.workload"),
    ],
)
def test_phase_errors_retain_machine_provenance(tmp_path, failure, field):
    request = pair()["companions"][0]
    if failure == "placeholder":
        request["sections"]["Instructions"] = "Read {undeclared_file}."
    elif failure == "resource":
        request["declaration"]["workflow"]["prompt_references"] = {
            "ref_file": "references/missing.md"
        }
    else:
        request["declaration"]["workflow"]["execution_profile"]["workload"] = "unsupported"
    result = prepare(request, root=tmp_path)
    assert result.status == "rejected"
    diagnostics = result.to_dict()["diagnostics"]
    diagnostic = next(d for d in diagnostics if d["field"] == field)
    assert diagnostic["target"] == PHASE
    assert diagnostic["skill"] == "cafe-observe"
    assert diagnostic["step"] is None
    assert diagnostic["remedy"]
    assert not (tmp_path / ".cafe").exists()
    assert apply(request, root=tmp_path).to_dict()["diagnostics"] == diagnostics


@pytest.mark.parametrize("failure", ["placeholder", "resource"])
def test_phase_command_returns_negative_provenance_as_json(tmp_path, monkeypatch, failure):
    import json

    from typer.testing import CliRunner

    from cafe.ui.cli import app

    monkeypatch.chdir(tmp_path)
    request = pair()["companions"][0]
    if failure == "placeholder":
        request["sections"]["Instructions"] = "Read {undeclared_file}."
    else:
        request["declaration"]["workflow"]["prompt_references"] = {
            "ref_file": "references/missing.md"
        }
    expected = prepare(request, root=tmp_path).to_dict()
    response = CliRunner().invoke(
        app,
        ["skill", "author", "phase", "--spec", "-", "--dry-run", "--format", "json"],
        input=json.dumps(request),
    )
    assert response.exit_code == 1
    assert json.loads(response.stdout) == expected
    assert all(
        d["target"] == PHASE and d["skill"] == "cafe-observe" and d["field"]
        for d in expected["diagnostics"]
    )


def producer_pair():
    request = pair()
    workflow = request["companions"][0]["declaration"]["workflow"]
    workflow["prompt_inputs"] = [
        {"placeholder": "record_file", "artifacts": ["missing", "record"], "required": True}
    ]
    producer = deepcopy(request["companions"][0])
    producer["target"] = ".cafe/skills/cafe-collect/SKILL.md"
    producer["declaration"]["name"] = "cafe-collect"
    producer["declaration"]["workflow"].pop("prompt_inputs")
    request["companions"].append(producer)
    request["declaration"]["entry_point"] = "collect"
    request["declaration"]["steps"]["collect"] = {
        "skill": "cafe-collect",
        "role": "observer",
        "input_artifacts": [],
        "output_artifact": "record",
        "allowed_tools": [],
        "on": {"await_agent": "observe"},
    }
    return request


@pytest.mark.parametrize("visibility", ["omitted", "explicit", "empty", "missing"])
def test_required_inputs_follow_runtime_visibility_and_reachable_candidates(tmp_path, visibility):
    request = producer_pair()
    observe = request["declaration"]["steps"]["observe"]
    if visibility in {"omitted", "missing"}:
        observe.pop("input_artifacts")
    elif visibility == "explicit":
        observe["input_artifacts"] = ["record"]
    if visibility == "missing":
        request["declaration"]["steps"]["collect"]["output_artifact"] = "other"
    preview = prepare(request, root=tmp_path)
    if visibility in {"empty", "missing"}:
        assert preview.status == "rejected"
        diagnostic = next(d for d in preview.diagnostics if d["code"] == "missing_producer")
        assert (diagnostic["skill"], diagnostic["step"], diagnostic["field"]) == (
            "cafe-observe",
            "observe",
            "workflow.prompt_inputs.record_file",
        )
        assert apply(request, root=tmp_path).status == "rejected"
        assert not (tmp_path / ".cafe").exists()
    else:
        assert preview.status == "ready", preview.to_dict()
        summary = preview.artifact_summary[request["target"]]
        assert summary["producers"]["record"] == ["collect"]
        assert summary["consumers"]["record"] == ["observe"]
        assert not any(
            d["code"] == "terminal_report" and d["field"] == "record" for d in preview.diagnostics
        )
        applied = apply(request, root=tmp_path, expect_change=preview.change_digest)
        assert applied.status == "applied", applied.to_dict()
        assert applied.artifact_summary == preview.artifact_summary
        assert apply(request, root=tmp_path).status == "noop"


def test_omitted_inputs_accept_entry_binding_and_report_its_consumer(tmp_path):
    request = pair()
    step = request["declaration"]["steps"]["observe"]
    step.pop("input_artifacts")
    step["output_artifact"] = "record"
    step["initial_input"] = {
        "providers": ["manual_text"],
        "bind": {"artifact": "record", "prompt_context": "user_input"},
    }
    step["hooks"] = {"prepare_input": ["InitialInputProviderResolver"]}
    request["companions"][0]["declaration"]["workflow"]["prompt_inputs"] = [
        {"placeholder": "record_file", "artifacts": ["record"], "required": True}
    ]
    preview = prepare(request, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    assert preview.artifact_summary[request["target"]]["consumers"]["record"] == ["observe"]
    assert apply(request, root=tmp_path, expect_change=preview.change_digest).status == "applied"


def test_omitted_inputs_reject_an_unreachable_producer(tmp_path):
    request = producer_pair()
    request["declaration"]["steps"]["observe"].pop("input_artifacts")
    request["declaration"]["steps"]["collect"]["on"] = {"await_agent": "_done"}
    result = prepare(request, root=tmp_path)
    assert result.status == "rejected"
    assert any(
        d["code"] == "missing_producer" and d["step"] == "observe" for d in result.diagnostics
    )
    assert not (tmp_path / ".cafe").exists()


@pytest.mark.parametrize("visibility", ["available", "missing", "empty"])
def test_optional_input_reports_follow_runtime_visibility_without_blocking(tmp_path, visibility):
    request = producer_pair()
    mapping = request["companions"][0]["declaration"]["workflow"]["prompt_inputs"][0]
    mapping["required"] = False
    observe = request["declaration"]["steps"]["observe"]
    if visibility != "empty":
        observe.pop("input_artifacts")
    if visibility == "missing":
        request["declaration"]["steps"]["collect"]["output_artifact"] = "other"
    preview = prepare(request, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    consumers = preview.artifact_summary[request["target"]]["consumers"]
    if visibility == "available":
        assert consumers["record"] == ["observe"]
        assert not any(
            d["code"] == "terminal_report" and d["field"] == "record" for d in preview.diagnostics
        )
    else:
        assert "record" not in consumers
    assert not any(d["code"] == "missing_producer" for d in preview.diagnostics)
    result = apply(request, root=tmp_path, expect_change=preview.change_digest)
    assert result.status == "applied", result.to_dict()
    assert result.artifact_summary == preview.artifact_summary

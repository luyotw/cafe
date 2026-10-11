"""U1/U2/U4/U9: authoring envelope and canonical domain ownership."""

from copy import deepcopy
from pathlib import Path

import pytest

from cafe.authoring import decode_request, prepare

FIXTURE = Path(__file__).parents[1] / "fixtures/authoring/pair.yaml"


def pair():
    return decode_request(FIXTURE.read_text())


@pytest.mark.parametrize("text", ['{"version":1,"version":1}', "version: 1\nversion: 1"])
def test_duplicate_keys_are_rejected(text):
    with pytest.raises(ValueError):
        decode_request(text)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(version=2),
        lambda r: r.update(unknown=True),
        lambda r: r.update(target="../escape"),
        lambda r: r["companions"][0]["declaration"]["workflow"].pop("execution_profile"),
        lambda r: r["companions"][0]["sections"].update(Instructions=""),
        lambda r: r["companions"][0]["declaration"]["workflow"].update(invented=True),
        lambda r: r["companions"][0]["sections"].update(Instructions="Read {undeclared_file}"),
    ],
)
def test_invalid_intent_has_no_writes(tmp_path, mutate):
    request = deepcopy(pair())
    mutate(request)
    result = prepare(request, root=tmp_path)
    assert result.status == "rejected"
    assert result.diagnostics
    assert not (tmp_path / ".cafe").exists()


def test_preview_is_deterministic_and_canonical(tmp_path):
    first = prepare(pair(), root=tmp_path)
    assert first.status == "ready", first.to_dict()
    assert first.to_dict() == prepare(pair(), root=tmp_path).to_dict()
    assert not (tmp_path / ".cafe").exists()
    skill = first.files[".cafe/skills/cafe-observe/SKILL.md"]
    assert "Read your agent file: {agent_file}" in skill
    for section in ("Role", "Instructions", "Output", "Handoff"):
        assert f"## {section}" in skill


def test_public_schema_projects_authoritative_runtime_models():
    from cafe.authoring import request_schema
    from cafe.core.playbook import PlaybookDefinition
    from cafe.skills.contracts import SkillWorkflowDeclaration

    schema = request_schema()
    assert schema["playbook"] == PlaybookDefinition.model_json_schema()
    assert schema["phase_workflow"] == SkillWorkflowDeclaration.model_json_schema()

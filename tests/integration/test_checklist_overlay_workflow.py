"""Production journeys for independently selected checklist overlays."""

from pathlib import Path

import pytest
import yaml

from cafe.playbooks.loader import PlaybookLoader


def write_skill(root, name, workflow=None, references=None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "description": name, "workflow": workflow or {}}
    (directory / "SKILL.md").write_text("---\n" + yaml.safe_dump(data) + "---\n# Policy\n")
    for filename, content in (references or {}).items():
        target = directory / "references" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return directory


def overlay(reference="review.md", **extra):
    return {"checklist_overlay": {"variants": [{"sections": [{"reference": reference}]}], **extra}}


def test_author_validates_inactive_policy_and_primary_alternatives(tmp_path):
    """I01/U02/U04: strict public loading validates project overrides and all branches."""
    builtin = tmp_path / "builtin"
    project = tmp_path / "project"
    for name in ("first", "later"):
        write_skill(builtin / "skills", name)
    write_skill(builtin / "skills", "policy")
    policy = overlay(when={"feedback": True})
    policy["required_tools"] = ["Write"]
    policy["prompt_inputs"] = [{"artifacts": ["notes"], "placeholder": "notes_file", "required": True}]
    directory = write_skill(project / ".cafe/skills", "policy", policy, {"review.md": "[ ] Review\n"})
    playbook = {
        "playbook": {"id": "overlay", "applicability": {"summary": "test", "use_when": ["test"], "avoid_when": ["other"]}},
        "roles": {"operator": {}},
        "commands": {"prepare": {"prompt_for_spec_plan_config": False}},
        "skills": {"workflow": {"shared": ["policy"]}, "chat": {"shared": []}},
        "steps": {"assemble": {"role": "operator", "skill": {"1": "first", "default": "later"}, "input_artifacts": ["notes"], "allowed_tools": ["Read", "Write"], "on": {"await_agent": "_done"}}},
    }
    folder = builtin / "playbooks"
    folder.mkdir(parents=True)
    path = folder / "overlay.yaml"
    loader = PlaybookLoader(project_root=project, global_root=tmp_path / "global", builtin_root=builtin)
    def validate():
        path.write_text(yaml.safe_dump(playbook))
        return loader.load_model("overlay", strict=True)
    validate()
    playbook["steps"]["assemble"]["allowed_tools"] = ["Read"]
    with pytest.raises(ValueError, match="Write"):
        validate()
    playbook["steps"]["assemble"]["allowed_tools"].append("Write")
    playbook["steps"]["assemble"]["input_artifacts"] = []
    with pytest.raises(ValueError, match="notes_file"):
        validate()
    playbook["steps"]["assemble"]["input_artifacts"] = ["notes"]
    (directory / "references/review.md").unlink()
    with pytest.raises(ValueError) as error:
        validate()
    assert all(token in str(error.value) for token in ("assemble", "policy", "checklist_overlay", "review.md"))
    (directory / "references/review.md").write_text("[ ] Review\n")
    write_skill(builtin / "skills", "later", {"checklist": {"variants": [{"sections": [{"reference": "missing.md"}]}]}})
    with pytest.raises(ValueError, match="missing.md"):
        validate()

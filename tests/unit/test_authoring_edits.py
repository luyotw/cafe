"""U3/I2: stable structural operations and byte preservation."""

import pytest

from cafe.authoring.requests import Operation
from cafe.authoring.source_edits import edit_yaml


def op(path, value, **kwargs):
    return Operation(op="upsert", path=path, value=value, **kwargs)


@pytest.mark.parametrize(
    "path,value,key",
    [
        (
            ["workflow", "prompt_inputs"],
            {"placeholder": "record_file", "artifacts": ["record"]},
            "placeholder",
        ),
        (["workflow", "human_tasks"], {"id": "review", "pattern": "confirm_output"}, "id"),
        (
            ["steps", "inspect", "human_tasks"],
            {"task_id": "review", "outcomes": {"agree": "_done"}},
            "task_id",
        ),
        (["steps", "inspect", "allowed_goto"], "inspect", None),
        (["workflow", "required_tools"], "Read", None),
        (["steps", "inspect", "input_artifacts"], "record", None),
        (["skills", "workflow", "shared"], "cafe-proof", None),
    ],
)
def test_keyed_additions_preserve_other_source_and_repeat(path, value, key):
    from cafe.authoring.source_edits import dump

    root = {}
    cursor = root
    for part in path[:-1]:
        cursor[part] = {}
        cursor = cursor[part]
    cursor[path[-1]] = []
    source = "# Manual heading\n" + dump(root) + "untouched: yes  # keep prose\n"
    changed = edit_yaml(source, op(path, value, key=key))
    assert changed.startswith("# Manual heading\n")
    assert changed.endswith("untouched: yes  # keep prose\n")
    assert edit_yaml(changed, op(path, value, key=key)) == changed


def test_conflicting_identity_requires_explicit_old_value():
    source = "tools:\n- id: observation\n  required: true\n"
    with pytest.raises(ValueError):
        edit_yaml(source, op(["tools"], {"id": "observation", "required": False}, key="id"))
    replace = Operation(
        op="replace",
        path=["tools"],
        value=[],
        expected=[{"id": "observation", "required": True}],
        overwrite=True,
    )
    assert edit_yaml(edit_yaml(source, replace), replace) == edit_yaml(source, replace)


@pytest.mark.parametrize(
    "source", ["tools: [Read]\n", "tools: &a\n- Read\n", "tools:\n- Read # retained\n"]
)
def test_ambiguous_container_replacements_fail_closed(source):
    with pytest.raises(ValueError):
        edit_yaml(
            source,
            Operation(op="replace", path=["tools"], value=[], expected=["Read"], overwrite=True),
        )


def test_append_preserves_unambiguous_member_comments():
    source = "tools:\n- Read # observe only\n# Owner footer\n"
    operation = op(["tools"], "Write")
    changed = edit_yaml(source, operation)
    assert "- Read # observe only\n" in changed
    assert changed.endswith("# Owner footer\n")
    assert edit_yaml(changed, operation) == changed

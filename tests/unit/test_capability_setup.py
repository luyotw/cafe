"""Capability setup is driven by declarations, not phase names or Driver defaults."""

from pathlib import Path

import pytest

from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
from cafe.core.capability_setup import resolve_setup_choices
from cafe.core.playbook import PlaybookDefinition
from cafe.playbooks.loader import PlaybookLoader


def _graph(capabilities=(), *, step="draft"):
    return PlaybookDefinition.model_validate(
        {
            "playbook": {"id": "minimal", "name": "Minimal", "conversation_locale": "en"},
            "steps": {
                step: {
                    "skill": "cafe-draft",
                    "role": "writer",
                    "on": {"await_agent": "_done"},
                    "capability_requests": list(capabilities),
                }
            },
        }
    )


def test_arbitrary_capability_supplies_its_own_question_and_prepare_arguments(tmp_path):
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))
    manifest = registry["cafe.pr.publish"].model_dump(mode="json")
    manifest.update(
        id="example.catalog",
        setup_questions=[
            {
                "setting": "catalog.mode",
                "prompt": "Catalog destination",
                "choices": [
                    {
                        "value": "preview",
                        "outcome": "Write a local preview",
                        "prepare_args": ["--catalog-mode", "preview"],
                    }
                ],
            }
        ],
    )
    custom = type(registry["cafe.pr.publish"]).model_validate(manifest)
    resolved = resolve_setup_choices(
        _graph([custom.id], step="pr"), {custom.id: custom}, ['catalog.mode="preview"']
    )
    assert [(q.prompt, c.value, c.prepare_args) for q, c in resolved] == [
        ("Catalog destination", "preview", ("--catalog-mode", "preview"))
    ]


@pytest.mark.parametrize(
    "answer",
    ["pr.auto_create=1", 'pr.auto_create="true"', "pr.auto_create=null", "pr.auto_create=yes"],
)
def test_capability_choice_does_not_coerce_authority(tmp_path, answer):
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))
    with pytest.raises(ValueError, match="capability-choice"):
        resolve_setup_choices(_graph(["cafe.pr.publish"]), registry, [answer])


def test_duplicate_missing_unknown_answers_and_capabilities_fail_closed(tmp_path):
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))
    graph = _graph(["cafe.pr.publish"])
    for answers in ([], ["unknown.flag=false"], ["pr.auto_create=false"] * 2):
        with pytest.raises(ValueError):
            resolve_setup_choices(graph, registry, answers)
    with pytest.raises(ValueError, match="unknown declared capability"):
        resolve_setup_choices(_graph(["unknown"]), registry, [])
    assert resolve_setup_choices(_graph(step="pr"), registry, []) == []


def test_every_builtin_graph_resolves_without_requiring_a_publication_capability(tmp_path):
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))
    for path in (Path(__file__).parents[2] / "src/cafe/data/playbooks").glob("*.yaml"):
        graph = PlaybookLoader(project_root=tmp_path).load_model(path.stem).model
        requested = {cap for step in graph.steps.values() for cap in step.capability_requests}
        answers = [f"{q.setting}=false" for cap in requested for q in registry[cap].setup_questions]
        resolved = resolve_setup_choices(graph, registry, answers)
        assert len(resolved) == len(answers)

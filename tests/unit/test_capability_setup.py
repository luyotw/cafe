"""Capability setup is driven by declarations, not phase names or Driver defaults."""

from pathlib import Path
from threading import Event, Thread

import pytest
import yaml

from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
from cafe.core.capability_setup import (
    _load_effective_playbook,
    resolve_setup_choices,
    update_pr_auto_create,
)
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


def test_pr_auto_create_update_is_targeted_and_preview_is_read_only(tmp_path, monkeypatch):
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    config_path = issue_dir / "issue.yaml"
    issue_dir.mkdir(parents=True)
    config_path.write_text(
        "playbook_id: direct-subagent-review\npr:\n  auto_create: true\n  post_todo_list: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cafe.utils.issue_config._registered_worktree_paths", lambda _root: (tmp_path,)
    )

    preview = update_pr_auto_create(config_path=config_path, value=False, preview=True)
    assert preview.status == "proposed"
    assert "auto_create: true" in config_path.read_text(encoding="utf-8")
    assert not config_path.with_name("issue-settings.lock").exists()

    saved = update_pr_auto_create(config_path=config_path, value=False)
    loaded = __import__("yaml").safe_load(config_path.read_text(encoding="utf-8"))
    assert saved.status == "saved"
    assert loaded["pr"] == {"auto_create": False, "post_todo_list": False}

    before = config_path.read_bytes()
    unchanged = update_pr_auto_create(config_path=config_path, value=False)
    assert unchanged.status == "unchanged"
    assert config_path.read_bytes() == before


def test_pr_auto_create_updates_linked_worktree_authority_not_inventory(tmp_path, monkeypatch):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    inventory = main / ".cafe" / "issues" / "demo" / "issue.yaml"
    authority = linked / ".cafe" / "issues" / "demo" / "issue.yaml"
    inventory.parent.mkdir(parents=True)
    authority.parent.mkdir(parents=True)
    inventory.write_text(f"issue_name: demo\nworktree_path: {linked}\n", encoding="utf-8")
    authority.write_text(
        "playbook_id: direct-subagent-review\npr:\n  auto_create: true\n",
        encoding="utf-8",
    )
    before_inventory = inventory.read_bytes()
    monkeypatch.setattr(
        "cafe.utils.issue_config._registered_worktree_paths", lambda _root: (main, linked)
    )

    result = update_pr_auto_create(config_path=inventory, value=False)

    assert result.config_path == authority
    assert yaml.safe_load(authority.read_text(encoding="utf-8"))["pr"]["auto_create"] is False
    assert inventory.read_bytes() == before_inventory


def test_pr_auto_create_adds_missing_choice_and_rejects_malformed_owner_data(tmp_path, monkeypatch):
    config_path = tmp_path / ".cafe" / "issues" / "demo" / "issue.yaml"
    config_path.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "cafe.utils.issue_config._registered_worktree_paths", lambda _root: (tmp_path,)
    )
    config_path.write_text("playbook_id: direct-subagent-review\n", encoding="utf-8")
    assert update_pr_auto_create(config_path=config_path, value=True).status == "saved"

    config_path.write_text("playbook_id: direct-subagent-review\npr: invalid\n", encoding="utf-8")
    before = config_path.read_bytes()
    with pytest.raises(ValueError, match="pr must be a mapping"):
        update_pr_auto_create(config_path=config_path, value=False)
    assert config_path.read_bytes() == before

    config_path.write_text(
        "playbook_id: direct-subagent-review\npr:\n  auto_create: null\n",
        encoding="utf-8",
    )
    before = config_path.read_bytes()
    with pytest.raises(ValueError, match="existing pr.auto_create"):
        update_pr_auto_create(config_path=config_path, value=False)
    assert config_path.read_bytes() == before


def test_effective_playbook_uses_canonical_project_before_global_and_builtin(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("builtin", "global", "canonical", "active")]
    for root in roots:
        root.mkdir()
    (roots[0] / "minimal.yaml").write_text(
        yaml.safe_dump(
            _graph(["cafe.pr.publish"]).model_dump(mode="json", by_alias=True, exclude_none=True)
        ),
        encoding="utf-8",
    )
    (roots[2] / "minimal.yaml").write_text(
        yaml.safe_dump(_graph().model_dump(mode="json", by_alias=True, exclude_none=True)),
        encoding="utf-8",
    )

    class Resolver:
        def __init__(self, **_kwargs):
            pass

        def catalog_roots(self, _kind):
            return [
                ("builtin", roots[0], None),
                ("global", roots[1], None),
                ("project", roots[2], "canonical"),
                ("project", roots[3], "active"),
            ]

    monkeypatch.setattr("cafe.core.capability_setup.CatalogResolver", Resolver)
    model = _load_effective_playbook(
        {"playbook_id": "minimal"},
        tmp_path / ".cafe" / "issues" / "demo" / "issue.yaml",
    )
    assert all(not step.capability_requests for step in model.steps.values())


def test_pr_auto_create_serializes_cooperating_writers(tmp_path, monkeypatch):
    config_path = tmp_path / ".cafe" / "issues" / "demo" / "issue.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "playbook_id: direct-subagent-review\npr:\n  auto_create: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cafe.utils.issue_config._registered_worktree_paths", lambda _root: (tmp_path,)
    )
    first_entered = Event()
    release_first = Event()
    second_started = Event()
    second_done = Event()
    import cafe.core.capability_setup as setup_module

    original = setup_module.write_issue_config_atomic
    writes = []

    def controlled_write(path, config):
        writes.append(config["pr"]["auto_create"])
        if len(writes) == 1:
            first_entered.set()
            assert release_first.wait(2)
        original(path, config)

    monkeypatch.setattr(setup_module, "write_issue_config_atomic", controlled_write)
    first = Thread(target=lambda: update_pr_auto_create(config_path=config_path, value=True))

    def second_update():
        second_started.set()
        update_pr_auto_create(config_path=config_path, value=False)
        second_done.set()

    second = Thread(target=second_update)
    first.start()
    assert first_entered.wait(2)
    second.start()
    assert second_started.wait(2)
    assert not second_done.wait(0.05)
    release_first.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert writes == [True, False]


def test_pr_auto_create_invokes_no_provider_publication_or_helper_sync(tmp_path, monkeypatch):
    from cafe.agents.manager import AgentManager
    from cafe.core.hooks.native import GitHubPRCreator
    from cafe.skills import global_installer

    config_path = tmp_path / ".cafe" / "issues" / "demo" / "issue.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("playbook_id: direct-subagent-review\n", encoding="utf-8")
    monkeypatch.setattr(
        "cafe.utils.issue_config._registered_worktree_paths", lambda _root: (tmp_path,)
    )
    calls = []
    monkeypatch.setattr(AgentManager, "execute", lambda *_args, **_kwargs: calls.append("provider"))
    monkeypatch.setattr(
        GitHubPRCreator, "run", lambda *_args, **_kwargs: calls.append("publication")
    )
    monkeypatch.setattr(
        global_installer,
        "auto_sync_global_skills",
        lambda *_args, **_kwargs: calls.append("helper-sync"),
    )

    update_pr_auto_create(config_path=config_path, value=False)

    assert calls == []


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_pr_auto_create_update_requires_exact_boolean(tmp_path, value):
    config_path = tmp_path / ".cafe" / "issues" / "demo" / "issue.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("playbook_id: direct-subagent-review\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Boolean"):
        update_pr_auto_create(config_path=config_path, value=value)

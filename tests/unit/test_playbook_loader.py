"""Tests for playbook schema, loader, and semantic validation."""

from pathlib import Path

import pytest

from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import resolve_step_human_task


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc-{name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _write_playbook(root: Path, name: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.yaml").write_text(content, encoding="utf-8")


def test_playbook_rejects_human_task_outcome_outside_declared_steps(tmp_path: Path) -> None:
    """A task cannot nominate a continuation absent from its own playbook."""
    builtin_root = tmp_path / "builtin"
    skill_dir = builtin_root / "skills" / "reviewer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: reviewer
description: reviewer
workflow:
  human_tasks:
    - id: review-output
      pattern: confirm_output
      prompt: Review output
      input_schema: decision
      decisions:
        - id: confirm
          label: Approve
---
""",
        encoding="utf-8",
    )
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-human-task",
        """
playbook: {id: invalid-human-task}
steps:
  review:
    role: reviewer
    skill: reviewer
    human_tasks:
      - trigger: confirm_output
        task_id: review-output
        outcomes: {confirm: unknown}
    on: {confirm_output: review, await_agent: _done}
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="human task outcome"):
        loader.load_model("invalid-human-task")


def test_load_uses_project_override(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    global_root = tmp_path / "global"
    project_root = tmp_path / "project"

    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook: {id: default}
steps:
  spec:
    role: pm
    skill: spec_first
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )
    _write_playbook(
        global_root / "playbooks",
        "default",
        """
playbook: {id: default}
steps:
  spec:
    role: developer
    skill: spec_first
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "default",
        """
playbook: {id: default}
roles:
  reviewer:
    description: review
steps:
  spec:
    role: reviewer
    skill: spec_first
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=global_root,
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")
    assert result.source == "project"
    assert result.model.steps["spec"].role == "reviewer"


def test_entry_step_initial_input_accepts_declared_providers_and_bindings(tmp_path: Path) -> None:
    """A non-development entry step owns its declared initial-input contract."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "intake-flow",
        """
playbook: {id: intake-flow}
entry_point: intake
commands:
  prepare:
    prompt_for_spec_plan_config: false
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [manual_text, github_issue]
      bind:
        artifact: intake_brief
        prompt_context: user_input
    hooks:
      prepare_input: [InitialInputProviderResolver]
    on: {await_agent: _done}
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    model = loader.load_model("intake-flow").model

    assert model.steps["intake"].initial_input.providers == ["manual_text", "github_issue"]
    assert model.steps["intake"].initial_input.bind.artifact == "intake_brief"
    assert model.steps["intake"].initial_input.bind.prompt_context == "user_input"


@pytest.mark.parametrize(
    ("initial_input", "error_token"),
    [
        (
            """
    initial_input:
      providers: [manual_text]
      bind: {artifact: unrelated}
""",
            "bind.artifact",
        ),
        (
            """
    initial_input:
      providers: [manual_text, manual_text]
      bind: {artifact: intake_brief}
""",
            "duplicates",
        ),
        (
            """
    initial_input:
      providers: [url]
      bind: {artifact: intake_brief}
""",
            "unsupported provider",
        ),
    ],
)
def test_initial_input_rejects_invalid_provider_or_binding_before_execution(
    tmp_path: Path, initial_input: str, error_token: str
) -> None:
    """U2 — invalid entry declarations fail with their actionable field token."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-input",
        f"""
playbook: {{id: invalid-input}}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
{initial_input}    on: {{await_agent: _done}}
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match=error_token):
        loader.load_model("invalid-input")


def test_initial_input_rejects_non_entry_or_unimplemented_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U3 — declarations are entry-only and must map to a host provider."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-placement",
        """
playbook: {id: invalid-placement}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    on: {await_agent: _done}
  refinement:
    role: pm
    skill: intake
    output_artifact: refined_brief
    initial_input:
      providers: [manual_text]
      bind: {artifact: refined_brief}
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="only allowed on entry_point"):
        loader.load_model("invalid-placement")

    _write_playbook(
        builtin_root / "playbooks",
        "unimplemented-provider",
        """
playbook: {id: unimplemented-provider}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [github_issue]
      bind: {artifact: intake_brief}
    on: {await_agent: _done}
""",
    )
    monkeypatch.setattr(
        "cafe.core.playbook.registered_initial_input_providers", lambda: frozenset()
    )

    with pytest.raises(ValueError, match="no trusted host implementation"):
        loader.load_model("unimplemented-provider")


@pytest.mark.parametrize("source", ["project", "global"])
def test_initial_input_rejects_legacy_presentation_outside_bundled_playbooks(
    tmp_path: Path, source: str
) -> None:
    """U2/I4 — custom playbooks cannot opt into the built-in empty-input compatibility path."""
    builtin_root = tmp_path / "builtin"
    global_root = tmp_path / "global"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "intake")
    playbook_root = (
        project_root / ".cafe" / "playbooks"
        if source == "project"
        else global_root / "playbooks"
    )
    _write_playbook(
        playbook_root,
        "intake-flow",
        """
playbook: {id: intake-flow}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [manual_text]
      bind: {artifact: intake_brief}
      legacy_presentation: true
    hooks:
      prepare_input: [InitialInputProviderResolver]
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=global_root,
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="legacy_presentation"):
        loader.load_model("intake-flow")


@pytest.mark.parametrize("playbook_name", ["default", "simple", "tdd"])
def test_builtin_entry_steps_use_declared_initial_input_resolver(playbook_name: str) -> None:
    """I3 — built-in development flows retain the provider contract."""
    model = PlaybookLoader().load_model(playbook_name).model
    entry = model.steps[model.entry_point]

    assert entry.initial_input.providers == ["manual_text", "github_issue"]
    assert entry.initial_input.bind.artifact == entry.output_artifact
    assert entry.initial_input.legacy_presentation is True
    assert "InitialInputProviderResolver" in entry.hooks.prepare_input
    assert "GitHubIssueFetcher" not in entry.hooks.prepare_input


def test_initial_input_declaration_requires_generic_resolver_hook(tmp_path: Path) -> None:
    """I4 — a declared provider cannot silently bypass trusted delivery."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "missing-resolver",
        """
playbook: {id: missing-resolver}
entry_point: intake
commands:
  prepare:
    prompt_for_spec_plan_config: false
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [manual_text]
      bind: {artifact: intake_brief}
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(builtin_root=builtin_root)

    with pytest.raises(ValueError, match="InitialInputProviderResolver"):
        loader.load_model("missing-resolver")


def test_playbook_conversation_locale_supports_bcp47_and_defaults_to_auto(
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "localized",
        """
playbook: {id: localized, conversation_locale: zh-TW}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )
    _write_playbook(
        builtin_root / "playbooks",
        "legacy",
        """
playbook: {id: legacy}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    assert loader.load_model("localized").model.playbook.conversation_locale == "zh-TW"
    assert loader.load_model("legacy").model.playbook.conversation_locale == "auto"


def test_playbook_conversation_locale_rejects_non_bcp47_value(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-conversation-locale",
        """
playbook: {id: invalid-conversation-locale, conversation_locale: zh_TW}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="playbook.conversation_locale"):
        loader.load_model("invalid-conversation-locale")


def test_playbook_rejects_ambiguous_locale_field(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "legacy-locale",
        """
playbook: {id: legacy-locale, locale: en-US}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="locale"):
        loader.load_model("legacy-locale")


def test_load_supports_iteration_aware_skill_and_defaults(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_skill(builtin_root / "skills", "spec_revise")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
roles:
  pm:
    description: product
steps:
  spec:
    skill:
      1: spec_first
      default: spec_revise
    role: pm
    valid_intents: [confirmed]
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")

    assert result.model.entry_point == "spec"
    assert result.model.steps["spec"].type == "skill"
    assert result.model.steps["spec"].assignee_type == "agent"
    assert result.model.steps["spec"].auto_snapshot is True
    assert result.model.steps["spec"].skill == {
        "1": "spec_first",
        "default": "spec_revise",
    }


def test_load_supports_step_handoff_label_and_chat_role(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-brief_first")
    _write_playbook(
        builtin_root / "playbooks",
        "editorial",
        """
playbook:
  id: editorial
roles:
  editor:
    description: editor
  writer:
    description: writer
steps:
  brief:
    skill: cafe-brief_first
    role: editor
    handoff_label: Refine editorial brief
    chat_role: writer
    valid_intents: [confirmed]
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("editorial")

    assert result.model.steps["brief"].handoff_label == "Refine editorial brief"
    assert result.model.steps["brief"].chat_role == "writer"


def test_load_supports_alignment_config(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  develop:
    skill: cafe-develop
    role: developer
    alignment:
      trigger_policy: policy
      pause_threshold: 5
      note_threshold: 2
      affected_document_categories: [roadmap, positioning]
      reuse_approved: true
    on:
      await_agent: _done
      alignment_checkpoint: develop
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    result = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load_model("default")

    alignment = result.model.steps["develop"].alignment
    assert alignment is not None
    assert alignment.pause_threshold == 5
    assert alignment.affected_document_categories == ["roadmap", "positioning"]


def test_load_supports_capability_requests(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "pr")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  pr:
    skill: pr
    role: developer
    capability_requests: [cafe.pr.publish]
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    result = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load_model("default")

    assert result.model.steps["pr"].capability_requests == ["cafe.pr.publish"]


def test_load_rejects_duplicate_capability_requests(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "pr")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  pr:
    skill: pr
    role: developer
    capability_requests: [cafe.pr.publish, cafe.pr.publish]
    on:
      await_agent: _done
""",
    )

    with pytest.raises(ValueError, match="duplicate capability_requests"):
        PlaybookLoader(
            project_root=tmp_path / "project",
            global_root=tmp_path / "global",
            builtin_root=builtin_root,
        ).load_model("default")


def test_load_rejects_unknown_alignment_config_key(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  develop:
    skill: cafe-develop
    role: developer
    alignment:
      unknown: true
    on:
      await_agent: _done
""",
    )

    with pytest.raises(ValueError, match="unknown"):
        PlaybookLoader(
            project_root=tmp_path / "project",
            global_root=tmp_path / "global",
            builtin_root=builtin_root,
        ).load_model("default")


def test_load_rejects_unknown_step_chat_role(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-brief_first")
    _write_playbook(
        builtin_root / "playbooks",
        "editorial",
        """
playbook:
  id: editorial
roles:
  editor:
    description: editor
steps:
  brief:
    skill: cafe-brief_first
    role: editor
    chat_role: writer
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="unknown chat_role"):
        loader.load_model("editorial")


def test_load_supports_dictionary_script_hook_declarations(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-plan")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
roles:
  developer:
    description: dev
steps:
  plan:
    skill: cafe-plan
    role: developer
    hooks:
      after_execute:
        - script: sync_github.sh
          when_intents: [confirmed]
          args:
            phase: plan
            output: "{output_file}"
          schema:
            type: object
            required: [phase, output]
            additionalProperties: false
            properties:
              phase:
                type: string
                enum: [spec, plan]
              output:
                type: string
    valid_intents: [ready_for_review, confirmed]
    on:
      confirm_output: plan
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")

    hook_entry = result.model.steps["plan"].hooks.after_execute[0]
    assert isinstance(hook_entry, dict)
    assert hook_entry["script"] == "sync_github.sh"


def test_load_rejects_script_hook_dict_in_unsupported_stage(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-pr")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
roles:
  developer:
    description: dev
steps:
  pr:
    skill: cafe-pr
    role: developer
    hooks:
      publish_output:
        - script: sync_pr.sh
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="unsupported stage 'publish_output'"):
        loader.load_model("default")


def test_load_missing_skill_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  spec:
    role: pm
    skill: missing_skill
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="unknown skill"):
        loader.load("bad")


def test_load_invalid_allowed_goto_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  develop:
    role: developer
    skill: cafe-develop
    valid_intents: [confirmed]
    allowed_goto: [review]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="invalid allowed_goto target"):
        loader.load("bad")


def test_load_invalid_transition_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-review")
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  review:
    role: reviewer
    skill: cafe-review
    valid_intents: [confirmed]
    on:
      await_agent: not_exist
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="invalid transition"):
        loader.load("bad")


def test_custom_playbook_reports_redundant_tool_warning(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  develop:
    role: developer
    skill: cafe-develop
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("custom")

    assert any("redundant allowed_tools entry" in warning for warning in result.warnings)


def test_strict_mode_upgrades_custom_warning_to_error(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  develop:
    role: developer
    skill: cafe-develop
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="redundant allowed_tools entry"):
        loader.load_model("custom", strict=True)


def test_builtin_catalog_includes_hotfix_and_simple() -> None:
    loader = PlaybookLoader()

    playbooks = loader.list_playbooks()

    assert "default" in playbooks
    assert "hotfix" in playbooks
    assert "simple" in playbooks
    assert "editorial" in playbooks
    assert "research" in playbooks
    assert "incident" in playbooks


def test_builtin_playbooks_declare_en_us_conversation_locale() -> None:
    loader = PlaybookLoader()

    for playbook_id in (
        "default",
        "simple",
        "tdd",
        "hotfix",
        "editorial",
        "incident",
        "research",
    ):
        assert loader.load_model(playbook_id).model.playbook.conversation_locale == "en-US"


def test_builtin_hotfix_and_simple_playbooks_load() -> None:
    loader = PlaybookLoader()

    hotfix = loader.load_model("hotfix").model
    tdd = loader.load_model("tdd").model
    simple = loader.load_model("simple").model

    assert hotfix.entry_point == "develop"
    assert list(hotfix.steps.keys()) == ["develop", "review", "pr"]
    assert hotfix.steps["review"].max_iterations == 1
    assert hotfix.steps["develop"].input_artifacts == ["review_feedback", "pr_result"]
    assert tdd.steps["develop"].input_artifacts == [
        "spec",
        "plan",
        "review_feedback",
        "pr_result",
    ]

    assert simple.entry_point == "spec"
    assert list(simple.steps.keys()) == ["spec", "develop", "pr"]
    assert simple.steps["develop"].on["await_agent"] == "pr"


def test_legacy_playbook_omits_input_artifact_scope_after_loading(tmp_path: Path) -> None:
    """An omitted field remains distinguishable from explicit input_artifacts: []."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "legacy",
        """
playbook: {id: legacy}
steps:
  draft:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loaded = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load("legacy")

    assert "input_artifacts" not in loaded["steps"]["draft"]


def test_playbook_rejects_explicit_null_input_artifact_scope(tmp_path: Path) -> None:
    """Explicit null must not bypass an isolated artifact scope."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-null-scope",
        """
playbook: {id: invalid-null-scope}
steps:
  draft:
    role: pm
    skill: spec_first
    input_artifacts: null
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="input_artifacts must be a list when specified"):
        loader.load("invalid-null-scope")


def test_builtin_non_software_playbooks_define_non_default_handoff_metadata() -> None:
    loader = PlaybookLoader()

    research = loader.load_model("research").model
    editorial = loader.load_model("editorial").model

    assert research.steps["question"].handoff_label == "Refine research question"
    assert research.steps["question"].chat_role == "researcher"
    assert editorial.steps["draft"].handoff_label == "Continue manuscript draft"
    assert editorial.steps["draft"].chat_role == "writer"
    assert "implementation" not in (research.steps["question"].handoff_label or "").lower()
    assert "requirements" not in (editorial.steps["draft"].handoff_label or "").lower()


def test_builtin_user_handoffs_resolve_nonempty_declared_policies() -> None:
    """Builtin user pauses must not fall back to implicit development behavior."""
    loader = PlaybookLoader()
    triggers = {"confirm_output", "need_clarification", "no_changes_needed"}

    for playbook_id in ("default", "simple", "tdd", "hotfix", "editorial", "incident", "research"):
        playbook = loader.load(playbook_id)
        for step_name, step in playbook["steps"].items():
            for trigger in triggers.intersection(step.get("on", {})):
                policy, binding = resolve_step_human_task(
                    playbook_data=playbook,
                    step_name=step_name,
                    trigger=trigger,
                )

                assert policy.prompt
                assert binding.task_id == policy.id

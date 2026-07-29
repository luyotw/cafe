"""Tests for prepare field renderer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.playbook import PlaybookDefinition, PrepareConfig, resolve_prepare_config
from cafe.core.prepare_fields import (
    ParsedPrepareFields,
    PrepareField,
    PrepareFieldChoice,
    parse_prepare_fields,
)
from cafe.core.prepare_profile import PrepareProfile, PrepareRigorError
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.prepare_field_renderer import (
    NonInteractiveCliAnswers,
    NonInteractiveResolverDeps,
    PrepareNonInteractiveContext,
    PrepareNonInteractiveError,
    PrepareNonInteractiveRequiredFieldError,
    PrepareNonInteractiveTemplateError,
    PreparePromptContext,
    RendererDeps,
    apply_quick_defaults,
    field_is_visible,
    field_is_visible_for_non_interactive,
    format_enum_choice,
    parse_enum_selection,
    prompt_setup_mode,
    prompt_custom_fields,
    resolve_non_interactive_issue_config,
    set_write_value,
    validate_non_interactive_required,
    visible_fields,
)
from cafe.templates.manager import TemplateManager
from cafe.core.prepare_profile import PrepareIssueConfig


def _profile(*, is_github_repo: bool = True) -> PrepareProfile:
    loader = PlaybookLoader()
    loaded = loader.load_model("default")
    return PrepareProfile.from_playbook(loaded.model, is_github_repo=is_github_repo)


def _no_pr_profile(*, is_github_repo: bool = True) -> PrepareProfile:
    model = PlaybookDefinition.model_validate(
        {
            "playbook": {"id": "no-pr"},
            "steps": {
                "build": {
                    "type": "skill",
                    "skill": "cafe-develop",
                    "role": "developer",
                    "on": {"await_agent": "_done"},
                }
            },
        }
    )
    return PrepareProfile.from_playbook(model, is_github_repo=is_github_repo)


def _default_fields():
    loader = PlaybookLoader()
    loaded = loader.load_model("default")
    profile = _profile()
    return profile.resolved_prepare_fields(
        playbook_path=loaded.path,
        skill_loader=SkillLoader(),
    )


class TestShowWhenVisibility:
    def test_github_repo_gate(self) -> None:
        field = PrepareField.model_validate(
            {
                "id": "input_method",
                "type": "enum",
                "label": "Input method",
                "write": "spec.input_method",
                "show_when": {"github_repo": True},
                "choices": [{"value": "manual", "label": "Manual"}],
            }
        )
        assert field_is_visible(
            field,
            PreparePromptContext(True, None, None, _profile()),
        )
        assert not field_is_visible(
            field,
            PreparePromptContext(False, None, None, _profile(is_github_repo=False)),
        )

    def test_issue_id_and_setup_mode_gates(self) -> None:
        field = PrepareField.model_validate(
            {
                "id": "sync",
                "type": "boolean",
                "label": "Sync",
                "write": "spec.sync_github",
                "show_when": {"issue_id_present": True, "setup_mode": "custom"},
            }
        )
        visible_ctx = PreparePromptContext(True, 123, "custom", _profile())
        hidden_ctx = PreparePromptContext(True, None, "custom", _profile())
        assert field_is_visible(field, visible_ctx)
        assert not field_is_visible(field, hidden_ctx)


class TestVisibleFieldsOrdering:
    def test_preserves_declaration_order(self) -> None:
        parsed = ParsedPrepareFields(
            fields=parse_prepare_fields(
                [
                    {"id": "a", "type": "boolean", "label": "A", "write": "spec.sync_github"},
                    {
                        "id": "b",
                        "type": "boolean",
                        "label": "B",
                        "write": "plan.sync_github",
                        "show_when": {"setup_mode": "quick"},
                    },
                ]
            )
        )
        ctx = PreparePromptContext(True, None, "quick", _profile())
        ids = [field.id for field in visible_fields(parsed, ctx)]
        assert ids == ["a", "b"]


class TestWriteTargetMapping:
    def test_maps_nested_issue_config_paths(self) -> None:
        config = PrepareIssueConfig(spec={}, plan={}, pr={})
        set_write_value(config, "spec.rigor", "high")
        set_write_value(config, "plan.template", "default")
        set_write_value(config, "pr.auto_create", True)
        assert config.spec["rigor"] == "high"
        assert config.plan["template"] == "default"
        assert config.pr["auto_create"] is True

    def test_maps_custom_step_template_selection(self) -> None:
        config = PrepareIssueConfig(spec={}, plan={}, pr={})

        set_write_value(config, "synthesis.template", "evidence")

        assert config.steps == {"synthesis": {"template": "evidence"}}


def test_non_interactive_prepare_persists_custom_template_field_default() -> None:
    """A declared custom catalog default is retained outside the spec/plan aliases."""
    field_defs = [
        {
            "id": "synthesis_template",
            "type": "template",
            "label": "Synthesis template",
            "write": "synthesis.template",
            "default": "evidence",
        }
    ]
    profile = PrepareProfile(
        prepare=PrepareConfig.model_validate({"fields": field_defs}),
        is_github_repo=True,
        step_names=frozenset({"synthesis"}),
    )
    parsed = ParsedPrepareFields(fields=parse_prepare_fields(field_defs))
    synthesis_manager = MagicMock()
    synthesis_manager.template_exists.return_value = True
    deps = NonInteractiveResolverDeps(
        spec_template_manager=MagicMock(),
        plan_template_manager=MagicMock(),
        template_managers={"synthesis": synthesis_manager},
    )

    config = resolve_non_interactive_issue_config(
        profile,
        NonInteractiveCliAnswers(input_method="manual"),
        parsed_fields=parsed,
        deps=deps,
    )

    assert config.steps == {"synthesis": {"template": "evidence"}}


def test_non_interactive_custom_template_default_survives_legacy_defaults() -> None:
    """Legacy spec/plan defaults do not discard declarative custom defaults."""
    field_defs = [
        {
            "id": "synthesis_template",
            "type": "template",
            "label": "Synthesis template",
            "write": "synthesis.template",
            "default": "evidence",
        }
    ]
    profile = PrepareProfile(
        prepare=PrepareConfig.model_validate(
            {
                "fields": field_defs,
                "non_interactive_defaults": {
                    "rigor": "medium",
                    "spec_template": "auto",
                    "plan_template": "auto",
                },
            }
        ),
        is_github_repo=True,
        step_names=frozenset({"synthesis"}),
    )
    parsed = ParsedPrepareFields(fields=parse_prepare_fields(field_defs))
    synthesis_manager = MagicMock()
    synthesis_manager.template_exists.return_value = True
    deps = NonInteractiveResolverDeps(
        spec_template_manager=MagicMock(),
        plan_template_manager=MagicMock(),
        template_managers={"synthesis": synthesis_manager},
    )

    config = resolve_non_interactive_issue_config(
        profile,
        NonInteractiveCliAnswers(input_method="manual"),
        parsed_fields=parsed,
        deps=deps,
    )

    assert config.steps == {"synthesis": {"template": "evidence"}}


class TestQuickDefaults:
    def test_matches_profile_quick_setup_for_github_issue(self) -> None:
        parsed = _default_fields()
        assert parsed is not None
        profile = _profile()
        ctx = PreparePromptContext(True, 337, None, profile)
        rendered = apply_quick_defaults(parsed, ctx)
        legacy = profile.quick_setup_issue_config(337)
        assert rendered.spec == legacy.spec
        assert rendered.plan == legacy.plan
        assert rendered.pr == legacy.pr

    def test_manual_input_uses_manual_sync_defaults(self) -> None:
        parsed = _default_fields()
        assert parsed is not None
        profile = _profile()
        ctx = PreparePromptContext(True, None, None, profile)
        rendered = apply_quick_defaults(parsed, ctx)
        assert rendered.spec["sync_github"] is False
        assert rendered.plan["sync_github"] is False


class TestEnumFormatting:
    def test_formats_label_and_description(self) -> None:
        choice = PrepareFieldChoice(value="high", label="High", description="Precise mode")
        assert format_enum_choice(choice) == "High\n   Precise mode"

    def test_parses_formatted_selection_back_to_value(self) -> None:
        choices = [
            PrepareFieldChoice(value="low", label="Low"),
            PrepareFieldChoice(value="high", label="High", description="Precise mode"),
        ]
        assert parse_enum_selection("High\n   Precise mode", choices) == "high"


class TestSetupModePrompt:
    def test_uses_declarative_default_choice(self) -> None:
        field = PrepareField.model_validate(
            {
                "id": "setup_mode",
                "type": "setup_mode",
                "label": "Mode",
                "default": "custom",
                "choices": [
                    {"value": "quick", "label": "Quick setup"},
                    {"value": "custom", "label": "Custom setup"},
                ],
            }
        )
        prompt_list = MagicMock(return_value="Custom setup")
        deps = RendererDeps(
            prompt_list=prompt_list,
            prompt_confirm=MagicMock(),
            prompt_text=MagicMock(),
            prompt_for_input_method=MagicMock(),
            select_template=MagicMock(),
            spec_template_manager=MagicMock(),
            plan_template_manager=MagicMock(),
        )

        assert prompt_setup_mode(field, deps) == "custom"
        assert prompt_list.call_args.kwargs["default"] == "Custom setup"


class TestPostTodoListVisibility:
    def test_hidden_when_auto_create_false(self) -> None:
        field = PrepareField.model_validate(
            {
                "id": "todo",
                "type": "boolean",
                "label": "Todo",
                "write": "pr.post_todo_list",
                "show_when": {"setup_mode": "custom", "github_repo": True},
            }
        )
        ctx = PreparePromptContext(True, None, "custom", _profile(), pr_auto_create=False)
        assert not field_is_visible(field, ctx)


def _resolver_deps() -> NonInteractiveResolverDeps:
    return NonInteractiveResolverDeps(
        spec_template_manager=TemplateManager(template_type="spec"),
        plan_template_manager=TemplateManager(template_type="plan"),
    )


class TestNonInteractiveContext:
    def test_cli_answers_optional_overrides(self) -> None:
        answers = NonInteractiveCliAnswers(
            input_method="manual",
            rigor="high",
            spec_template="auto",
            plan_template="default",
        )
        assert answers.input_method == "manual"
        assert answers.rigor == "high"

    def test_required_input_method_validation(self) -> None:
        with pytest.raises(PrepareNonInteractiveRequiredFieldError):
            validate_non_interactive_required(NonInteractiveCliAnswers())

    def test_github_mode_requires_issue_id(self) -> None:
        with pytest.raises(PrepareNonInteractiveRequiredFieldError):
            validate_non_interactive_required(
                NonInteractiveCliAnswers(input_method="github")
            )


class TestNonInteractiveVisibility:
    def test_ignores_setup_mode_gate(self) -> None:
        field = PrepareField.model_validate(
            {
                "id": "quick_rigor",
                "type": "enum",
                "label": "Rigor",
                "write": "spec.rigor",
                "show_when": {"setup_mode": "quick"},
                "choices": [{"value": "medium", "label": "Medium"}],
            }
        )
        ctx = PrepareNonInteractiveContext(True, None, _profile())
        assert field_is_visible_for_non_interactive(field, ctx)

    def test_github_repo_gate_still_applies(self) -> None:
        field = PrepareField.model_validate(
            {
                "id": "pr_auto",
                "type": "boolean",
                "label": "Auto PR",
                "write": "pr.auto_create",
                "show_when": {"github_repo": True},
            }
        )
        assert field_is_visible_for_non_interactive(
            field, PrepareNonInteractiveContext(True, None, _profile())
        )
        assert not field_is_visible_for_non_interactive(
            field, PrepareNonInteractiveContext(False, None, _profile(is_github_repo=False))
        )


class TestNonInteractiveResolver:
    def test_default_playbook_manual_defaults(self) -> None:
        parsed = _default_fields()
        profile = _profile()
        config = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(input_method="manual"),
            parsed_fields=parsed,
            deps=_resolver_deps(),
        )
        assert config.spec == {
            "input_method": "manual",
            "rigor": "medium",
            "template": "auto",
        }
        assert config.plan == {"template": "default"}
        assert config.pr == {}

    def test_cli_override_precedence(self) -> None:
        parsed = _default_fields()
        profile = _profile()
        config = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(
                input_method="manual",
                rigor="low",
                spec_template="detailed",
                plan_template="bug",
            ),
            parsed_fields=parsed,
            deps=_resolver_deps(),
        )
        assert config.spec["rigor"] == "low"
        assert config.spec["template"] == "detailed"
        assert config.plan["template"] == "bug"

    def test_legacy_playbook_without_fields(self) -> None:
        loader = PlaybookLoader()
        loaded = loader.load_model("simple")
        profile = PrepareProfile.from_playbook(loaded.model, is_github_repo=True)
        config = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(input_method="manual"),
            parsed_fields=None,
            deps=_resolver_deps(),
        )
        defaults = profile.non_interactive_defaults()
        assert config.spec["rigor"] == defaults.rigor
        assert config.spec["template"] == defaults.spec_template
        assert config.plan["template"] == defaults.plan_template

    def test_declarative_fields_supply_defaults_when_legacy_defaults_absent(self) -> None:
        field_defs = [
            {
                "id": "custom_rigor",
                "type": "enum",
                "label": "Rigor",
                "write": "spec.rigor",
                "default": "low",
                "show_when": {"setup_mode": "custom"},
                "choices": [
                    {"value": "low", "label": "Low"},
                    {"value": "medium", "label": "Medium"},
                    {"value": "high", "label": "High"},
                ],
            },
            {
                "id": "custom_spec_template",
                "type": "template",
                "label": "Spec template",
                "write": "spec.template",
                "default": "detailed",
                "show_when": {"setup_mode": "custom"},
            },
            {
                "id": "custom_plan_template",
                "type": "template",
                "label": "Plan template",
                "write": "plan.template",
                "default": "bug",
                "show_when": {"setup_mode": "custom"},
            },
        ]
        profile = PrepareProfile(
            prepare=PrepareConfig.model_validate({"fields": field_defs}),
            is_github_repo=True,
        )
        parsed = ParsedPrepareFields(fields=parse_prepare_fields(field_defs))

        config = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(input_method="manual"),
            parsed_fields=parsed,
            deps=_resolver_deps(),
        )

        assert config.spec["rigor"] == "low"
        assert config.spec["template"] == "detailed"
        assert config.plan["template"] == "bug"

    def test_github_mode_writes_issue_id(self) -> None:
        parsed = _default_fields()
        profile = _profile()
        config = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(input_method="github", issue_id=336),
            parsed_fields=parsed,
            deps=_resolver_deps(),
        )
        assert config.spec["input_method"] == "github"
        assert config.spec["issue_id"] == "336"

    def test_invalid_rigor_blocked(self, tmp_path: Path, monkeypatch) -> None:
        from tests.conftest import create_minimal_config
        from tests.integration.test_prepare_playbook_driven import (
            _write_config_with_playbook,
            _write_custom_playbook,
        )

        create_minimal_config(tmp_path)
        prepare_block = """
commands:
  prepare:
    quick_setup:
      spec:
        rigor: high
    non_interactive_defaults:
      rigor: high
    constraints:
      rigor: [high]
"""
        _write_custom_playbook(tmp_path, "strict", prepare_block)
        _write_config_with_playbook(tmp_path, "strict")
        monkeypatch.chdir(tmp_path)
        loader = PlaybookLoader()
        strict_profile = PrepareProfile.from_playbook(
            loader.load_model("strict").model,
            is_github_repo=True,
        )
        with pytest.raises(PrepareRigorError):
            resolve_non_interactive_issue_config(
                strict_profile,
                NonInteractiveCliAnswers(input_method="manual", rigor="low"),
                parsed_fields=None,
                deps=_resolver_deps(),
            )

    def test_invalid_plan_template_blocked(self) -> None:
        profile = _profile()
        with pytest.raises(PrepareNonInteractiveTemplateError):
            resolve_non_interactive_issue_config(
                profile,
                NonInteractiveCliAnswers(
                    input_method="manual",
                    plan_template="does-not-exist",
                ),
                parsed_fields=_default_fields(),
                deps=_resolver_deps(),
            )

    def test_pr_flags_only_when_explicit(self) -> None:
        profile = _profile()
        without_pr = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(input_method="manual"),
            parsed_fields=_default_fields(),
            deps=_resolver_deps(),
        )
        assert without_pr.pr == {}

        with_pr = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(
                input_method="manual",
                auto_create_pr=True,
                post_pr_todo_list=False,
            ),
            parsed_fields=_default_fields(),
            deps=_resolver_deps(),
        )
        assert with_pr.pr == {"auto_create": True, "post_todo_list": False}

    def test_pr_flags_rejected_when_playbook_has_no_pr_config(self) -> None:
        profile = _no_pr_profile()

        with pytest.raises(PrepareNonInteractiveError, match="require a playbook"):
            resolve_non_interactive_issue_config(
                profile,
                NonInteractiveCliAnswers(
                    input_method="manual",
                    auto_create_pr=True,
                    post_pr_todo_list=True,
                ),
                parsed_fields=None,
                deps=_resolver_deps(),
            )

    def test_pr_flags_allowed_when_no_pr_step_declares_pr_fields(self) -> None:
        profile = _no_pr_profile()
        parsed = ParsedPrepareFields(
            fields=parse_prepare_fields(
                [
                    {
                        "id": "auto",
                        "type": "boolean",
                        "label": "Auto PR",
                        "write": "pr.auto_create",
                    },
                    {
                        "id": "todo",
                        "type": "boolean",
                        "label": "Todo",
                        "write": "pr.post_todo_list",
                    },
                ]
            )
        )

        config = resolve_non_interactive_issue_config(
            profile,
            NonInteractiveCliAnswers(
                input_method="manual",
                auto_create_pr=True,
                post_pr_todo_list=False,
            ),
            parsed_fields=parsed,
            deps=_resolver_deps(),
        )

        assert config.pr == {"auto_create": True, "post_todo_list": False}


class TestPromptCustomFields:
    def test_skips_post_todo_when_auto_create_disabled(self) -> None:
        parsed = ParsedPrepareFields(
            fields=parse_prepare_fields(
                [
                    {
                        "id": "auto",
                        "type": "boolean",
                        "label": "Auto PR",
                        "write": "pr.auto_create",
                        "show_when": {"setup_mode": "custom", "github_repo": True},
                    },
                    {
                        "id": "todo",
                        "type": "boolean",
                        "label": "Todo",
                        "write": "pr.post_todo_list",
                        "show_when": {"setup_mode": "custom", "github_repo": True},
                        "default": True,
                    },
                ]
            )
        )
        confirm = MagicMock(side_effect=[False])
        deps = RendererDeps(
            prompt_list=MagicMock(),
            prompt_confirm=confirm,
            prompt_text=MagicMock(),
            prompt_for_input_method=MagicMock(),
            select_template=MagicMock(),
            spec_template_manager=MagicMock(),
            plan_template_manager=MagicMock(),
        )
        ctx = PreparePromptContext(True, None, "custom", _profile())
        config = prompt_custom_fields(parsed, ctx, deps=deps)
        assert config.pr["auto_create"] is False
        assert "post_todo_list" not in config.pr
        assert confirm.call_count == 1

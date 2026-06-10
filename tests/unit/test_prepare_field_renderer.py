"""Tests for prepare field renderer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.playbook import PlaybookDefinition, resolve_prepare_config
from cafe.core.prepare_fields import (
    ParsedPrepareFields,
    PrepareField,
    PrepareFieldChoice,
    parse_prepare_fields,
)
from cafe.core.prepare_profile import PrepareProfile
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.prepare_field_renderer import (
    PreparePromptContext,
    RendererDeps,
    apply_quick_defaults,
    field_is_visible,
    format_enum_choice,
    parse_enum_selection,
    prompt_setup_mode,
    prompt_custom_fields,
    set_write_value,
    visible_fields,
)
from cafe.core.prepare_profile import PrepareIssueConfig


def _profile(*, is_github_repo: bool = True) -> PrepareProfile:
    loader = PlaybookLoader()
    loaded = loader.load_model("default")
    return PrepareProfile.from_playbook(loaded.model, is_github_repo=is_github_repo)


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

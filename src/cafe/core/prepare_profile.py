"""Playbook-driven decision layer for ``cafe prepare``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cafe.core.playbook import PlaybookDefinition, PrepareConfig, resolve_prepare_config
from cafe.core.prepare_fields import ParsedPrepareFields, resolve_prepare_fields
from cafe.skills.loader import SkillLoader


class PrepareRigorError(ValueError):
    """Raised when rigor is outside playbook ``constraints.rigor``."""


@dataclass(frozen=True)
class PrepareIssueConfig:
    """Resolved spec/plan/pr blocks for ``issue.yaml``."""

    spec: Dict[str, Any]
    plan: Dict[str, Any]
    pr: Dict[str, Any]


@dataclass(frozen=True)
class NonInteractiveDefaults:
    """Defaults applied when optional prepare flags are omitted."""

    rigor: str
    spec_template: str
    plan_template: str


@dataclass(frozen=True)
class PrepareProfile:
    """Effective prepare behavior from playbook metadata and repo context."""

    prepare: PrepareConfig
    is_github_repo: bool
    step_names: frozenset[str] = frozenset()

    @classmethod
    def from_playbook(cls, model: PlaybookDefinition, is_github_repo: bool) -> PrepareProfile:
        return cls(
            prepare=resolve_prepare_config(model),
            is_github_repo=is_github_repo,
            step_names=frozenset(model.steps),
        )

    def supports_pr_config(
        self,
        parsed_fields: Optional[ParsedPrepareFields] = None,
    ) -> bool:
        """Return whether prepare may write PR-related issue config."""
        if "pr" in self.step_names:
            return True
        if parsed_fields is None:
            return False
        return any((field.write or "").startswith("pr.") for field in parsed_fields.fields)

    def should_prompt_spec_plan_config(self, base_should_prompt: bool) -> bool:
        return base_should_prompt and self.prepare.prompt_for_spec_plan_config

    def enabled_setup_mode_labels(self) -> list[str]:
        labels: list[str] = []
        if self.prepare.setup_modes.quick.enabled:
            labels.append(self.prepare.setup_modes.quick.label)
        if self.prepare.setup_modes.custom.enabled:
            labels.append(self.prepare.setup_modes.custom.label)
        return labels

    def is_quick_setup_choice(self, choice: str) -> bool:
        return choice == self.prepare.setup_modes.quick.label

    def quick_setup_issue_config(self, issue_id: Optional[int]) -> PrepareIssueConfig:
        quick = self.prepare.quick_setup
        spec: Dict[str, Any] = {
            "rigor": quick.spec.rigor,
            "template": quick.spec.template,
        }
        plan: Dict[str, Any] = {"template": quick.plan.template}
        pr: Dict[str, Any] = {}

        sync = quick.sync_github
        has_issue_id = issue_id is not None
        spec["sync_github"] = sync.when_issue_id_present if has_issue_id else sync.when_manual_input
        plan["sync_github"] = sync.when_issue_id_present if has_issue_id else sync.when_manual_input

        if self.supports_pr_config() and self.is_github_repo and quick.pr.auto_create_on_github_repo:
            pr["auto_create"] = True
            if quick.pr.post_todo_list_when_auto_create:
                pr["post_todo_list"] = True
        elif self.supports_pr_config():
            pr["auto_create"] = False

        return PrepareIssueConfig(spec=spec, plan=plan, pr=pr)

    def non_interactive_defaults(self) -> NonInteractiveDefaults:
        defaults = self.prepare.non_interactive_defaults
        return NonInteractiveDefaults(
            rigor=defaults.rigor,
            spec_template=defaults.spec_template,
            plan_template=defaults.plan_template,
        )

    def validate_rigor(self, rigor: str) -> None:
        allowed = set(self.prepare.constraints.rigor)
        if rigor not in allowed:
            allowed_display = ", ".join(f"'{level}'" for level in self.prepare.constraints.rigor)
            raise PrepareRigorError(
                f"--rigor must be one of: {allowed_display}"
            )

    def allowed_rigor_values(self) -> list[str]:
        return list(self.prepare.constraints.rigor)

    def should_prompt_input_method(self) -> bool:
        return self.is_github_repo and self.prepare.input_method.prompt_on_github_repo

    def default_input_method(self) -> str:
        return self.prepare.input_method.non_github_default

    def resolved_prepare_fields(
        self,
        *,
        playbook_path: Path,
        skill_loader: SkillLoader,
    ) -> Optional[ParsedPrepareFields]:
        """Return declarative prepare fields for this playbook, if declared."""
        return resolve_prepare_fields(
            self.prepare,
            playbook_path=playbook_path,
            skill_loader=skill_loader,
        )

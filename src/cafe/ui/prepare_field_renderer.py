"""Render interactive cafe prepare prompts from PrepareField definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Literal, Optional, Tuple

from cafe.core.initial_input import normalize_initial_input_provider
from cafe.core.prepare_fields import ParsedPrepareFields, PrepareField, PrepareFieldChoice
from cafe.core.prepare_profile import PrepareIssueConfig, PrepareProfile, PrepareRigorError
from cafe.templates.manager import TemplateManager

SetupMode = Literal["quick", "custom"]

_ALLOWED_INPUT_METHODS = frozenset({"manual", "github"})


class PrepareNonInteractiveError(ValueError):
    """Raised when non-interactive prepare answers fail validation."""


class PrepareNonInteractiveRequiredFieldError(PrepareNonInteractiveError):
    """Raised when a required non-interactive flag is missing or invalid."""


class PrepareNonInteractiveTemplateError(PrepareNonInteractiveError):
    """Raised when a template name does not resolve to an existing template."""

    def __init__(
        self,
        template_kind: str,
        template_name: str,
        available: List[Tuple[str, str]],
    ) -> None:
        self.template_kind = template_kind
        self.template_name = template_name
        self.available = available
        super().__init__(f"{template_kind} template {template_name!r} not found")


@dataclass
class PrepareNonInteractiveContext:
    """Runtime context for non-interactive prepare field visibility."""

    is_github_repo: bool
    issue_id: Optional[int]
    profile: PrepareProfile


@dataclass
class NonInteractiveCliAnswers:
    """CLI flags for ``cafe prepare --no-interactive``."""

    input_method: Optional[str] = None
    issue_id: Optional[int] = None
    rigor: Optional[str] = None
    spec_template: Optional[str] = None
    plan_template: Optional[str] = None
    sync_spec_github: Optional[bool] = None
    sync_plan_github: Optional[bool] = None
    auto_create_pr: Optional[bool] = None
    post_pr_todo_list: Optional[bool] = None


@dataclass(frozen=True)
class NonInteractiveResolverDeps:
    """Template managers used to validate non-interactive template answers."""

    spec_template_manager: TemplateManager
    plan_template_manager: TemplateManager
    template_managers: dict[str, TemplateManager] = field(default_factory=dict)


@dataclass
class PreparePromptContext:
    """Runtime context for evaluating prepare field visibility and defaults."""

    is_github_repo: bool
    issue_id: Optional[int]
    setup_mode: Optional[SetupMode]
    profile: PrepareProfile
    pr_auto_create: Optional[bool] = None


@dataclass
class RendererDeps:
    """Host-provided prompt and template dependencies."""

    prompt_list: Callable[..., str]
    prompt_confirm: Callable[..., bool]
    prompt_text: Callable[..., str]
    prompt_for_input_method: Callable[..., tuple[str, Optional[int]]]
    select_template: Callable[..., Optional[str]]
    spec_template_manager: TemplateManager
    plan_template_manager: TemplateManager
    template_managers: dict[str, TemplateManager] = field(default_factory=dict)
    console: Any = None
    display: Any = None
    github_ops: Any = None


def field_is_visible(field: PrepareField, ctx: PreparePromptContext) -> bool:
    """Return whether one field should be shown for the current context."""
    show_when = field.show_when
    if show_when is None:
        return True
    if show_when.github_repo is not None and show_when.github_repo != ctx.is_github_repo:
        return False
    if show_when.issue_id_present is not None:
        has_issue = ctx.issue_id is not None
        if show_when.issue_id_present != has_issue:
            return False
    if show_when.setup_mode is not None:
        if ctx.setup_mode is None or show_when.setup_mode != ctx.setup_mode:
            return False
    if (
        field.write == "pr.post_todo_list"
        and ctx.setup_mode == "custom"
        and ctx.pr_auto_create is not True
    ):
        return False
    return True


def visible_fields(parsed: ParsedPrepareFields, ctx: PreparePromptContext) -> List[PrepareField]:
    """Return visible fields in declaration order."""
    return [field for field in parsed.fields if field_is_visible(field, ctx)]


def field_is_visible_for_non_interactive(
    field: PrepareField,
    ctx: PrepareNonInteractiveContext,
) -> bool:
    """Return whether one field applies in non-interactive mode."""
    if field.type == "setup_mode" or not field.non_interactive:
        return False
    show_when = field.show_when
    if show_when is None:
        return True
    if show_when.github_repo is not None and show_when.github_repo != ctx.is_github_repo:
        return False
    if show_when.issue_id_present is not None:
        has_issue = ctx.issue_id is not None
        if show_when.issue_id_present != has_issue:
            return False
    return True


def visible_fields_for_non_interactive(
    parsed: ParsedPrepareFields,
    ctx: PrepareNonInteractiveContext,
) -> List[PrepareField]:
    """Return fields visible for non-interactive default resolution."""
    return [field for field in parsed.fields if field_is_visible_for_non_interactive(field, ctx)]


def validate_non_interactive_required(answers: NonInteractiveCliAnswers) -> None:
    """Validate required CLI flags before non-interactive prepare continues."""
    if answers.input_method is None:
        raise PrepareNonInteractiveRequiredFieldError(
            "--input-method is required in non-interactive mode"
        )
    if answers.input_method not in _ALLOWED_INPUT_METHODS:
        raise PrepareNonInteractiveRequiredFieldError("--input-method must be 'manual' or 'github'")
    if answers.input_method == "github" and answers.issue_id is None:
        raise PrepareNonInteractiveRequiredFieldError(
            "--issue-id is required when using --input-method=github"
        )


def _validate_template_name(
    manager: TemplateManager,
    template_kind: str,
    template_name: Optional[str],
) -> None:
    if not template_name or template_name == "auto":
        return
    if not manager.template_exists(template_name):
        raise PrepareNonInteractiveTemplateError(
            template_kind,
            template_name,
            manager.list_templates(),
        )


def _validate_field_enum_value(
    field: PrepareField,
    value: Any,
    *,
    profile: PrepareProfile,
) -> None:
    if field.type != "enum":
        return
    allowed = {choice.value for choice in field.choices}
    if field.write == "spec.rigor":
        allowed &= set(profile.allowed_rigor_values())
    if value not in allowed:
        raise PrepareNonInteractiveError(f"invalid value {value!r} for prepare field {field.id!r}")


def validate_non_interactive_config(
    profile: PrepareProfile,
    answers: NonInteractiveCliAnswers,
    *,
    rigor: str,
    spec_template: Optional[str],
    plan_template: Optional[str],
    parsed_fields: Optional[ParsedPrepareFields],
    deps: NonInteractiveResolverDeps,
) -> None:
    """Validate resolved non-interactive answers before writing issue.yaml."""
    profile.validate_rigor(rigor)

    if parsed_fields is not None:
        ctx = PrepareNonInteractiveContext(
            is_github_repo=profile.is_github_repo,
            issue_id=answers.issue_id if answers.input_method == "github" else None,
            profile=profile,
        )
        value_by_write = {
            "spec.rigor": rigor,
            "spec.template": spec_template,
            "plan.template": plan_template,
            "spec.input_method": answers.input_method,
        }
        if answers.sync_spec_github is not None:
            value_by_write["spec.sync_github"] = answers.sync_spec_github
        if answers.sync_plan_github is not None:
            value_by_write["plan.sync_github"] = answers.sync_plan_github
        for field in visible_fields_for_non_interactive(parsed_fields, ctx):
            if field.write is None or field.write not in value_by_write:
                continue
            value = value_by_write[field.write]
            if value is not None:
                _validate_field_enum_value(field, value, profile=profile)

    _validate_template_name(deps.spec_template_manager, "Spec", spec_template)
    _validate_template_name(deps.plan_template_manager, "Plan", plan_template)


def _cli_value_for_field(
    field: PrepareField,
    answers: NonInteractiveCliAnswers,
) -> tuple[bool, Any]:
    """Return an explicitly supplied CLI value for one declared field."""
    if field.id == "input_method":
        return answers.input_method is not None, answers.input_method
    if field.id == "github_issue_id" or field.normalize == "github_issue":
        return answers.issue_id is not None, answers.issue_id
    values = {
        "spec.input_method": answers.input_method,
        "spec.issue_id": answers.issue_id,
        "spec.rigor": answers.rigor,
        "spec.template": answers.spec_template,
        "plan.template": answers.plan_template,
        "spec.sync_github": answers.sync_spec_github,
        "plan.sync_github": answers.sync_plan_github,
        "pr.post_todo_list": answers.post_pr_todo_list,
    }
    if field.write == "pr.auto_create":
        return answers.auto_create_pr is not None, answers.auto_create_pr
    value = values.get(field.write)
    return value is not None, value


def _field_default_for_non_interactive(field: PrepareField) -> Any:
    """Return the authoritative default for a declared field."""
    if field.non_interactive_default is not None:
        return field.non_interactive_default
    return field.default


def _template_manager_for_field(
    field: PrepareField,
    deps: NonInteractiveResolverDeps,
) -> TemplateManager:
    """Resolve the template catalog declared by a template field's destination."""
    section = field.write.split(".", 1)[0] if field.write else "plan"
    if section == "spec":
        return deps.spec_template_manager
    if section == "plan":
        return deps.plan_template_manager
    manager = deps.template_managers.get(section)
    if manager is None:
        raise PrepareNonInteractiveError(
            f"prepare field {field.id!r} targets undeclared template step {section!r}"
        )
    return manager


def _validate_declared_non_interactive_input(
    parsed_fields: ParsedPrepareFields,
    answers: NonInteractiveCliAnswers,
) -> None:
    """Require input flags only when a declared input field asks for them."""
    input_fields = [field for field in parsed_fields.fields if field.id == "input_method"]
    if not input_fields:
        return
    if answers.input_method is not None and answers.input_method not in _ALLOWED_INPUT_METHODS:
        raise PrepareNonInteractiveRequiredFieldError("--input-method must be 'manual' or 'github'")
    if any(field.required for field in input_fields) and answers.input_method is None:
        raise PrepareNonInteractiveRequiredFieldError(
            "--input-method is required by the declared prepare field"
        )
    if answers.input_method == "github" and answers.issue_id is None:
        raise PrepareNonInteractiveRequiredFieldError(
            "--issue-id is required when using --input-method=github"
        )


def resolve_non_interactive_issue_config(
    profile: PrepareProfile,
    answers: NonInteractiveCliAnswers,
    *,
    parsed_fields: Optional[ParsedPrepareFields] = None,
    deps: NonInteractiveResolverDeps,
) -> PrepareIssueConfig:
    """Resolve and validate non-interactive prepare config for issue.yaml."""
    if parsed_fields is None:
        if not profile.prepare.prompt_for_spec_plan_config and profile.prepare.model_fields_set == {
            "prompt_for_spec_plan_config"
        }:
            if answers.auto_create_pr is not None or answers.post_pr_todo_list is not None:
                raise PrepareNonInteractiveError(
                    "--auto-create-pr and --post-pr-todo-list require a playbook with a "
                    "pr step or explicit pr.* prepare fields"
                )
            return empty_issue_config()
        validate_non_interactive_required(answers)
        defaults = profile.non_interactive_defaults()
        rigor = answers.rigor if answers.rigor is not None else defaults.rigor
        spec_template = (
            answers.spec_template if answers.spec_template is not None else defaults.spec_template
        )
        plan_template = (
            answers.plan_template if answers.plan_template is not None else defaults.plan_template
        )
        validate_non_interactive_config(
            profile,
            answers,
            rigor=rigor,
            spec_template=spec_template,
            plan_template=plan_template,
            parsed_fields=None,
            deps=deps,
        )
        config = empty_issue_config()
        set_write_value(config, "spec.input_method", answers.input_method)
        if answers.input_method == "github" and answers.issue_id is not None:
            set_write_value(config, "spec.issue_id", str(answers.issue_id))
        set_write_value(config, "spec.rigor", rigor)
        if spec_template:
            set_write_value(config, "spec.template", spec_template)
        if plan_template:
            set_write_value(config, "plan.template", plan_template)
        if answers.sync_spec_github is not None:
            set_write_value(config, "spec.sync_github", answers.sync_spec_github)
        if answers.sync_plan_github is not None:
            set_write_value(config, "plan.sync_github", answers.sync_plan_github)
        if not profile.supports_pr_config() and (
            answers.auto_create_pr is not None or answers.post_pr_todo_list is not None
        ):
            raise PrepareNonInteractiveError(
                "--auto-create-pr and --post-pr-todo-list require a playbook with a "
                "pr step or explicit pr.* prepare fields"
            )
        if (
            profile.supports_pr_config()
            and profile.is_github_repo
            and answers.auto_create_pr is not None
        ):
            set_write_value(config, "pr.auto_create", answers.auto_create_pr)
        if profile.supports_pr_config() and answers.post_pr_todo_list is not None:
            set_write_value(config, "pr.post_todo_list", answers.post_pr_todo_list)
        return config

    _validate_declared_non_interactive_input(parsed_fields, answers)
    ctx = PrepareNonInteractiveContext(
        is_github_repo=profile.is_github_repo,
        issue_id=answers.issue_id if answers.input_method == "github" else None,
        profile=profile,
    )
    config = empty_issue_config()
    for field in parsed_fields.fields:
        if field.type == "setup_mode" or field.write is None:
            continue
        is_explicit, value = _cli_value_for_field(field, answers)
        if not field_is_visible_for_non_interactive(field, ctx) and not is_explicit:
            continue
        if not is_explicit:
            value = _field_default_for_non_interactive(field)
        if value is None:
            if field.required:
                raise PrepareNonInteractiveRequiredFieldError(
                    f"prepare field {field.id!r} requires a value in non-interactive mode"
                )
            continue
        if field.id == "github_issue_id" or field.normalize == "github_issue":
            value = str(value)
        _validate_field_enum_value(field, value, profile=profile)
        if field.type == "template":
            _validate_template_name(
                _template_manager_for_field(field, deps), field.label, str(value)
            )
        section, key = field.write.split(".", 1)
        if key in config.section(section) and not is_explicit:
            continue
        set_write_value(config, field.write, value)

    if answers.input_method is not None:
        provider = normalize_initial_input_provider(answers.input_method)
        if provider is not None:
            config.initial_input["provider"] = provider
            if provider == "github_issue" and answers.issue_id is not None:
                config.initial_input["issue_id"] = answers.issue_id

    if not profile.supports_pr_config(parsed_fields) and (
        answers.auto_create_pr is not None or answers.post_pr_todo_list is not None
    ):
        raise PrepareNonInteractiveError(
            "--auto-create-pr and --post-pr-todo-list require a playbook with a "
            "pr step or explicit pr.* prepare fields"
        )
    return config


def format_enum_choice(choice: PrepareFieldChoice) -> str:
    """Format one enum choice for prompt_list display."""
    if choice.description:
        return f"{choice.label}\n   {choice.description}"
    return choice.label


def parse_enum_selection(selection: str, choices: List[PrepareFieldChoice]) -> str:
    """Map a prompt_list selection back to the enum value."""
    for choice in choices:
        formatted = format_enum_choice(choice)
        if selection == formatted or selection.startswith(choice.label):
            return choice.value
    raise ValueError(f"unrecognized enum selection: {selection!r}")


def set_write_value(config: PrepareIssueConfig, write: str, value: Any) -> None:
    """Write one answer into a legacy or arbitrary declared step block."""
    section, key = write.split(".", 1)
    config.section(section)[key] = value


def empty_issue_config() -> PrepareIssueConfig:
    return PrepareIssueConfig(spec={}, plan={}, pr={})


def apply_quick_defaults(
    parsed: ParsedPrepareFields,
    ctx: PreparePromptContext,
) -> PrepareIssueConfig:
    """Apply quick-setup defaults from visible field definitions."""
    quick_ctx = PreparePromptContext(
        is_github_repo=ctx.is_github_repo,
        issue_id=ctx.issue_id,
        setup_mode="quick",
        profile=ctx.profile,
    )
    config = empty_issue_config()
    for field in visible_fields(parsed, quick_ctx):
        if field.type in {"setup_mode", "enum"} and field.write is None:
            continue
        if field.id == "input_method":
            continue
        if field.default is None:
            continue
        if field.write is None:
            continue
        set_write_value(config, field.write, field.default)

    if (
        ctx.profile.supports_pr_config(parsed)
        and ctx.is_github_repo
        and "auto_create" not in config.pr
    ):
        auto_field = next(
            (field for field in parsed.fields if field.write == "pr.auto_create"),
            None,
        )
        if auto_field is not None and auto_field.default is not None:
            set_write_value(config, "pr.auto_create", auto_field.default)
            if config.pr.get("auto_create") and "post_todo_list" not in config.pr:
                todo_field = next(
                    (field for field in parsed.fields if field.write == "pr.post_todo_list"),
                    None,
                )
                if todo_field is not None and todo_field.default is not None:
                    set_write_value(config, "pr.post_todo_list", todo_field.default)
    return config


def format_quick_summary(
    config: PrepareIssueConfig,
    parsed: ParsedPrepareFields,
    ctx: PreparePromptContext,
) -> List[str]:
    """Build human-readable quick-setup summary lines."""
    quick_ctx = PreparePromptContext(
        is_github_repo=ctx.is_github_repo,
        issue_id=ctx.issue_id,
        setup_mode="quick",
        profile=ctx.profile,
    )
    lines: List[str] = []
    for field in visible_fields(parsed, quick_ctx):
        if field.write is None or field.type == "setup_mode":
            continue
        section, key = field.write.split(".", 1)
        value = config.section(section).get(key)
        if value is None:
            continue
        lines.append(f"{field.label}: {value}")
    if ctx.issue_id is not None:
        lines.append(f"Input method: github")
    elif "input_method" in config.spec:
        lines.append(f"Input method: {config.spec['input_method']}")
    return lines


def _find_field(parsed: ParsedPrepareFields, field_id: str) -> Optional[PrepareField]:
    return next((field for field in parsed.fields if field.id == field_id), None)


def prompt_setup_mode(field: PrepareField, deps: RendererDeps) -> SetupMode:
    """Prompt for setup mode using declarative field choices."""
    labels = [choice.label for choice in field.choices]
    default = labels[0] if labels else None
    if field.default is not None:
        for choice in field.choices:
            if choice.value == field.default:
                default = choice.label
                break
    selected = deps.prompt_list(message=field.label, choices=labels, default=default)
    for choice in field.choices:
        if selected == choice.label:
            return "quick" if choice.value == "quick" else "custom"
    return "quick"


def _prompt_enum_field(field: PrepareField, ctx: PreparePromptContext, deps: RendererDeps) -> str:
    allowed = set(ctx.profile.allowed_rigor_values())
    choices = [
        choice for choice in field.choices if field.write != "spec.rigor" or choice.value in allowed
    ]
    if not choices:
        raise PrepareRigorError("no rigor choices available for this playbook")
    formatted = [format_enum_choice(choice) for choice in choices]
    default = formatted[0]
    if field.default is not None:
        for choice in choices:
            if choice.value == field.default:
                default = format_enum_choice(choice)
                break
    selected = deps.prompt_list(message=field.label, choices=formatted, default=default)
    value = parse_enum_selection(selected, choices)
    if field.write == "spec.rigor":
        ctx.profile.validate_rigor(value)
    return value


def _prompt_template_field(field: PrepareField, deps: RendererDeps) -> Optional[str]:
    step_name = field.write.split(".", 1)[0] if field.write else "plan"
    manager = deps.template_managers.get(step_name)
    if manager is None:
        manager = deps.spec_template_manager if step_name == "spec" else deps.plan_template_manager
    templates_with_source = manager.list_templates()
    template_names = [name for name, _ in templates_with_source]
    if not template_names:
        if deps.console is not None:
            deps.console.print()
            deps.console.print("[yellow]⚠️  No templates found. Using default template.[/yellow]")
        return str(field.default) if field.default is not None else None
    if deps.console is not None:
        deps.console.print()
        deps.console.print(f"[bold cyan]{field.label}[/bold cyan]")
    template_paths = {name: manager.get_template_path(name) for name in template_names}
    selected = deps.select_template(template_names, template_paths, templates_with_source)
    return selected or (str(field.default) if field.default is not None else None)


def prompt_custom_fields(
    parsed: ParsedPrepareFields,
    ctx: PreparePromptContext,
    *,
    deps: RendererDeps,
) -> PrepareIssueConfig:
    """Prompt for custom-setup fields in declaration order."""
    custom_ctx = PreparePromptContext(
        is_github_repo=ctx.is_github_repo,
        issue_id=ctx.issue_id,
        setup_mode=ctx.setup_mode,
        profile=ctx.profile,
        pr_auto_create=ctx.pr_auto_create,
    )
    config = empty_issue_config()
    for field in parsed.fields:
        if (
            field.type == "setup_mode"
            or field.id == "input_method"
            or field.normalize == "github_issue"
        ):
            continue
        if field.write is None:
            continue
        if not field_is_visible(field, custom_ctx):
            continue

        if field.type == "boolean":
            default = bool(field.default) if field.default is not None else False
            value = deps.prompt_confirm(field.label, default=default)
        elif field.type == "enum":
            value = _prompt_enum_field(field, custom_ctx, deps)
        elif field.type == "template":
            value = _prompt_template_field(field, deps)
            if value is None:
                continue
        elif field.type == "text":
            value = deps.prompt_text(field.label, default=str(field.default or ""))
            if not value and field.required:
                raise ValueError(f"prepare field {field.id!r} requires a value")
        else:
            continue

        set_write_value(config, field.write, value)
        if field.write == "pr.auto_create":
            custom_ctx.pr_auto_create = bool(value)
    return config


def run_field_driven_prepare_flow(
    parsed: ParsedPrepareFields,
    profile: PrepareProfile,
    *,
    deps: RendererDeps,
) -> tuple[PrepareIssueConfig, Optional[int]]:
    """Run the full interactive field-driven prepare configuration flow."""
    ctx = PreparePromptContext(
        is_github_repo=profile.is_github_repo,
        issue_id=None,
        setup_mode=None,
        profile=profile,
    )
    spec_config = empty_issue_config()
    issue_id: Optional[int] = None

    input_field = next(
        (field for field in parsed.fields if field.id == "input_method"), None
    )
    issue_field = next(
        (
            field
            for field in parsed.fields
            if field.id == "github_issue_id" or field.normalize == "github_issue"
        ),
        None,
    )
    if input_field is not None and field_is_visible(
        input_field,
        PreparePromptContext(
            is_github_repo=ctx.is_github_repo,
            issue_id=None,
            setup_mode=None,
            profile=profile,
        ),
    ):
        input_method, issue_id = deps.prompt_for_input_method(
            deps.display,
            deps.github_ops,
            field=input_field,
            issue_field=issue_field,
        )
        set_write_value(spec_config, input_field.write, input_method)
        if issue_id is not None:
            if issue_field is None or issue_field.write is None:
                raise ValueError(
                    "input_method field requires a github_issue field for GitHub input"
                )
            set_write_value(spec_config, issue_field.write, str(issue_id))
    elif input_field is not None and input_field.default is not None:
        set_write_value(spec_config, input_field.write, input_field.default)
        input_method = str(input_field.default)
    else:
        input_method = None
    provider = normalize_initial_input_provider(input_method)
    if provider is not None:
        spec_config.initial_input["provider"] = provider
        if provider == "github_issue" and issue_id is not None:
            spec_config.initial_input["issue_id"] = issue_id
    ctx.issue_id = issue_id

    setup_field = next((field for field in parsed.fields if field.type == "setup_mode"), None)
    if setup_field is None:
        custom_config = prompt_custom_fields(parsed, ctx, deps=deps)
        spec_config.spec.update(custom_config.spec)
        spec_config.plan.update(custom_config.plan)
        spec_config.pr.update(custom_config.pr)
        spec_config.steps.update(custom_config.steps)
        return spec_config, issue_id
    if deps.console is not None:
        deps.console.print()
    setup_mode = prompt_setup_mode(setup_field, deps)
    ctx.setup_mode = setup_mode

    if setup_mode == "quick":
        quick_config = apply_quick_defaults(parsed, ctx)
        spec_config.spec.update(quick_config.spec)
        spec_config.plan.update(quick_config.plan)
        spec_config.pr.update(quick_config.pr)
        spec_config.steps.update(quick_config.steps)
        if deps.console is not None:
            deps.console.print()
            deps.console.print("[green]✓ Quick setup applied with recommended defaults:[/green]")
            for line in format_quick_summary(spec_config, parsed, ctx):
                deps.console.print(f"  • {line}")
            deps.console.print()
        if not profile.is_github_repo and any(
            field.write == "pr.auto_create" for field in parsed.fields
        ):
            spec_config.pr["auto_create"] = False
        return spec_config, issue_id

    custom_config = prompt_custom_fields(parsed, ctx, deps=deps)
    spec_config.spec.update(custom_config.spec)
    spec_config.plan.update(custom_config.plan)
    spec_config.pr.update(custom_config.pr)
    spec_config.steps.update(custom_config.steps)
    if not profile.is_github_repo and any(
        field.write == "pr.auto_create" for field in parsed.fields
    ):
        spec_config.pr.setdefault("auto_create", False)
    return spec_config, issue_id

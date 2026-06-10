"""Render interactive cafe prepare prompts from PrepareField definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Literal, Optional

from cafe.core.prepare_fields import ParsedPrepareFields, PrepareField, PrepareFieldChoice
from cafe.core.prepare_profile import PrepareIssueConfig, PrepareProfile, PrepareRigorError
from cafe.templates.manager import TemplateManager

SetupMode = Literal["quick", "custom"]


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
    """Write one answer into spec/plan/pr blocks."""
    section, key = write.split(".", 1)
    target = {"spec": config.spec, "plan": config.plan, "pr": config.pr}[section]
    target[key] = value


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

    if ctx.is_github_repo and "auto_create" not in config.pr:
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
        value = {"spec": config.spec, "plan": config.plan, "pr": config.pr}[section].get(key)
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
        choice
        for choice in field.choices
        if field.write != "spec.rigor" or choice.value in allowed
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
    manager = (
        deps.spec_template_manager
        if field.write and field.write.startswith("spec.")
        else deps.plan_template_manager
    )
    templates_with_source = manager.list_templates()
    template_names = [name for name, _ in templates_with_source]
    if not template_names:
        if deps.console is not None:
            deps.console.print()
            deps.console.print(
                "[yellow]⚠️  No templates found. Using default template.[/yellow]"
            )
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
        setup_mode="custom",
        profile=ctx.profile,
        pr_auto_create=ctx.pr_auto_create,
    )
    config = empty_issue_config()
    for field in parsed.fields:
        if field.type == "setup_mode" or field.id == "input_method":
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

    input_field = _find_field(parsed, "input_method")
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
        )
        spec_config.spec["input_method"] = input_method
        if issue_id is not None:
            spec_config.spec["issue_id"] = str(issue_id)
    elif profile.should_prompt_input_method():
        input_method, issue_id = deps.prompt_for_input_method(deps.display, deps.github_ops)
        spec_config.spec["input_method"] = input_method
        if issue_id is not None:
            spec_config.spec["issue_id"] = str(issue_id)
    else:
        spec_config.spec["input_method"] = profile.default_input_method()

    ctx.issue_id = issue_id

    setup_field = _find_field(parsed, "setup_mode")
    if setup_field is None:
        raise ValueError("prepare fields document must declare setup_mode")
    if deps.console is not None:
        deps.console.print()
    setup_mode = prompt_setup_mode(setup_field, deps)
    ctx.setup_mode = setup_mode

    if setup_mode == "quick":
        quick_config = apply_quick_defaults(parsed, ctx)
        spec_config.spec.update(quick_config.spec)
        spec_config.plan.update(quick_config.plan)
        spec_config.pr.update(quick_config.pr)
        if deps.console is not None:
            deps.console.print()
            deps.console.print("[green]✓ Quick setup applied with recommended defaults:[/green]")
            for line in format_quick_summary(spec_config, parsed, ctx):
                deps.console.print(f"  • {line}")
            deps.console.print()
        return spec_config, issue_id

    custom_config = prompt_custom_fields(parsed, ctx, deps=deps)
    spec_config.spec.update(custom_config.spec)
    spec_config.plan.update(custom_config.plan)
    spec_config.pr.update(custom_config.pr)
    if not profile.is_github_repo:
        spec_config.pr.setdefault("auto_create", False)
    return spec_config, issue_id

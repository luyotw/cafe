#!/usr/bin/env python3
"""Render a structurally validated, provider-neutral CAFE kickoff contract."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

_MODEL_ADJUSTMENT_AUTHORITIES = {
    "driver_autonomous",
    "user_approval_required",
}
_DRIVER_MODES = {"attached", "unattended", "event-driven"}
_EVENT_DRIVEN_CLIS = {"claude", "codex", "gemini", "copilot", "cursor-agent"}


def _reexec_with_cafe_python() -> None:
    """Restart with the interpreter that owns the installed cafe command."""
    if os.environ.get("CAFE_KICKOFF_FORMATTER_REEXEC") == "1":
        raise RuntimeError("cafe's Python environment cannot import formatter dependencies")
    cafe_command = shutil.which("cafe")
    if cafe_command is None:
        raise RuntimeError("cafe command not found; cannot load formatter dependencies")
    first_line = Path(cafe_command).read_text(encoding="utf-8").splitlines()[0]
    if not first_line.startswith("#!"):
        raise RuntimeError(f"cafe command has no interpreter shebang: {cafe_command}")
    interpreter = shlex.split(first_line[2:].strip())
    if not interpreter:
        raise RuntimeError(f"cafe command has an empty interpreter shebang: {cafe_command}")
    environment = dict(os.environ)
    environment["CAFE_KICKOFF_FORMATTER_REEXEC"] = "1"
    os.execvpe(
        interpreter[0],
        [*interpreter, str(Path(__file__).resolve()), *sys.argv[1:]],
        environment,
    )


try:
    import yaml  # type: ignore[import-untyped]

    from cafe.core.playbook import (
        confirmation_gate_steps,
        mandatory_confirmation_gate_steps,
    )
    from cafe.core.types import AgentCLI
    from cafe.playbooks.loader import PlaybookLoader
    from cafe.skills.execution_profile import resolve_execution_profile
    from cafe.skills.loader import SkillLoader
    from cafe.utils.phase_config import load_phase_step_model
except ModuleNotFoundError:
    _reexec_with_cafe_python()
    raise

from proactive_review import policy_digest, validate_policy


ModelChain = list[tuple[str, str]]


def _project_path(path: Path, project_root: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def _items(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or ():
        for item in value.split(","):
            token = item.strip()
            if token and token not in result:
                result.append(token)
    return result


def _positive_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer number of seconds") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def _driver_policy_rows(args: argparse.Namespace) -> list[list[Any]]:
    rows: list[list[Any]] = [
        ["contract_version", 2],
        ["driver.mode", args.driver_mode],
    ]
    if args.driver_mode == "attached":
        if args.poll_interval_seconds is None:
            raise ValueError("attached driver requires --poll-interval-seconds")
        if args.event_driver_cli is not None or args.event_driver_model is not None:
            raise ValueError("attached driver rejects event-driven fields")
        rows.extend(
            [
                ["driver.poll_interval_seconds", args.poll_interval_seconds],
                [
                    "driver.first_poll",
                    "after the full interval; no startup or transport-level poll",
                ],
                [
                    "driver.poll_timestamp",
                    "capture and print current system time with every proactive poll",
                ],
            ]
        )
    elif args.driver_mode == "unattended":
        if any(
            value is not None
            for value in (
                args.poll_interval_seconds,
                args.event_driver_cli,
                args.event_driver_model,
            )
        ):
            raise ValueError("unattended driver accepts no mode-specific fields")
    else:
        if args.poll_interval_seconds is not None:
            raise ValueError("event-driven driver rejects attached polling")
        if args.event_driver_cli is None or not args.event_driver_model:
            raise ValueError(
                "event-driven driver requires --event-driver-cli and --event-driver-model"
            )
        rows.extend(
            [
                ["driver.cli", args.event_driver_cli],
                ["driver.model", args.event_driver_model],
            ]
        )
    return rows


def _cell(value: Any) -> str:
    text = str(value if value is not None else "—")
    return text.replace("|", "\\|").replace("\n", "<br>")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(_cell(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_cell(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must contain a top-level mapping: {path}")
    return raw


def _json_mapping(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def _validate_preflight(value: dict[str, Any], *, label: str, required: set[str]) -> dict[str, Any]:
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{label} preflight is missing: {', '.join(missing)}")
    return value


def _load_strategic_context(path: Path, issue_name: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise ValueError(f"strategic context not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("mandate"), dict):
        raise ValueError(f"strategic context has no mandate mapping: {path}")

    mandate = dict(raw["mandate"])
    source = "mandate"
    issues = raw.get("issues")
    issue_override = issues.get(issue_name) if isinstance(issues, dict) else None
    if isinstance(issue_override, dict):
        source = f"mandate + issues.{issue_name}"
        for key, value in issue_override.items():
            if key == "axes" and isinstance(value, dict):
                axes = dict(mandate.get("axes") or {})
                axes.update(value)
                mandate["axes"] = axes
            else:
                mandate[key] = value
    return mandate, source


def _resolve_partition(
    *,
    candidates: tuple[str, ...],
    user_values: list[str] | None,
    driver_values: list[str] | None,
) -> tuple[list[str], list[str]]:
    if user_values is None and driver_values is None:
        return list(candidates), []

    user_required = _items(user_values)
    driver_confirmable = _items(driver_values)
    candidate_set = set(candidates)
    user_set = set(user_required)
    driver_set = set(driver_confirmable)
    overlap = user_set & driver_set
    unknown = (user_set | driver_set) - candidate_set
    missing = candidate_set - (user_set | driver_set)
    problems = []
    if overlap:
        problems.append(f"overlapping gates: {', '.join(sorted(overlap))}")
    if unknown:
        problems.append(f"unknown gates: {', '.join(sorted(unknown))}")
    if missing:
        problems.append(f"unassigned gates: {', '.join(sorted(missing))}")
    if problems:
        raise ValueError("invalid confirmation partition: " + "; ".join(problems))
    return user_required, driver_confirmable


def _parse_chain(raw_chain: str, *, step_name: str) -> ModelChain:
    chain: ModelChain = []
    seen_clis: set[str] = set()
    for raw_entry in raw_chain.split(","):
        cli, separator, model = raw_entry.strip().partition(":")
        cli, model = cli.strip(), model.strip()
        if not separator or not cli or not model:
            raise ValueError("invalid phase-chain entry; expected CLI:MODEL")
        try:
            cli = AgentCLI(cli).value
        except ValueError as exc:
            raise ValueError(f"unsupported CLI '{cli}' in phase chain for {step_name}") from exc
        if cli in seen_clis:
            raise ValueError(f"duplicate CLI '{cli}' in phase chain for {step_name}")
        seen_clis.add(cli)
        chain.append((cli, model))
    return _validate_chain(chain, step_name=step_name)


def _validate_chain(
    chain: Iterable[tuple[str, str | None]],
    *,
    step_name: str,
) -> ModelChain:
    chain = list(chain)
    if not chain:
        raise ValueError(f"phase chain for {step_name} must include a primary")
    seen: set[str] = set()
    result: ModelChain = []
    for raw_cli, raw_model in chain:
        try:
            cli = AgentCLI(raw_cli).value
        except ValueError as exc:
            raise ValueError(f"unsupported CLI '{raw_cli}' in phase chain for {step_name}") from exc
        model = str(raw_model or "").strip()
        if not model:
            raise ValueError(f"phase chain for {step_name} has an unresolved model for CLI '{cli}'")
        if cli in seen:
            raise ValueError(f"duplicate CLI '{cli}' in phase chain for {step_name}")
        seen.add(cli)
        result.append((cli, model))
    return result


def _parse_phase_chains(
    values: list[str],
    *,
    step_names: set[str],
) -> dict[str, ModelChain]:
    parsed: dict[str, ModelChain] = {}
    for value in values:
        step_name, separator, raw_chain = value.partition("=")
        step_name = step_name.strip()
        if not separator or not step_name or not raw_chain.strip():
            raise ValueError("invalid --phase-chain; expected STEP=CLI:MODEL[,CLI:MODEL...]")
        if step_name not in step_names:
            raise ValueError(f"unknown phase-chain step: {step_name}")
        if step_name in parsed:
            raise ValueError(f"duplicate phase-chain step: {step_name}")
        parsed[step_name] = _parse_chain(raw_chain, step_name=step_name)
    return parsed


def _parse_phase_rationales(
    values: list[str],
    *,
    step_names: set[str],
) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        step_name, separator, rationale = value.partition("=")
        step_name, rationale = step_name.strip(), rationale.strip()
        if not separator or not step_name or not rationale:
            raise ValueError("invalid --phase-rationale; expected STEP=RATIONALE")
        if step_name not in step_names:
            raise ValueError(f"unknown phase-rationale step: {step_name}")
        if step_name in parsed:
            raise ValueError(f"duplicate phase-rationale step: {step_name}")
        parsed[step_name] = rationale
    return parsed


def _resolve_configured_chain(
    *,
    step_name: str,
    role: str,
    phase_config: Path,
) -> tuple[ModelChain, str]:
    phase = load_phase_step_model(
        step_name=step_name,
        local_path=phase_config if phase_config.is_file() else None,
    )
    if phase.role is not None and phase.role != role:
        raise ValueError(
            f"phase config role mismatch for '{step_name}': expected '{role}', got '{phase.role}'"
        )
    return _validate_chain(list(phase.clis), step_name=step_name), str(phase_config)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Format a structurally validated CAFE kickoff contract as Markdown tables."
    )
    parser.add_argument("playbook_id")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--playbook-rationale", required=True)
    parser.add_argument("--issue-nature", required=True)
    parser.add_argument(
        "--issue-scale",
        choices=("small", "medium", "large"),
        required=True,
    )
    parser.add_argument(
        "--model-adjustment-authority",
        choices=tuple(sorted(_MODEL_ADJUSTMENT_AUTHORITIES)),
        required=True,
    )
    parser.add_argument("--update-preflight", type=_json_mapping, required=True)
    parser.add_argument("--catalog-preflight", type=_json_mapping, required=True)
    parser.add_argument("--driver-mode", choices=tuple(sorted(_DRIVER_MODES)), required=True)
    parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_seconds,
    )
    parser.add_argument("--event-driver-cli", choices=tuple(sorted(_EVENT_DRIVEN_CLIS)))
    parser.add_argument("--event-driver-model")
    parser.add_argument("--risk-factor", action="append", required=True)
    parser.add_argument("--assessment-rationale", required=True)
    parser.add_argument(
        "--proactive-review-json",
        type=_json_mapping,
        help=(
            "Complete issue-specific proactive phase-review policy. Rendering validates "
            "and digests it without writing issue state."
        ),
    )
    parser.add_argument(
        "--phase-chain",
        action="append",
        default=[],
        metavar="STEP=CLI:MODEL[,CLI:MODEL...]",
        help="Exact ordered chain for a phase; otherwise resolve phases.yaml.",
    )
    parser.add_argument(
        "--phase-rationale",
        action="append",
        default=[],
        metavar="STEP=RATIONALE",
        help="Driver-assessed capability band and evidence for an agent-executed phase.",
    )
    parser.add_argument("--phase-config", type=Path, default=Path(".cafe/phases.yaml"))
    parser.add_argument("--effective-locale")
    parser.add_argument("--locale-source")
    parser.add_argument("--repository-content-locale", required=True)
    parser.add_argument("--user-required", nargs="*", default=None)
    parser.add_argument("--driver-confirmable", nargs="*", default=None)
    parser.add_argument(
        "--strategic-context",
        type=Path,
        default=Path(".cafe/strategic_context.yaml"),
    )
    checkout = parser.add_mutually_exclusive_group(required=True)
    checkout.add_argument("--worktree")
    checkout.add_argument("--current-checkout", action="store_true")
    parser.add_argument("--need-clarification", default="user_required")
    parser.add_argument("--need-permission", default="user_required")
    parser.add_argument(
        "--alignment-checkpoint",
        default="driver_resolvable_when_clear",
    )
    return parser


def render(args: argparse.Namespace) -> str:
    project_root = args.project_root.resolve()
    playbook_rationale = args.playbook_rationale.strip()
    if not playbook_rationale:
        raise ValueError("--playbook-rationale must not be empty")
    playbook_loader = PlaybookLoader(project_root=project_root)
    loaded = playbook_loader.load_model(args.playbook_id)
    model = loaded.model
    proactive_policy = (
        validate_policy(args.proactive_review_json, playbook=model)
        if args.proactive_review_json is not None
        else None
    )
    skill_loader = SkillLoader(project_root=project_root)
    candidates = confirmation_gate_steps(model)
    mandatory_human_tasks = mandatory_confirmation_gate_steps(model)
    user_required, driver_confirmable = _resolve_partition(
        candidates=candidates,
        user_values=args.user_required,
        driver_values=args.driver_confirmable,
    )

    configured_locale = model.playbook.conversation_locale
    effective_locale = args.effective_locale or configured_locale
    if effective_locale.lower() == "auto":
        raise ValueError("--effective-locale is required when the playbook locale is auto")
    locale_source = args.locale_source or f"playbook:{args.playbook_id}"
    strategic_context = _project_path(args.strategic_context, project_root)
    phase_config = _project_path(args.phase_config, project_root)
    mandate, mandate_source = _load_strategic_context(strategic_context, args.issue_name)
    phase_chain_overrides = _parse_phase_chains(
        args.phase_chain,
        step_names=set(model.steps),
    )
    phase_rationales = _parse_phase_rationales(
        args.phase_rationale,
        step_names=set(model.steps),
    )
    update_preflight = _validate_preflight(
        args.update_preflight,
        label="runtime update",
        required={
            "checked_at",
            "status",
            "installed_version",
            "latest_version",
            "decision",
            "comparison_token",
            "post_change_evidence",
        },
    )
    catalog_preflight = _validate_preflight(
        args.catalog_preflight,
        label="catalog",
        required={
            "checked_at",
            "status",
            "comparison_token",
            "effective_digests",
            "decision",
            "post_change_evidence",
        },
    )
    effective_digests = catalog_preflight["effective_digests"]
    if not isinstance(effective_digests, dict) or set(effective_digests) != {
        "playbook",
        "phase",
        "agent",
    }:
        raise ValueError(
            "catalog preflight effective_digests must cover playbook, phase, and agent"
        )
    zh = effective_locale.lower().startswith("zh")
    worktree = args.worktree if args.worktree else "current checkout"

    if zh:
        title = f"## Kickoff Contract — {args.issue_name}"
        summary_headers = ["欄位", "值"]
        phase_headers = [
            "Phase",
            "Role",
            "Skill",
            "排程確認 gate",
            "預定處理者",
            "會停下來給 user 確認",
        ]
        yes, no = "是", "否"
        no_gate, user_owner = "—", "user"
        mandatory_user_owner = "user（mandatory）"
        driver_owner = "driver（驗證後繼續）"
        reactive_title = "### Reactive user handoffs"
        reactive_headers = ["Intent", "Policy", "是否為排程 gate"]
    else:
        title = f"## Kickoff Contract — {args.issue_name}"
        summary_headers = ["Field", "Value"]
        phase_headers = [
            "Phase",
            "Role",
            "Skill",
            "Scheduled confirmation gate",
            "Planned owner",
            "Stops for user confirmation",
        ]
        yes, no = "yes", "no"
        no_gate, user_owner = "—", "user"
        mandatory_user_owner = "user (mandatory)"
        driver_owner = "driver (verify, then continue)"
        reactive_title = "### Reactive user handoffs"
        reactive_headers = ["Intent", "Policy", "Scheduled gate"]

    summary = _table(
        summary_headers,
        [
            ["playbook_id", args.playbook_id],
            ["playbook_source", f"{loaded.source}: {loaded.path}"],
            ["playbook_selection_rationale", playbook_rationale],
            ["configured_locale", configured_locale],
            ["effective_locale", f"{effective_locale} ({locale_source})"],
            ["repository_content_locale", args.repository_content_locale],
            ["issue_nature", args.issue_nature],
            ["issue_scale", args.issue_scale],
            ["risk_factors", ", ".join(args.risk_factor)],
            ["assessment_rationale", args.assessment_rationale],
            ["model_adjustment_authority", args.model_adjustment_authority],
            *_driver_policy_rows(args),
            ["user_required", ", ".join(user_required) or "[]"],
            ["driver_confirmable", ", ".join(driver_confirmable) or "[]"],
            [
                "mandatory_human_tasks",
                ", ".join(mandatory_human_tasks) or "[]",
            ],
            ["worktree", worktree],
            ["mandate_source", mandate_source],
        ],
    )
    preflight = _table(
        summary_headers,
        [
            ["runtime_update.checked_at", update_preflight["checked_at"]],
            ["runtime_update.status", update_preflight["status"]],
            [
                "runtime_update.versions",
                f"{update_preflight['installed_version']} → "
                f"{update_preflight['latest_version']}",
            ],
            ["runtime_update.decision", update_preflight["decision"]],
            [
                "runtime_update.comparison_token",
                update_preflight["comparison_token"],
            ],
            [
                "runtime_update.post_change_evidence",
                update_preflight["post_change_evidence"],
            ],
            ["catalog.checked_at", catalog_preflight["checked_at"]],
            ["catalog.status", catalog_preflight["status"]],
            ["catalog.decision", catalog_preflight["decision"]],
            ["catalog.comparison_token", catalog_preflight["comparison_token"]],
            [
                "catalog.effective_digests",
                ", ".join(
                    f"{kind}={effective_digests[kind]}" for kind in ("playbook", "phase", "agent")
                ),
            ],
            [
                "catalog.post_change_evidence",
                catalog_preflight["post_change_evidence"],
            ],
        ],
    )

    phase_rows: list[list[Any]] = []
    model_rows: list[list[Any]] = []
    profile_rows: list[list[Any]] = []
    for step_name, step in model.steps.items():
        if step_name in mandatory_human_tasks:
            gate, owner, stop = yes, mandatory_user_owner, yes
        elif step_name in user_required:
            gate, owner, stop = yes, user_owner, yes
        elif step_name in driver_confirmable:
            gate, owner, stop = yes, driver_owner, no
        else:
            gate, owner, stop = no, no_gate, no
        profile = resolve_execution_profile(skill_loader, step.skill)
        skill_label = ", ".join(profile.skill_names)
        phase_rows.append([step_name, step.role, skill_label, gate, owner, stop])
        profile_rows.append(
            [
                step_name,
                skill_label,
                ", ".join(profile.workloads),
                profile.reasoning,
                ", ".join(profile.risk_domains) or "—",
                profile.fallback_strength,
                "defaulted" if profile.uses_default else "declared",
            ]
        )
        if step.assignee_type not in {"agent", "hybrid"}:
            model_rows.append([step_name, "not agent-executed", "—", "playbook", "—"])
            continue
        if step_name in phase_chain_overrides:
            chain, chain_source = phase_chain_overrides[step_name], "--phase-chain"
        else:
            chain, chain_source = _resolve_configured_chain(
                step_name=step_name,
                role=step.role,
                phase_config=phase_config,
            )
        primary = f"{chain[0][0]}:{chain[0][1]}"
        fallbacks = " → ".join(f"{cli}:{model_name}" for cli, model_name in chain[1:]) or "—"
        rationale = phase_rationales.get(step_name)
        if rationale is None:
            raise ValueError(f"missing phase rationale for agent-executed step: {step_name}")
        model_rows.append([step_name, primary, fallbacks, chain_source, rationale])

    unused_rationales = set(phase_rationales) - {
        name for name, step in model.steps.items() if step.assignee_type in {"agent", "hybrid"}
    }
    if unused_rationales:
        raise ValueError(
            "phase rationale targets non-agent step: " + ", ".join(sorted(unused_rationales))
        )

    reactive = _table(
        reactive_headers,
        [
            ["need_clarification", args.need_clarification, no],
            ["need_permission", args.need_permission, no],
            ["alignment_checkpoint", args.alignment_checkpoint, no],
        ],
    )

    axes = mandate.get("axes") or {}
    mandate_rows = []
    if isinstance(axes, dict):
        for axis, policy in axes.items():
            if isinstance(policy, dict):
                grounds = policy.get("grounds") or []
                if isinstance(grounds, list):
                    grounds = ", ".join(str(item) for item in grounds)
                mandate_rows.append([axis, policy.get("level", "—"), grounds or "—"])
            else:
                mandate_rows.append([axis, policy, "—"])
    out_of_mandate = mandate.get("out_of_mandate") or []
    if isinstance(out_of_mandate, list):
        out_of_mandate = ", ".join(str(item) for item in out_of_mandate)
    mandate_summary = _table(
        summary_headers,
        [
            ["preset", mandate.get("preset", "—")],
            ["out_of_mandate", out_of_mandate or "[]"],
        ],
    )

    proactive_section: list[str] = []
    if proactive_policy is not None:
        proactive_rows: list[list[Any]] = []
        for entry in proactive_policy["phases"]:
            if entry["selected"]:
                reviewer = entry["reviewer"]
                initial = entry["initial_review_cost"]
                rereview = entry["rereview_cost"]
                reviewer_label = f"{reviewer['cli']}:{reviewer['model']}"
                initial_label = json.dumps(initial, ensure_ascii=False, sort_keys=True)
                rereview_label = json.dumps(rereview, ensure_ascii=False, sort_keys=True)
                ordering = entry["ordering"]
                decision = "selected"
            else:
                reviewer_label = "—"
                ordering = "—"
                initial_label = "—"
                rereview_label = "—"
                decision = "excluded"
            proactive_rows.append(
                [
                    entry["phase"],
                    decision,
                    entry["rationale"],
                    reviewer_label,
                    ordering,
                    initial_label,
                    rereview_label,
                ]
            )
        proactive_section = [
            "### Proactive phase reviews — driver-assessed",
            _table(
                [
                    "Phase",
                    "Decision",
                    "Selection rationale",
                    "Exact reviewer",
                    "Ordering",
                    "Initial review cost and delay impact",
                    "Correction/re-review cost",
                ],
                proactive_rows,
            ),
            f"proposal_digest: `{policy_digest(proactive_policy)}`",
            (
                "The user confirms this complete review plan with the kickoff contract. "
                "A clean proactive review is quality evidence only and never completes a "
                "HumanTask, permission, capability, scope, or authority decision."
            ),
        ]

    return "\n\n".join(
        [
            title,
            summary,
            "### Preflight evidence",
            preflight,
            "### Phases",
            _table(phase_headers, phase_rows),
            "### Phase execution requirements",
            _table(
                [
                    "Phase",
                    "Resolved skill variants",
                    "Workload",
                    "Reasoning",
                    "Risk domains",
                    "Fallback strength",
                    "Profile source",
                ],
                profile_rows,
            ),
            "### Phase model chains — driver-assessed",
            _table(
                ["Phase", "Primary", "Fallbacks", "Source", "Selection rationale"],
                model_rows,
            ),
            reactive_title,
            reactive,
            "### Mandate",
            mandate_summary,
            _table(["Axis", "Level", "Grounds"], mandate_rows),
            *proactive_section,
        ]
    )


def main() -> int:
    try:
        print(render(_parser().parse_args()))
    except (FileNotFoundError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

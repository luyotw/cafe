#!/usr/bin/env python3
"""Render a structurally validated, provider-neutral CAFE kickoff contract."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import string
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_SOURCE_ROOT = Path(__file__).resolve().parents[5]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

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
    from cafe.agents.executor import AgentExecutor
    from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
    from cafe.core.capability_setup import resolve_setup_choices
    from cafe.core.playbook import (
        confirmation_gate_steps,
        mandatory_confirmation_gate_steps,
        resolve_playbook_skills,
    )
    from cafe.core.types import AgentCLI, AgentConfig
    from cafe.driver import ActivateConfirmedContract, activate_confirmed_contract
    from cafe.driver.delivery import normalize_delivery_contract
    from cafe.playbooks.loader import PlaybookLoader
    from cafe.skills.execution_profile import resolve_execution_profile
    from cafe.skills.loader import SkillLoader
    from cafe.utils.phase_config import load_phase_step_model
except ModuleNotFoundError:
    _reexec_with_cafe_python()
    raise

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_workflow_progress import render_progress  # noqa: E402, I001


ModelChain = list[tuple[str, str]]
EventDriverChain = list[tuple[str, str | None]]


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


def _kickoff_delivery_contract(args: argparse.Namespace) -> dict[str, Any]:
    """Combine concise product facts with separately confirmed exact closeout commands."""
    core = args.delivery_contract
    if core.get("schema_version") != 3:
        raise ValueError("new kickoff requires version-3 delivery facts")
    if "closeout_plan" in core:
        raise ValueError("--delivery-contract must omit closeout_plan; use --deliver and --cleanup")
    return normalize_delivery_contract(
        {
            **core,
            "closeout_plan": {
                "deliver": [{"argv": command} for command in args.deliver],
                "cleanup": [{"argv": command} for command in args.cleanup],
            },
        }
    )


def _positive_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer number of seconds") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def _capability_choices(args: argparse.Namespace, model: Any) -> list[Any]:
    registry = load_capability_registry(default_capability_definition_dirs(args.project_root))
    return resolve_setup_choices(model, registry, args.capability_choice)


def _driver_policy_rows(args: argparse.Namespace) -> list[list[Any]]:
    rows: list[list[Any]] = [["driver.mode", args.driver_mode]]
    if args.driver_mode == "attached":
        if args.poll_interval_seconds is None:
            raise ValueError("attached driver requires --poll-interval-seconds")
        if args.event_driver:
            raise ValueError("attached driver rejects event-driven fields")
        rows.append(["driver.poll_interval_seconds", args.poll_interval_seconds])
    elif args.driver_mode == "unattended":
        if args.poll_interval_seconds is not None or args.event_driver:
            raise ValueError("unattended driver accepts no mode-specific fields")
    else:
        if args.poll_interval_seconds is not None:
            raise ValueError("event-driven driver rejects attached polling")
        rows.extend(
            [f"driver.clis[{index}]", cli if model is None else f"{cli}:{model}"]
            for index, (cli, model) in enumerate(_parse_event_driver_entries(args.event_driver))
        )
    return rows


def _parse_event_driver_entries(values: Iterable[str] | None) -> EventDriverChain:
    entries: EventDriverChain = []
    seen: set[str] = set()
    for index, value in enumerate(values or ()):
        raw_cli, separator, raw_model = value.partition(":")
        cli, model = raw_cli.strip(), raw_model.strip()
        if not cli or (index == 0 and separator) or (index > 0 and (not separator or not model)):
            raise ValueError("event-driven primary must use CLI and fallbacks must use CLI:MODEL")
        try:
            cli = AgentCLI(cli).value
        except ValueError as exc:
            raise ValueError(f"unsupported event-driven CLI '{cli}'") from exc
        if (
            cli not in _EVENT_DRIVEN_CLIS
            or not AgentExecutor(
                AgentConfig(name="__cafe_event_driver__", cli=AgentCLI(cli), model=model),
                stream_output=False,
            ).supports_event_driver()
        ):
            raise ValueError(f"CLI '{cli}' lacks the event-driven contract")
        if cli in seen:
            raise ValueError(f"duplicate event-driven CLI '{cli}'")
        seen.add(cli)
        entries.append((cli, None if index == 0 else model))
    if not entries:
        raise ValueError("event-driven driver requires a primary --event-driver CLI")
    return entries


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


def _fact(value: str | list[str]) -> str:
    """Render literal prose without letting facts create Markdown structure."""
    items = value if isinstance(value, list) else [value]
    escapes = str.maketrans({mark: f"\\{mark}" for mark in string.punctuation})
    return "\n".join(
        "- " + item.translate(escapes).replace("\n", "\n  ") for item in items
    ) or "- []"


def _json_mapping(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def _json_argv_list(value: str) -> list[list[str]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, list) or any(
        not isinstance(command, list) or any(not isinstance(item, str) for item in command)
        for command in parsed
    ):
        raise argparse.ArgumentTypeError("must be a JSON array of string arrays")
    return parsed


def _validate_preflight(value: dict[str, Any], *, label: str, required: set[str]) -> dict[str, Any]:
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{label} preflight is missing: {', '.join(missing)}")
    return value


def _resolve_partition(
    *,
    candidates: tuple[str, ...],
    user_values: list[str] | None,
    driver_values: list[str] | None,
) -> tuple[list[str], list[str]]:
    if user_values is None and driver_values is None:
        return [], list(candidates)

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
        allow_abbrev=False,
        description="Format a structurally validated CAFE kickoff contract as Markdown tables.",
    )
    parser.add_argument("playbook_id")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--issue-name", required=True)
    parser.add_argument(
        "--delivery-contract",
        type=_json_mapping,
        required=True,
        help=(
            "Version-3 product facts without closeout_plan; the formatter adds the exact "
            "closeout commands."
        ),
    )
    parser.add_argument(
        "--deliver",
        required=True,
        type=_json_argv_list,
        metavar="JSON_ARGV_LIST",
        help="Exact ordered deliver argv arrays, including [] when nothing remains.",
    )
    parser.add_argument(
        "--cleanup",
        required=True,
        type=_json_argv_list,
        metavar="JSON_ARGV_LIST",
        help="Exact ordered cleanup argv arrays, including [] when nothing remains.",
    )
    parser.add_argument("--update-preflight", type=_json_mapping, required=True)
    parser.add_argument("--catalog-preflight", type=_json_mapping, required=True)
    parser.add_argument("--driver-mode", choices=tuple(sorted(_DRIVER_MODES)), required=True)
    parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_seconds,
    )
    parser.add_argument(
        "--event-driver",
        action="append",
        default=[],
        metavar="CLI[:MODEL]",
    )
    parser.add_argument(
        "--phase-chain",
        action="append",
        default=[],
        metavar="STEP=CLI:MODEL[,CLI:MODEL...]",
        help="Exact ordered chain for a phase; otherwise resolve phases.yaml.",
    )
    parser.add_argument("--phase-config", type=Path, default=Path(".cafe/phases.yaml"))
    parser.add_argument("--effective-locale")
    parser.add_argument("--locale-source")
    parser.add_argument("--repository-content-locale", required=True)
    parser.add_argument(
        "--capability-choice",
        action="append",
        default=[],
        metavar="SETTING=JSON",
        help="Explicit answer to a setup question declared by an effective capability.",
    )
    parser.add_argument("--user-required", nargs="*", default=None)
    parser.add_argument("--driver-confirmable", nargs="*", default=None)
    checkout = parser.add_mutually_exclusive_group(required=True)
    checkout.add_argument("--worktree")
    checkout.add_argument("--current-checkout", action="store_true")
    parser.add_argument("--need-clarification", default="driver_confirmable")
    parser.add_argument("--need-permission", default="user_required")
    parser.add_argument(
        "--alignment-checkpoint",
        default="driver_resolvable_when_clear",
    )
    parser.add_argument(
        "--proactive-review-decision",
        action="append",
        default=[],
        metavar="PHASE=required|not_required",
        help="Override the default proactive-review decision for an eligible phase.",
    )
    parser.add_argument(
        "--activate-confirmed",
        action="store_true",
        help="Persist the complete proposal after the user has already confirmed it.",
    )
    parser.add_argument("--workflow-id")
    parser.add_argument("--confirmed-by")
    parser.add_argument("--confirmed-at")
    parser.add_argument("--issue-dir", type=Path)
    return parser


def _proactive_review_decisions(
    values: Iterable[str], *, agent_phases: list[str], eligible_phases: set[str]
) -> list[dict[str, str]]:
    """Resolve sparse overrides into complete ordered review decisions."""
    decisions: dict[str, dict[str, str]] = {}
    for raw in values:
        phase, separator, state = raw.partition("=")
        phase, state = phase.strip(), state.strip()
        if not separator or state not in {"required", "not_required"}:
            raise ValueError("proactive review decisions use PHASE=required|not_required")
        if phase in decisions:
            raise ValueError(f"duplicate proactive review decision: {phase}")
        if phase not in agent_phases:
            raise ValueError(f"proactive review targets unknown or non-agent phase: {phase}")
        if state == "required" and phase not in eligible_phases:
            raise ValueError(
                f"proactive review phase '{phase}' cannot be required because it has no "
                "scheduled confirmation pause before workflow advancement"
            )
        decisions[phase] = {"phase": phase, "decision": state}
    if list(decisions) != [phase for phase in agent_phases if phase in decisions]:
        raise ValueError("proactive review overrides must follow agent phase order")
    return [
        decisions.get(
            phase,
            {
                "phase": phase,
                "decision": "required" if phase in eligible_phases else "not_required",
            },
        )
        for phase in agent_phases
    ]


def _preflight_reports(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    update = _validate_preflight(
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
    catalog = _validate_preflight(
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
    if not isinstance(catalog["effective_digests"], dict) or set(catalog["effective_digests"]) != {
        "playbook",
        "phase",
        "agent",
    }:
        raise ValueError(
            "catalog preflight effective_digests must cover playbook, phase, and agent"
        )
    mismatch_ids = catalog.get("content_mismatch_entry_ids", [])
    if not isinstance(mismatch_ids, list) or not all(
        isinstance(entry_id, str) and entry_id.strip() for entry_id in mismatch_ids
    ):
        raise ValueError("catalog preflight content_mismatch_entry_ids must be a string list")
    return update, catalog


def build_confirmed_proposal(args: argparse.Namespace) -> dict[str, Any]:
    """Build only the issue's user-confirmed delivery and execution decisions."""
    project_root = args.project_root.resolve()
    model = PlaybookLoader(project_root=project_root).load_model(args.playbook_id).model
    candidates = confirmation_gate_steps(model)
    mandatory_human_tasks = mandatory_confirmation_gate_steps(model)
    user_required, driver_confirmable = _resolve_partition(
        candidates=candidates,
        user_values=args.user_required,
        driver_values=args.driver_confirmable,
    )
    effective_locale = args.effective_locale or model.playbook.conversation_locale
    if effective_locale.lower() == "auto":
        raise ValueError("--effective-locale is required when the playbook locale is auto")
    _preflight_reports(args)
    _driver_policy_rows(args)
    _capability_choices(args, model)
    overrides = _parse_phase_chains(args.phase_chain, step_names=set(model.steps))
    phase_config = _project_path(args.phase_config, project_root)
    phases: list[dict[str, Any]] = []
    for step_name, step in model.steps.items():
        if step.assignee_type not in {"agent", "hybrid"}:
            continue
        selected = overrides.get(step_name)
        if selected is None:
            selected, _ = _resolve_configured_chain(
                step_name=step_name, role=step.role, phase_config=phase_config
            )
        phases.append(
            {
                "name": step_name,
                "chain": [{"cli": cli, "model": model_name} for cli, model_name in selected],
            }
        )
    proposal: dict[str, Any] = {
        "delivery_contract": _kickoff_delivery_contract(args),
        "locales": {
            "conversation": {
                "value": effective_locale,
                "source": args.locale_source or f"playbook:{args.playbook_id}",
            },
        },
        "confirmation_contract": {
            "user_required": user_required,
            "driver_confirmable": driver_confirmable,
            "mandatory_human_stops": list(mandatory_human_tasks),
        },
        "reactive_user_handoffs": {
            "need_clarification": args.need_clarification,
            "need_permission": args.need_permission,
            "alignment_checkpoint": args.alignment_checkpoint,
        },
        "phases": phases,
        "proactive_review": {
            "phase_decisions": _proactive_review_decisions(
                args.proactive_review_decision,
                agent_phases=[phase["name"] for phase in phases],
                eligible_phases=set(candidates) | set(mandatory_human_tasks),
            )
        },
        "driver": {"mode": args.driver_mode},
        "checkout": (
            {"kind": "worktree", "path": args.worktree}
            if args.worktree
            else {"kind": "current_checkout"}
        ),
    }
    if args.driver_mode == "attached":
        proposal["driver"]["poll_interval_seconds"] = args.poll_interval_seconds
    elif args.driver_mode == "event-driven":
        proposal["driver"]["clis"] = [
            {"cli": cli} if model_name is None else {"cli": cli, "model": model_name}
            for cli, model_name in _parse_event_driver_entries(args.event_driver)
        ]
    return proposal


def activate_confirmed_proposal(
    args: argparse.Namespace, *, proposal: dict[str, Any] | None = None
) -> None:
    """Persist only after explicit confirmation metadata and prepared identity are present."""
    if not args.workflow_id or not args.confirmed_by or not args.confirmed_at:
        raise ValueError(
            "activation requires workflow ID, confirmer, and timezone-aware confirmation time"
        )
    issue_dir = args.issue_dir or args.project_root / ".cafe" / "issues" / args.issue_name
    blackboard = issue_dir / "blackboard.json"
    if not blackboard.is_file() or blackboard.is_symlink():
        raise ValueError("activation requires a prepared issue identity")
    try:
        prepared = json.loads(blackboard.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("prepared issue identity is unreadable") from exc
    if not isinstance(prepared, dict) or prepared.get("workflow_id") != args.workflow_id:
        raise ValueError("activation workflow ID does not match the prepared issue")
    confirmed_at = datetime.fromisoformat(args.confirmed_at.replace("Z", "+00:00"))
    confirmed_proposal = proposal or build_confirmed_proposal(args)
    command = ActivateConfirmedContract(
        issue_dir=issue_dir,
        issue_name=args.issue_name,
        workflow_id=args.workflow_id,
        confirmed_by=args.confirmed_by,
        confirmed_at=confirmed_at,
        proposal=confirmed_proposal,
    )
    driver = confirmed_proposal.get("driver")
    if isinstance(driver, dict) and driver.get("mode") == "event-driven":
        from workflow_event_callback import activate_confirmed_contract_with_host_session

        activate_confirmed_contract_with_host_session(
            issue_dir=issue_dir,
            issue_name=args.issue_name,
            workflow_id=args.workflow_id,
            activate_contract=lambda: activate_confirmed_contract(command),
        )
    else:
        activate_confirmed_contract(command)


def render(args: argparse.Namespace, *, confirmed_proposal: dict[str, Any] | None = None) -> str:
    proposal = (
        confirmed_proposal if confirmed_proposal is not None else build_confirmed_proposal(args)
    )
    project_root = args.project_root.resolve()
    model = PlaybookLoader(project_root=project_root).load_model(args.playbook_id).model
    capability_choices = _capability_choices(args, model)
    skill_loader = SkillLoader(project_root=project_root)
    locale = proposal["locales"]["conversation"]
    effective_locale = locale["value"]
    locale_token = effective_locale.strip().lower().replace("_", "-")
    zh = locale_token == "zh-tw" or locale_token.startswith("zh-hant")
    headers = ["欄位", "值"] if zh else ["Field", "Value"]
    confirmation_prompt = (
        "請確認上述完整契約；確認後 Driver 才會準備並啟動 workflow。"
        if zh
        else "Please confirm the complete contract above before the Driver prepares "
        "and starts the workflow."
    )
    driver = proposal["driver"]
    driver_rows: list[list[Any]] = [["driver.mode", driver["mode"]]]
    if driver["mode"] == "attached":
        driver_rows.append(["driver.poll_interval_seconds", driver["poll_interval_seconds"]])
    elif driver["mode"] == "event-driven":
        driver_rows.extend(
            [
                f"driver.clis[{index}]",
                entry["cli"] if index == 0 else f"{entry['cli']}:{entry['model']}",
            ]
            for index, entry in enumerate(driver["clis"])
        )
    summary = _table(
        headers,
        [
            ["playbook_id", args.playbook_id],
            ["effective_locale", f"{effective_locale} ({locale['source']})"],
            ["repository_content_locale", args.repository_content_locale],
            *driver_rows,
            ["worktree", proposal["checkout"].get("path", "current checkout")],
        ],
    )
    capability_contracts = [
        "\n\n".join(
            [
                f"### {question.prompt}",
                _table(
                    ["Setting", "Selected value", "Outcome", "Prepare arguments"],
                    [
                        [
                            question.setting,
                            json.dumps(selected.value, ensure_ascii=False),
                            selected.outcome,
                            shlex.join(selected.prepare_args),
                        ]
                    ],
                ),
            ]
        )
        for question, selected in capability_choices
    ]
    update, catalog = _preflight_reports(args)
    preflight_rows: list[list[Any]] = []
    successful = {"current", "identical", "no_project_entries", "updated", "synchronized"}
    for label, report in (("CAFE", update), ("Catalog", catalog)):
        if report.get("error") or report["status"] not in successful:
            details = [str(report["status"]), str(report["decision"])]
            if label == "CAFE":
                details.insert(0, f"{report['installed_version']} → {report['latest_version']}")
            if report.get("error"):
                details.append(str(report["error"]))
            preflight_rows.append([label, "; ".join(details)])
    preflight = (
        ["### 檢查結果" if zh else "### Checks", _table(headers, preflight_rows)]
        if preflight_rows
        else []
    )
    chains = {phase["name"]: phase["chain"] for phase in proposal["phases"]}
    model_rows: list[list[Any]] = []
    for step_name, step in model.steps.items():
        resolve_execution_profile(
            skill_loader,
            step.skill,
            workflow_skills=resolve_playbook_skills(
                model,
                channel="workflow",
                role=step.role,
                step_name=step_name,
            ),
            step_name=step_name,
        )
        if step.assignee_type not in {"agent", "hybrid"}:
            model_rows.append([step_name, "not agent-executed", "—"])
            continue
        chain = chains[step_name]
        model_rows.append(
            [
                step_name,
                f"{chain[0]['cli']}:{chain[0]['model']}",
                " → ".join(f"{entry['cli']}:{entry['model']}" for entry in chain[1:]) or "—",
            ]
        )
    confirmation = proposal["confirmation_contract"]
    driver_confirmable = set(confirmation["driver_confirmable"])
    eligible = set(confirmation_gate_steps(model)) | set(mandatory_confirmation_gate_steps(model))
    proactive_decisions = proposal["proactive_review"]["phase_decisions"]
    proactive_rows: list[list[Any]] = []
    for decision in proactive_decisions:
        phase = decision["phase"]
        if phase not in eligible:
            continue
        if decision["decision"] == "not_required" and phase in driver_confirmable:
            action = "Driver may confirm after ordinary evidence verification"
        elif decision["decision"] == "not_required":
            action = "user confirmation remains required; no proactive review"
        elif phase in driver_confirmable:
            action = "Driver may confirm and advance after clean review"
        else:
            action = "user confirmation remains required"
        proactive_rows.append([phase, decision["decision"], action])
    progress = render_progress(
        playbook=model,
        contract=proposal,
        locale=effective_locale,
        driver_state={
            "proactive_review": {
                decision["phase"]: "pending"
                for decision in proactive_decisions
                if decision["decision"] == "required"
            },
            "deliver": "pending",
            "cleanup": "pending",
        },
    )
    delivery = proposal["delivery_contract"]
    closeout = delivery["closeout_plan"]
    mismatch_ids = catalog.get("content_mismatch_entry_ids", [])
    catalog_reminder = []
    if mismatch_ids:
        catalog_reminder = [
            "### 可選的 Global catalog 同步" if zh else "### Optional Global catalog sync",
            (
                "下列既有 Global entries 與 project 內容不同："
                if zh
                else "These existing Global entries differ from the project content: "
            )
            + ", ".join(mismatch_ids)
            + (
                "。確認 kickoff 不代表同意發布；如需同步請另行提出。"
                if zh
                else ". Kickoff confirmation does not approve publication; request "
                "synchronization separately if desired."
            ),
        ]
    return "\n\n".join(
        [
            f"## Kickoff Contract — {args.issue_name}",
            "### Delivery Contract",
            "\n\n".join(
                f"#### {key}\n\n{_fact(value)}"
                for key, value in delivery.items()
                if key not in {"closeout_plan", "schema_version"}
            ),
            "Implementation direction is advisory; alternatives that satisfy scope, acceptance "
            "criteria, permissions and constraints do not require reconfirmation.",
            "### Execution settings",
            summary,
            *capability_contracts,
            *preflight,
            "### Phase model chains",
            _table(["Phase", "Primary", "Fallbacks"], model_rows),
            "### Proactive review at scheduled pauses",
            _table(["Phase", "Decision", "Clean result"], proactive_rows),
            "### Reactive user handoffs",
            _table(
                ["Intent", "Policy"],
                [[key, value] for key, value in proposal["reactive_user_handoffs"].items()],
            ),
            "### Deliver and cleanup plan to confirm",
            _table(
                headers,
                [
                    [stage, json.dumps(closeout[stage], ensure_ascii=False)]
                    for stage in ("deliver", "cleanup")
                ],
            ),
            (
                "確認後依序執行上述命令；更改命令、順序、目標或影響時另行確認。"
                if zh
                else "Confirmation authorizes these commands in order; changes to commands, order, "
                "targets or effects require reconfirmation."
            ),
            *catalog_reminder,
            confirmation_prompt,
            "### Workflow progress",
            "```text",
            progress,
            "```",
        ]
    )


def main() -> int:
    try:
        args = _parser().parse_args()
        proposal = build_confirmed_proposal(args)
        rendered = render(args, confirmed_proposal=proposal)
        if args.activate_confirmed:
            activate_confirmed_proposal(args, proposal=proposal)
        print(rendered)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

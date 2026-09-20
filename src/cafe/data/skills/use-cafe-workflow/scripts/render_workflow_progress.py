#!/usr/bin/env python3
"""Render one read-only, contract-aware CAFE workflow progress diagram."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


def _reexec_with_cafe_python() -> None:
    if os.environ.get("CAFE_PROGRESS_RENDERER_REEXEC") == "1":
        raise RuntimeError("cafe's Python environment cannot import renderer dependencies")
    cafe_command = shutil.which("cafe")
    if cafe_command is None:
        raise RuntimeError("cafe command not found; cannot load renderer dependencies")
    first_line = Path(cafe_command).read_text(encoding="utf-8").splitlines()[0]
    if not first_line.startswith("#!"):
        raise RuntimeError(f"cafe command has no interpreter shebang: {cafe_command}")
    interpreter = shlex.split(first_line[2:].strip())
    environment = dict(os.environ)
    environment["CAFE_PROGRESS_RENDERER_REEXEC"] = "1"
    os.execvpe(
        interpreter[0],
        [*interpreter, str(Path(__file__).resolve()), *sys.argv[1:]],
        environment,
    )


try:
    import yaml  # type: ignore[import-untyped]

    from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides
except ModuleNotFoundError:
    _reexec_with_cafe_python()
    raise


_STATUSES = {
    "pending",
    "in_progress",
    "completed",
    "returned",
    "awaiting_confirmation",
    "skipped",
    "blocked",
    "unknown",
}
_SYMBOLS = {
    "pending": "○",
    "in_progress": "▶",
    "completed": "✓",
    "returned": "↩",
    "awaiting_confirmation": "⏸",
    "skipped": "−",
    "blocked": "!",
    "unknown": "？",
}
_TEXT = {
    "zh": {
        "missing": "流程尚未建立",
        "iteration": "第 {iteration} 輪",
        "review": "driver 主動審查",
        "confirmation": "使用者確認",
        "delegable": "driver 可代理",
        "not_delegable": "driver 不可代理",
        "closeout": "收尾",
        "routes": "分支",
        "legend": {
            "pending": "待執行",
            "in_progress": "進行中",
            "completed": "已完成",
            "returned": "已退回",
            "awaiting_confirmation": "等待確認",
            "skipped": "已略過",
            "blocked": "受阻",
            "unknown": "狀態未知",
        },
    },
    "en": {
        "missing": "Workflow has not been established.",
        "iteration": "iteration {iteration}",
        "review": "driver proactive review",
        "confirmation": "user confirmation",
        "delegable": "driver may act",
        "not_delegable": "driver may not act",
        "closeout": "closeout",
        "routes": "routes",
        "legend": {
            "pending": "Pending",
            "in_progress": "In progress",
            "completed": "Completed",
            "returned": "Returned",
            "awaiting_confirmation": "Awaiting confirmation",
            "skipped": "Skipped",
            "blocked": "Blocked",
            "unknown": "Unknown",
        },
    },
}


def _language(locale: str) -> str:
    token = locale.strip().lower().replace("_", "-")
    return "zh" if token == "zh-tw" or token.startswith("zh-hant") else "en"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return {str(key): item for key, item in value.items()}


def _playbook_mapping(playbook: Any) -> dict[str, Any]:
    if hasattr(playbook, "model_dump"):
        playbook = playbook.model_dump(mode="json")
    result = _mapping(playbook, "playbook")
    if not isinstance(result.get("steps"), Mapping) or not result["steps"]:
        raise ValueError("playbook must declare steps")
    result["steps"] = {
        str(name): _mapping(step, f"steps.{name}") for name, step in result["steps"].items()
    }
    return result


def load_effective_playbook(
    *, project_root: Path, playbook_id: str, issue_dir: Path | None = None
) -> dict[str, Any]:
    """Load the effective graph, including an explicitly selected issue override."""
    playbook = PlaybookLoader(project_root=project_root).load(playbook_id)
    if issue_dir is not None and (issue_dir / "issue.yaml").is_file():
        playbook = apply_issue_playbook_overrides(playbook, issue_dir / "issue.yaml")
    return _playbook_mapping(playbook)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def _load_contract(issue_dir: Path) -> dict[str, Any] | None:
    raw = _read_json(issue_dir / "driver" / "contract.json")
    if raw is None:
        return None
    policy = raw.get("policy", raw)
    return _mapping(policy, "driver contract policy")


def _status(value: Any, label: str) -> str:
    if not isinstance(value, str) or value not in _STATUSES:
        raise ValueError(f"invalid progress status for {label}: {value!r}")
    return value


def _required_reviews(contract: Mapping[str, Any], steps: set[str]) -> set[str]:
    proactive = contract.get("proactive_review", {})
    if not isinstance(proactive, Mapping):
        return set()
    decisions = proactive.get("phase_decisions", [])
    if not isinstance(decisions, list):
        return set()
    required: set[str] = set()
    for item in decisions:
        if not isinstance(item, Mapping):
            continue
        phase = item.get("phase")
        if phase in steps and item.get("decision") == "required":
            required.add(str(phase))
    return required


def _confirmation_contract(contract: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    raw = contract.get("confirmation_contract", {})
    if not isinstance(raw, Mapping):
        return set(), set(), set()

    def values(name: str) -> set[str]:
        value = raw.get(name, [])
        return {str(item) for item in value} if isinstance(value, list) else set()

    return values("user_required"), values("driver_confirmable"), values("mandatory_human_stops")


def _driver_progress(
    driver_state: Mapping[str, Any] | None,
    *,
    required_reviews: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    if driver_state is None:
        return {}, {}
    state = _mapping(driver_state, "driver state")
    unexpected = set(state) - {"proactive_review", "deliver", "close"}
    if unexpected:
        field = sorted(unexpected)[0]
        raise ValueError(f"driver state cannot override runtime phase '{field}'")
    review_raw = state.get("proactive_review", {})
    reviews = _mapping(review_raw, "proactive_review")
    unknown = set(reviews) - required_reviews
    if unknown:
        raise ValueError(f"unknown proactive review phase: {sorted(unknown)[0]}")
    normalized_reviews = {
        phase: _status(value, f"proactive_review.{phase}") for phase, value in reviews.items()
    }
    closeout = {name: _status(state[name], name) for name in ("deliver", "close") if name in state}
    return normalized_reviews, closeout


def _runtime_progress(
    issue_dir: Path | None, playbook: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, int], list[tuple[str, str]]]:
    steps = list(playbook["steps"])
    statuses = {step: "pending" for step in steps}
    iterations: dict[str, int] = {}
    returns: list[tuple[str, str]] = []
    terminal_evidence: set[str] = set()
    if issue_dir is None:
        return statuses, iterations, returns
    blackboard = _read_json(issue_dir / "blackboard.json")
    if blackboard is None:
        return statuses, iterations, returns
    events = blackboard.get("events", [])
    if not isinstance(events, list):
        raise ValueError("blackboard events must be a list")
    workflow_finished = str(blackboard.get("current_step", "")) == "done"
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("event_type", event.get("type", "")))
        data = event.get("data", {})
        data = data if isinstance(data, Mapping) else {}
        step = str(data.get("step", event.get("step", "")))
        if step in statuses:
            raw_iteration = data.get("iteration", data.get("attempt"))
            if isinstance(raw_iteration, int) and raw_iteration > 0:
                iterations[step] = max(iterations.get(step, 0), raw_iteration)
            if event_type == "step_started":
                statuses[step] = "in_progress"
                terminal_evidence.discard(step)
            elif event_type in {"step_completed", "single_step_completed"}:
                statuses[step] = "completed"
                terminal_evidence.discard(step)
            elif event_type in {"step_skipped", "workflow_step_skipped"}:
                statuses[step] = "skipped"
                terminal_evidence.add(step)
            elif event_type == "workflow_blocked" or (
                event_type in {"workflow_paused", "workflow_interruption"}
                and str(data.get("status_code", "")).upper() == "INTERRUPTED"
            ):
                statuses[step] = "blocked"
                terminal_evidence.add(step)
        if event_type == "transition":
            source, target = str(data.get("from", "")), str(data.get("to", ""))
            transition_intent = str(data.get("transition_intent", ""))
            status_code = str(data.get("status_code", "")).lower()
            if (
                source in statuses
                and target in statuses
                and source != target
                and transition_intent == "manual_handoff"
                and status_code in {"needs_changes", "rejected"}
            ):
                edge = (source, target)
                if edge not in returns:
                    returns.append(edge)
        if event_type == "workflow_completed":
            workflow_finished = True
    handoff = blackboard.get("handoff_contract", {})
    if isinstance(handoff, Mapping):
        target = str(handoff.get("to_step", ""))
        if target in statuses and str(handoff.get("status_code", "")).upper() in {
            "INTERRUPTED",
            "BLOCKED",
        }:
            statuses[target] = "blocked"
            terminal_evidence.add(target)
    for step in steps:
        step_dir = issue_dir / step
        if not step_dir.is_dir():
            continue
        candidates = [
            (int(candidate.name.removeprefix("iteration_")), candidate)
            for candidate in step_dir.glob("iteration_*")
            if candidate.name.removeprefix("iteration_").isdigit()
        ]
        if not candidates:
            continue
        iteration, candidate = max(candidates, key=lambda item: item[0])
        iterations[step] = max(iterations.get(step, 0), iteration)
        metadata = _read_json(candidate / "iteration.json")
        if metadata is None:
            continue
        code = str(metadata.get("status_code", "")).upper()
        if code == "SKIPPED":
            statuses[step] = "skipped"
            terminal_evidence.add(step)
        elif code in {"INTERRUPTED", "BLOCKED"}:
            statuses[step] = "blocked"
            terminal_evidence.add(step)
        elif metadata.get("end_time") and code and step not in terminal_evidence:
            statuses[step] = "completed"
    if workflow_finished:
        statuses = {
            step: "skipped" if status == "pending" else status for step, status in statuses.items()
        }
    return statuses, iterations, returns


def _confirmation_statuses(
    issue_dir: Path | None,
    gate_steps: set[str],
    phase_iterations: Mapping[str, int],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    statuses = {step: "pending" for step in gate_steps}
    returns: list[tuple[str, str]] = []
    if issue_dir is None:
        return statuses, returns
    records = _read_json(issue_dir / "human_tasks.json")
    if records is None:
        return statuses, returns
    tasks = records.get("tasks", [])
    results = records.get("results", [])
    if not isinstance(tasks, list) or not isinstance(results, list):
        raise ValueError("human task records must contain task and result lists")
    result_by_task = {
        str(result.get("task_id")): result
        for result in results
        if isinstance(result, Mapping) and result.get("task_id")
    }
    latest: dict[str, Mapping[str, Any]] = {}
    for task in tasks:
        if not isinstance(task, Mapping) or task.get("trigger") != "confirm_output":
            continue
        step = str(task.get("step", ""))
        if step not in gate_steps:
            continue
        iteration = task.get("iteration", 0)
        prior = latest.get(step)
        prior_iteration = prior.get("iteration", 0) if prior is not None else 0
        if isinstance(iteration, int) and iteration >= prior_iteration:
            latest[step] = task
    for step, task in latest.items():
        task_status = str(task.get("status", ""))
        task_iteration = task.get("iteration", 0)
        if task_status == "pending":
            statuses[step] = "awaiting_confirmation"
        elif task_status == "configuration_error":
            statuses[step] = "blocked"
        elif task_status != "completed":
            statuses[step] = "unknown"
        else:
            result = result_by_task.get(str(task.get("id", "")), {})
            payload = result.get("payload", {}) if isinstance(result, Mapping) else {}
            payload = payload if isinstance(payload, Mapping) else {}
            decision = str(payload.get("decision", ""))
            expected = task.get("expected_result", {})
            expected = expected if isinstance(expected, Mapping) else {}
            decisions = expected.get("decisions", [])
            matched = (
                next(
                    (
                        item
                        for item in decisions
                        if isinstance(item, Mapping) and item.get("id") == decision
                    ),
                    None,
                )
                if isinstance(decisions, list)
                else None
            )
            continuations = task.get("continuations", {})
            declared_target = (
                continuations.get(decision) if isinstance(continuations, Mapping) else None
            )
            payload_target = payload.get("continuation")
            outcome_is_valid = (
                matched is not None
                and isinstance(declared_target, str)
                and bool(declared_target)
                and (payload_target is None or payload_target == declared_target)
            )
            if not outcome_is_valid:
                statuses[step] = "unknown"
                if (
                    isinstance(task_iteration, int)
                    and phase_iterations.get(step, 0) > task_iteration
                ):
                    statuses[step] = "pending"
                continue
            correction = matched.get("correction") is True
            if correction:
                returns.append((step, declared_target))
                statuses[step] = "returned"
            else:
                statuses[step] = "completed"
        if isinstance(task_iteration, int) and phase_iterations.get(step, 0) > task_iteration:
            statuses[step] = "pending"
    return statuses, returns


def _line(status: str, label: str) -> str:
    return f"{_SYMBOLS[status]} {label}"


def _branch_lines(playbook: Mapping[str, Any], step: str, steps: Sequence[str]) -> list[str]:
    raw = playbook["steps"][step].get("on", {})
    if not isinstance(raw, Mapping):
        return []
    edges = [
        (str(intent), "done" if target in {"_done", "done"} else str(target))
        for intent, target in raw.items()
        if target != step
    ]
    allowed_goto = playbook["steps"][step].get("allowed_goto", [])
    if isinstance(allowed_goto, list):
        declared_targets = {target for _, target in edges}
        edges.extend(
            ("goto", str(target))
            for target in allowed_goto
            if isinstance(target, str) and target not in declared_targets
        )
    return [
        f"  {'└─' if index == len(edges) - 1 else '├─'} {intent} → {target}"
        for index, (intent, target) in enumerate(edges)
    ]


def render_progress(
    *,
    playbook: Any | None = None,
    contract: Mapping[str, Any] | None = None,
    locale: str = "en",
    issue_dir: Path | None = None,
    driver_state: Mapping[str, Any] | None = None,
    include_closeout: Sequence[str] = (),
) -> str:
    """Render progress without creating, resuming, or mutating workflow state."""
    language = _language(locale)
    text = _TEXT[language]
    if playbook is None:
        return str(text["missing"])
    model = _playbook_mapping(playbook)
    steps = list(model["steps"])
    policy = _mapping(contract or {}, "contract")
    required_reviews = _required_reviews(policy, set(steps))
    reviews, closeout = _driver_progress(driver_state, required_reviews=required_reviews)
    invalid_closeout = set(include_closeout) - {"deliver", "close"}
    if invalid_closeout:
        raise ValueError(f"unknown closeout item: {sorted(invalid_closeout)[0]}")
    phase_statuses, iterations, runtime_returns = _runtime_progress(issue_dir, model)
    user_required, driver_confirmable, mandatory = _confirmation_contract(policy)
    gate_steps = (user_required | driver_confirmable | mandatory) & set(steps)
    confirmation_statuses, task_returns = _confirmation_statuses(issue_dir, gate_steps, iterations)

    nodes: list[str] = []
    for step in steps:
        label = step
        if iterations.get(step, 0) > 1:
            label += " · " + str(text["iteration"]).format(iteration=iterations[step])
        phase_block = [_line(phase_statuses[step], label), *_branch_lines(model, step, steps)]
        if step in required_reviews:
            review_status = reviews.get(step, "unknown")
            review_label = (
                f"{step}：{text['review']}" if language == "zh" else f"{step}: {text['review']}"
            )
            phase_block.append(_line(review_status, review_label))
        if step in gate_steps:
            proxy = text["delegable"] if step in driver_confirmable else text["not_delegable"]
            confirmation_label = (
                f"{step}：{text['confirmation']}（{proxy}）"
                if language == "zh"
                else f"{step}: {text['confirmation']} ({proxy})"
            )
            phase_block.append(
                _line(
                    confirmation_statuses[step],
                    confirmation_label,
                )
            )
        nodes.append("\n".join(phase_block))
    for source, target in [*runtime_returns, *task_returns]:
        return_line = _line("returned", f"{source} → {target}")
        if return_line not in nodes:
            nodes.append(return_line)
    for item in include_closeout:
        nodes.append(
            _line(
                closeout.get(item, "unknown"),
                (
                    f"{item}（{text['closeout']}）"
                    if language == "zh"
                    else f"{item} ({text['closeout']})"
                ),
            )
        )
    body = "\n\n".join(nodes)
    legend = text["legend"]
    legend_lines = [
        "　".join(
            f"{_SYMBOLS[name]} {legend[name]}"
            for name in ("pending", "in_progress", "completed", "returned")
        ),
        "　".join(
            f"{_SYMBOLS[name]} {legend[name]}"
            for name in ("awaiting_confirmation", "skipped", "blocked", "unknown")
        ),
    ]
    return body + "\n\n" + "\n".join(legend_lines)


def _json_argument(value: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(value), "driver state")
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"must be valid JSON: {exc.msg}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--playbook")
    parser.add_argument("--issue-dir", type=Path)
    parser.add_argument("--locale", default="en")
    parser.add_argument("--driver-state", type=_json_argument)
    parser.add_argument("--show-deliver", action="store_true")
    parser.add_argument("--show-close", action="store_true")
    return parser


def _issue_playbook_id(issue_dir: Path) -> str | None:
    blackboard = _read_json(issue_dir / "blackboard.json")
    if blackboard is not None and isinstance(blackboard.get("playbook_id"), str):
        return str(blackboard["playbook_id"])
    issue_config = issue_dir / "issue.yaml"
    if issue_config.is_file():
        raw = yaml.safe_load(issue_config.read_text(encoding="utf-8")) or {}
        if isinstance(raw, Mapping) and isinstance(raw.get("playbook_id"), str):
            return str(raw["playbook_id"])
    return None


def main() -> int:
    try:
        args = _parser().parse_args()
        issue_dir = args.issue_dir.resolve() if args.issue_dir is not None else None
        playbook_id = args.playbook or (
            _issue_playbook_id(issue_dir) if issue_dir is not None else None
        )
        if playbook_id is None:
            print(render_progress(locale=args.locale))
            return 0
        playbook = load_effective_playbook(
            project_root=args.project_root.resolve(),
            playbook_id=playbook_id,
            issue_dir=issue_dir,
        )
        contract = _load_contract(issue_dir) if issue_dir is not None else None
        if issue_dir is not None and contract is None:
            print(render_progress(locale=args.locale))
            return 0
        include_closeout = tuple(
            name
            for name, enabled in (("deliver", args.show_deliver), ("close", args.show_close))
            if enabled
        )
        print(
            render_progress(
                playbook=playbook,
                contract=contract,
                locale=args.locale,
                issue_dir=issue_dir,
                driver_state=args.driver_state,
                include_closeout=include_closeout,
            )
        )
    except (FileNotFoundError, LookupError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

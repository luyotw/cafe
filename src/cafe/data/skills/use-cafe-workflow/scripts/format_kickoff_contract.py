#!/usr/bin/env python3
"""Render a validated CAFE kickoff contract as Markdown tables."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable


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
    import yaml

    from cafe.core.playbook import confirmation_gate_steps
    from cafe.playbooks.loader import PlaybookLoader
except ModuleNotFoundError:
    _reexec_with_cafe_python()
    raise


def _items(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or ():
        for item in value.split(","):
            token = item.strip()
            if token and token not in result:
                result.append(token)
    return result


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


def _skill_name(value: str | dict[str, str]) -> str:
    if isinstance(value, str):
        return value
    return ", ".join(f"{key}: {skill}" for key, skill in value.items())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Format a validated CAFE kickoff contract as Markdown tables."
    )
    parser.add_argument("playbook_id")
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--effective-locale")
    parser.add_argument("--locale-source")
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
    loaded = PlaybookLoader().load_model(args.playbook_id)
    model = loaded.model
    candidates = confirmation_gate_steps(model)
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
    mandate, mandate_source = _load_strategic_context(
        args.strategic_context,
        args.issue_name,
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
        no_gate = "—"
        user_owner = "user"
        driver_owner = "driver（驗證後繼續）"
        reactive_title = "### Reactive user handoffs"
        reactive_headers = ["Intent", "Policy", "是否為排程 gate"]
        mandate_title = "### Mandate"
        mandate_headers = ["Axis", "Level", "Grounds"]
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
        no_gate = "—"
        user_owner = "user"
        driver_owner = "driver (verify, then continue)"
        reactive_title = "### Reactive user handoffs"
        reactive_headers = ["Intent", "Policy", "Scheduled gate"]
        mandate_title = "### Mandate"
        mandate_headers = ["Axis", "Level", "Grounds"]

    summary = _table(
        summary_headers,
        [
            ["playbook_id", args.playbook_id],
            ["playbook_source", f"{loaded.source}: {loaded.path}"],
            ["configured_locale", configured_locale],
            ["effective_locale", f"{effective_locale} ({locale_source})"],
            ["user_required", ", ".join(user_required) or "[]"],
            ["driver_confirmable", ", ".join(driver_confirmable) or "[]"],
            ["worktree", worktree],
            ["mandate_source", mandate_source],
        ],
    )

    phase_rows = []
    for step_name, step in model.steps.items():
        if step_name in user_required:
            gate, owner, stop = yes, user_owner, yes
        elif step_name in driver_confirmable:
            gate, owner, stop = yes, driver_owner, no
        else:
            gate, owner, stop = no, no_gate, no
        phase_rows.append([step_name, step.role, _skill_name(step.skill), gate, owner, stop])

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
            ["playbook_id", mandate.get("playbook_id", "—")],
            ["out_of_mandate", out_of_mandate or "[]"],
        ],
    )

    return "\n\n".join(
        [
            title,
            summary,
            "### Phases",
            _table(phase_headers, phase_rows),
            reactive_title,
            reactive,
            mandate_title,
            mandate_summary,
            _table(mandate_headers, mandate_rows),
        ]
    )


def main() -> int:
    try:
        print(render(_parser().parse_args()))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

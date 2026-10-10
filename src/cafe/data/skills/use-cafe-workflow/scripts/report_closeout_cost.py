#!/usr/bin/env python3
"""Offer a human-owned preference after verified closeout; report only on yes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from cafe.core.runtime_locales import render_text
from cafe.manager.costs import (
    _archive,
    format_summary,
    inclusive_report,
    preserve_worker_cost,
    quiescent_worker,
    read_accounting_file,
    validate_issue_identity,
)

CATALOG = Path(__file__).resolve().parents[1] / "locales"


def _text(key, locale, **fields):
    return render_text(f"cost.{key}", locale=locale, catalog_root=CATALOG, **fields)


def _effect_complete(argv, project_root, worktree, issue_dir, archive):
    if len(argv) > 1 and Path(argv[1]).name == "cleanup_worktree.py":
        script = Path(argv[1])
        bundled = Path(__file__).with_name("cleanup_worktree.py")
        if not script.is_file() or script.read_bytes() != bundled.read_bytes():
            return False
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--project-root", type=Path, required=True)
        parser.add_argument("--worktree", type=Path, required=True)
        parser.add_argument("--issue-name", required=True)
        parser.add_argument("--remote")
        try:
            args = parser.parse_args(argv[2:])
        except SystemExit:
            return False
        if (args.project_root.resolve() != Path(project_root).resolve()
                or args.worktree.absolute() != Path(worktree).absolute()
                or args.issue_name != Path(issue_dir).name
                or not archive.is_dir() or Path(issue_dir).exists() or args.worktree.exists()):
            return False
        listing = subprocess.run(["git", "-C", str(project_root), "worktree", "list", "--porcelain"],
                                 check=True, capture_output=True, text=True).stdout
        local = subprocess.run(["git", "-C", str(project_root), "branch", "--list", args.issue_name],
                               check=True, capture_output=True, text=True).stdout
        if f"worktree {args.worktree}\n" in listing or local.strip():
            return False
        if args.remote:
            refs = subprocess.run(["git", "-C", str(project_root), "ls-remote", "--heads", args.remote,
                                   f"refs/heads/{args.issue_name}"], check=True, capture_output=True, text=True)
            if refs.stdout.strip():
                return False
        return True
    if argv[:3] == ["gh", "issue", "close"]:
        if len(argv) < 4 or argv[3].startswith("-"):
            raise ValueError("cleanup issue target is not explicit")
        query = ["gh", "issue", "view", argv[3], "--json", "state"]
        if "--repo" in argv:
            index = argv.index("--repo")
            query.extend(["--repo", argv[index + 1]])
        result = subprocess.run(
            query,
            cwd=worktree if Path(worktree).exists() else project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout).get("state") == "CLOSED"
    if argv[0] == "git":
        args = argv[1:]
        target_root = Path(worktree)
        if args[:1] == ["-C"]:
            target_root, args = Path(args[1]), args[2:]
        if args[:2] == ["branch", "-d"] and len(args) == 3:
            result = subprocess.run(
                ["git", "-C", str(target_root), "branch", "--list", args[2]],
                check=True,
                capture_output=True,
                text=True,
            )
            return not result.stdout.strip()
        if args[:2] == ["worktree", "remove"]:
            targets = [arg for arg in args[2:] if arg not in {"--force", "-f"}]
            if len(targets) != 1 or targets[0].startswith("-"):
                return False
            target = Path(targets[0])
            if not target.is_absolute():
                target = target_root / target
            listing = subprocess.run(
                ["git", "-C", str(project_root), "worktree", "list", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return not target.exists() and f"worktree {target.absolute()}\n" not in listing
    if argv[:2] == ["cafe", "close"]:
        return archive.is_dir() and not Path(issue_dir).exists()
    # Unknown external effects require the existing human recovery route.
    return False


def verify_closeout(project_root, issue_name, workflow_id, issue_dir, operation, archive_dir=None):
    """Use existing outcomes plus observable lifecycle effects, never a caller flag."""
    from execute_closeout import _paths, _read, _read_locked

    if operation not in {"cleanup", "archive"}:
        raise ValueError("leave does not qualify for the cost preference")
    archive = _archive(project_root, issue_name)
    if archive_dir is not None and Path(archive_dir).absolute() != archive.absolute():
        raise ValueError("archive path differs from this project's lifecycle archive")
    if operation == "cleanup":
        path, lock = _paths(project_root, issue_name, workflow_id)
        with _read_locked(lock):
            receipt = _read(path, issue_name=issue_name, workflow_id=workflow_id)
        if receipt is None:
            raise ValueError("cleanup evidence is missing")
        target = Path(receipt["worktree"]) / ".cafe/issues" / issue_name
        if Path(issue_dir).absolute() != target.absolute():
            raise ValueError("cleanup issue path differs from the receipt target")
        commands = receipt["commands"].get("cleanup", [])
        if not commands or any(
            c["status"] != "succeeded" or c["returncode"] != 0 for c in commands
        ):
            raise ValueError("cleanup has not verifiably completed")
        if any(
            c["status"] != "succeeded" or c["returncode"] != 0
            for c in receipt["commands"].get("deliver", [])
        ):
            raise ValueError("delivery outcomes are not complete")
        if Path(issue_dir).exists() or Path(issue_dir).is_symlink():
            validate_issue_identity(issue_dir, issue_name, workflow_id)
        if not all(
            _effect_complete(c["argv"], project_root, receipt["worktree"], issue_dir, archive)
            for c in commands
        ):
            raise ValueError("cleanup external state is not complete")
        for command in commands:
            argv = command["argv"]
            if "worktree" in argv and "remove" in argv:
                worktrees = subprocess.run(
                    ["git", "-C", str(project_root), "worktree", "list", "--porcelain"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                if f"worktree {receipt['worktree']}\n" in worktrees:
                    raise ValueError("cleanup target remains a registered worktree")
    else:
        if Path(issue_dir).exists() or Path(issue_dir).is_symlink():
            raise ValueError("archive operation has not moved the active issue")
        if not archive.exists():
            raise ValueError("completed lifecycle archive is missing")
    if archive.exists() or archive.is_symlink():
        validate_issue_identity(archive, issue_name, workflow_id)
        if (
            operation == "archive"
            and read_accounting_file(archive / "blackboard.json").get("current_step") != "done"
        ):
            raise ValueError("archive does not prove completed workflow")
        contract = archive / "manager/contract.json"
        if contract.exists():
            policy = read_accounting_file(contract)
            if ("closeout_contract" in policy or
                    policy.get("delivery_contract", {}).get("terminal_selection") == "delivery_outcome"):
                from inspect_delivery_closeout import inspect

                result = inspect(archive, workflow_id, project_root=project_root)
                if result["status"] != "accepted" or result["selection"]["choice"] != operation:
                    raise ValueError("closeout differs from accepted terminal selection")
                if "closeout_contract" in policy:
                    from cafe.manager.closeout import execution_plan
                    path, lock = _paths(project_root, issue_name, workflow_id)
                    with _read_locked(lock):
                        evidence = _read(path, issue_name=issue_name, workflow_id=workflow_id)
                    if (evidence is None or evidence["contract_sha256"] != result["selection"]["contract_sha256"]
                            or [c["argv"] for c in evidence["commands"]["cleanup"]]
                            != [c["argv"] for c in execution_plan(policy)["cleanup"]]
                            or any(c["status"] != "succeeded" or c["returncode"] != 0
                                   for c in evidence["commands"]["cleanup"])):
                        raise ValueError("Manager closeout receipt differs from its confirmed selection")
        return archive
    return Path(issue_dir) if Path(issue_dir).exists() else None


def closeout_cost(
    project_root,
    issue_name,
    workflow_id,
    issue_dir,
    *,
    operation="cleanup",
    archive_dir=None,
    choice=None,
    locale="en-US",
):
    archive = verify_closeout(
        project_root, issue_name, workflow_id, issue_dir, operation, archive_dir
    )
    if choice is None:
        return dict(
            status="offered",
            prompt=_text("question", locale),
            options=[
                dict(value="yes", label=_text("yes", locale)),
                dict(value="no", label=_text("no", locale)),
            ],
        )
    if choice == "no":
        return dict(status="declined", text=_text("declined", locale))
    if choice != "yes":
        raise ValueError("cost preference requires an explicit human yes or no")
    report = inclusive_report(project_root, issue_name, workflow_id, issue_dir=archive)
    lines = [_text("cutoff", locale, captured_at=report["captured_at"])]
    for key in ("worker", "manager", "combined"):
        label = "combined_known" if key == "combined" and report[key]["incomplete"] else key
        lines.append(_text(label, locale, cost=format_summary(report[key], locale)))
    if "native_usage" in report["combined"]:
        from cafe.core.native_accounting import format_native_usage
        from cafe.core.runtime_locales import load_catalogs, select_text_locale

        catalog = load_catalogs(CATALOG)[select_text_locale(locale)]
        templates = {
            key: catalog["cost.native." + key]
            for key in (
                "child",
                "child_subtotal",
                "caller_subtotal",
                "unknown",
                "complete",
                "partial",
                "combined_unavailable",
            )
        }
        lines.extend(format_native_usage(report["combined"]["native_usage"], templates=templates))
    lines.append(_text("limitations", locale))
    return dict(status="reported", report=report, text="\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--operation", choices=("cleanup", "archive"), default="cleanup")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--choice", choices=("yes", "no"))
    parser.add_argument("--locale", choices=("en-US", "zh-TW"), default="en-US")
    parser.add_argument("--preserve", action="store_true")
    args = parser.parse_args()
    try:
        if args.preserve:
            if args.choice is not None:
                raise ValueError("preservation cannot answer the cost preference")
            with quiescent_worker(args.issue_dir):
                snapshot = preserve_worker_cost(
                    args.project_root, args.issue_dir, args.issue_name, args.workflow_id
                )
            result = dict(status="preserved", captured_at=snapshot["captured_at"])
        else:
            result = closeout_cost(
                args.project_root,
                args.issue_name,
                args.workflow_id,
                args.issue_dir,
                operation=args.operation,
                archive_dir=args.archive_dir,
                choice=args.choice,
                locale=args.locale,
            )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

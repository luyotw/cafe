#!/usr/bin/env python3
"""Discover, assemble, inspect and render reusable kickoff inputs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _kickoff_store import VersionedJsonStore, repository_identity, _lock
import kickoff_inputs
import kickoff_preferences


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _default_config_dir() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".config"
    return base / "cafe" / "kickoff"


def _default_cache_dir() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return base / "cafe" / "kickoff" / "v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="Show request fields and a partial starter; performs no discovery.")
    capture = commands.add_parser("capture-report", allow_abbrev=False,
                                  help="Capture one existing check's JSON stdin and reference it in an editable request; executes no checks.")
    capture.add_argument("--request-file", type=Path, required=True)
    capture.add_argument("--kind", choices=("update", "catalog"), required=True)
    capture.add_argument("--report-output", type=Path, required=True)
    capture.add_argument("--checked-at", required=True, help="Actual check observation timestamp; never inferred from capture time.")
    for name in ("stores", "discover", "assemble", "render"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--request-file", type=Path, required=True)
        command.add_argument("--config-dir", type=Path, default=_default_config_dir())
        command.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
        if name in {"stores", "discover", "assemble"}:
            command.add_argument("--summary", nargs="?", const="json", choices=("json", "decisions"),
                                 help="Legacy JSON summary, or directly readable current decisions with inspect references.")
        if name == "assemble":
            command.add_argument("--guidance-output", type=Path, help="Write current owner sections as plain text and return disjoint line references, without duplicating the body in JSON.")
            command.add_argument("--with-guidance", action="store_true", help="Include current kickoff policy sections once, with source fingerprints.")
            command.add_argument("--draft-output", type=Path, help="Write an editable request with owner-typed product fields and unresolved action slots.")
        if name == "render":
            command.add_argument("--output", type=Path, help="Write the complete formatter text and print a compact receipt.")
    preferences = commands.add_parser("preferences", allow_abbrev=False).add_subparsers(
        dest="operation", required=True
    )
    inspect = preferences.add_parser("inspect", allow_abbrev=False)
    inspect.add_argument("--scope", choices=("user", "repository"), required=True)
    inspect.add_argument("--project-root", type=Path)
    inspect.add_argument("--config-dir", type=Path, default=_default_config_dir())
    set_parser = preferences.add_parser("set", allow_abbrev=False)
    set_parser.add_argument("--scope", choices=("user", "repository"), required=True)
    set_parser.add_argument("--key", required=True)
    set_parser.add_argument("--value-json", required=True)
    set_parser.add_argument("--origin", choices=("explicit", "inferred"), default="explicit")
    set_parser.add_argument("--reuse", action="store_true")
    set_parser.add_argument("--project-root", type=Path)
    set_parser.add_argument("--config-dir", type=Path, default=_default_config_dir())
    clear = preferences.add_parser("clear", allow_abbrev=False)
    clear.add_argument("--scope", choices=("user", "repository"), required=True)
    clear.add_argument("--key", required=True)
    clear.add_argument("--project-root", type=Path)
    clear.add_argument("--config-dir", type=Path, default=_default_config_dir())
    evidence = commands.add_parser("evidence", allow_abbrev=False).add_subparsers(
        dest="operation", required=True
    )
    for operation in ("inspect", "refresh", "clear"):
        item = evidence.add_parser(operation, allow_abbrev=False)
        item.add_argument("--category", choices=("catalog", "delivery", "models"), required=True)
        item.add_argument("--project-root", type=Path, default=Path.cwd())
        item.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
        item.add_argument("--key")
        if operation == "refresh":
            item.add_argument("--evidence-file", type=Path)
            item.add_argument("--request-file", type=Path)
    return parser


def _request_command(args: argparse.Namespace) -> int:
    try:
        request = _read_json(args.request_file)
        if not isinstance(request, dict):
            raise ValueError("request file must contain an object")
        storage = {
            "config_dir": str(args.config_dir.expanduser().resolve()),
            "cache_dir": str(args.cache_dir.expanduser().resolve()),
            "repository_identity": repository_identity(Path(request.get("project_root", Path.cwd()))),
        }
        args.config_dir = Path(storage["config_dir"])
        args.cache_dir = Path(storage["cache_dir"])

        def continuation(command: str, request_file: Path) -> list[str]:
            return [sys.executable, str(Path(__file__).resolve()), command,
                    "--request-file", str(request_file.resolve()),
                    "--config-dir", storage["config_dir"], "--cache-dir", storage["cache_dir"]]

        if args.command == "stores":
            stage = "assemble" if request.get("playbook_id") else "discover"
            view = ["--summary", "decisions"] if args.summary == "decisions" else ["--summary"]
            _json({"storage": storage, "next_command": continuation(stage, args.request_file) + view})
            return 0
        summary_mode = getattr(args, "summary", False)
        inspect_references = {
            "catalog": [
                sys.executable, str(Path(__file__).resolve()), "discover",
                "--request-file", str(args.request_file.resolve()),
                "--config-dir", str(args.config_dir.resolve()), "--cache-dir", str(args.cache_dir.resolve()),
            ],
            "delivery": [
                sys.executable, str(Path(__file__).resolve()), "evidence", "inspect",
                "--category", "delivery", "--project-root",
                str(Path(request.get("project_root", Path.cwd())).resolve()),
                "--cache-dir", str(args.cache_dir.resolve()),
            ],
            "models": [
                sys.executable, str(Path(__file__).resolve()), "evidence", "inspect",
                "--category", "models", "--project-root",
                str(Path(request.get("project_root", Path.cwd())).resolve()),
                "--cache-dir", str(args.cache_dir.resolve()),
            ],
        }
        if args.command == "discover":
            report = kickoff_inputs.discover_kickoff(
                request, config_dir=args.config_dir, cache_dir=args.cache_dir,
                include_provenance=summary_mode,
            )
            result = kickoff_inputs.compact_discovery_summary(
                report, inspect_references=inspect_references
            ) if summary_mode else report
            _json({**result, "storage": storage})
            return 0 if report.get("status") in {"ready", "partial"} else 2
        discovery = kickoff_inputs.discover_kickoff(
            request, config_dir=args.config_dir, cache_dir=args.cache_dir,
            include_provenance=summary_mode,
        )
        assembled = kickoff_inputs.assemble_kickoff(
            request,
            preference_store=kickoff_preferences.PreferenceStore(
                args.config_dir,
                repository_root=Path(request.get("project_root", Path.cwd())),
            ),
            discovery=discovery,
        )
        active_draft = getattr(args, "draft_output", None) or args.request_file
        followup = {"request_file": str(active_draft.resolve()), "checks": [],
                    "render_command": continuation("render", active_draft),
                    "complete_endpoint": False,
                    "report_decisions": "Read each complete captured report; set preflight_metadata.<kind>.decision and post_change_evidence from current facts before render. A manual draft is not a completed contract."}
        raw_fields = request.get("formatter_inputs", {})
        explicit = request.get("current_explicit_inputs", {})
        report_files = request.get("preflight_files", {})
        for kind in ("update", "catalog"):
            if (isinstance(raw_fields, dict) and raw_fields.get(kind + "_preflight") is not None
                    or isinstance(explicit, dict) and explicit.get(kind + "_preflight") is not None
                    or isinstance(report_files, dict) and report_files.get(kind)):
                continue
            followup["checks"].append({
                "kind": kind,
                "check_argv": ["cafe", "update", "check", "--json"] if kind == "update" else
                    [sys.executable, str(_SCRIPT_DIR / "catalog_version_check.py")],
                "capture_argv": [sys.executable, str(Path(__file__).resolve()), "capture-report",
                    "--request-file", str(active_draft.resolve()), "--kind", kind,
                    "--report-output", str(active_draft.resolve().with_name(active_draft.stem + "." + kind + ".json")),
                    "--checked-at", "<actual-checked-at>"],
                "stdin": "Pipe the complete check JSON to capture_argv; supply its actual timezone-qualified observation time. These read-only preparation checks are required even for proposal-only work; they execute no proposed case action.",
                "owner": "references/kickoff.md#complete-runtime-and-catalog-preflight",
            })
        normal_projection = args.command == "assemble" and (
            summary_mode == "decisions" or (args.draft_output is not None
                and args.guidance_output is None and not args.with_guidance))
        if args.command == "assemble":
            draft_request = kickoff_inputs.preparation_template(request, assembled.get("formatter_draft") or {})
            if args.draft_output is not None:
                if args.draft_output.resolve() == args.request_file.resolve():
                    raise ValueError("draft output must differ from the input request")
                args.draft_output.write_text(json.dumps(draft_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if summary_mode:
                compact = kickoff_inputs.compact_discovery_summary(
                    discovery, selected_only=True, inspect_references=inspect_references
                )
                compact.update({
                    "stage": "assembly_summary",
                    "storage": storage,
                    "render_command": continuation("render", args.draft_output or args.request_file),
                    "input_template": draft_request["formatter_inputs"] if assembled.get("status") != "ready" else None,
                    "input_schema": kickoff_inputs.request_schema(),
                    "decision_brief": kickoff_inputs.decision_brief(request, assembled.get("missing_decisions", []), summary=compact, assembled=assembled, indexed=normal_projection),
                    "draft_output": str(args.draft_output.resolve()) if args.draft_output is not None else None,
                    "status": assembled.get("status", "invalid"),
                    "selected_playbook": assembled.get("selected_playbook"),
                    "missing_decisions": assembled.get("missing_decisions", []),
                    "assembly_diagnostics": assembled.get("diagnostics", []),
                    "formatter_inputs": assembled.get("formatter_inputs"),
                    "formatter_draft": assembled.get("formatter_draft") if assembled.get("status") != "ready" else None,
                })
                if args.draft_output is None and summary_mode != "decisions":
                    compact["decision_brief"].pop("field_shapes", None)
                if normal_projection:
                    # Editable data lives in the requested file. Old summary-only
                    # and full assembly consumers retain their complete payload.
                    for key in ("input_template", "formatter_draft", "formatter_inputs", "input_schema"):
                        compact.pop(key, None)
                    compact["schema_reference"] = [sys.executable, str(Path(__file__).resolve()), "schema"]
                    compact["continuation"] = followup
                    kickoff_inputs.index_summary_sources(compact)
                if args.guidance_output is not None:
                    if args.with_guidance:
                        raise ValueError("choose guidance-output or with-guidance, not both")
                    if args.guidance_output.resolve() in {args.request_file.resolve(), args.draft_output.resolve() if args.draft_output else None}:
                        raise ValueError("guidance output must differ from request and draft")
                    sections = kickoff_inputs.kickoff_guidance()
                    text = ""
                    index = []
                    for section in sections:
                        start = len(text.splitlines()) + 1
                        text += section["text"]
                        index.append({key: section[key] for key in ("file", "heading", "sha256")})
                        index[-1].update(start_line=start, end_line=len(text.splitlines()))
                    args.guidance_output.write_text(text, encoding="utf-8")
                    compact["guidance_file"] = str(args.guidance_output.resolve())
                    compact["guidance_index"] = index
                elif args.with_guidance:
                    compact["guidance"] = kickoff_inputs.kickoff_guidance()
                if summary_mode == "decisions":
                    compact = kickoff_inputs.current_decision_view(compact, request, draft_request)
                    print(json.dumps(compact, ensure_ascii=False, indent=2))
                else:
                    _json(compact)
                return 0 if assembled.get("status") == "ready" else 3
            _json({"stage": "assembly", **assembled, "discovery": discovery, "storage": storage, "render_command": continuation("render", args.draft_output or args.request_file)})
            return 0 if assembled.get("status") == "ready" else 3
        if assembled.get("status") != "ready":
            blocked = {"status": assembled.get("status", "invalid"),
                       "diagnostics": assembled.get("diagnostics", []),
                       "missing_decisions": assembled.get("missing_decisions", []), "continuation": followup}
            _json({"stage": "render", **blocked} if args.output is not None else
                  {"stage": "render", "assembly": assembled, "render": blocked})
            return 3
        rendered = kickoff_inputs.render_kickoff(assembled.get("formatter_inputs"))
        if args.output is not None:
            if rendered.get("status") == "rendered":
                args.output.write_text(rendered["output"], encoding="utf-8")
                _json({"stage": "render", "status": "rendered", "output_file": str(args.output.resolve())})
                return 0
            _json({
                "stage": "render", **rendered,
                "assembly_diagnostics": assembled.get("diagnostics", []),
                "missing_decisions": assembled.get("missing_decisions", []),
            })
            return 3
        _json({"stage": "render", "assembly": assembled, "render": rendered})
        return 0 if rendered.get("status") == "rendered" else 3
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        _json({"status": "invalid", "diagnostics": [type(exc).__name__]})
        return 2


def _capture_report(args: argparse.Namespace) -> int:
    """Retain original check bytes and unresolved decisions, without executing checks."""
    from datetime import datetime

    try:
        timestamp = datetime.fromisoformat(args.checked_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("checked_at must include a timezone")
        raw = sys.stdin.read()
        if not isinstance(json.loads(raw), dict):
            raise ValueError("check output must be a JSON object")
        args.request_file = args.request_file.expanduser().resolve()
        with _lock(args.request_file):
            request = _read_json(args.request_file)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            output = args.report_output.expanduser().resolve()
            if output == args.request_file.resolve():
                raise ValueError("report and request paths must differ")
            for key in ("preflight_files", "preflight_metadata", "formatter_inputs", "current_explicit_inputs"):
                if not isinstance(request.get(key, {}), dict):
                    raise ValueError(f"{key} must be an object")
            field = args.kind + "_preflight"
            if field in request.get("formatter_inputs", {}) or field in request.get("current_explicit_inputs", {}):
                raise ValueError("remove the prior inline report explicitly before capturing a replacement")
            request.setdefault("preflight_files", {})[args.kind] = str(output)
            request.setdefault("preflight_metadata", {})[args.kind] = {
                "checked_at": args.checked_at, "decision": None, "post_change_evidence": None,
            }
            output.write_text(raw, encoding="utf-8")
            args.request_file.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _json({"status": "captured", "report_file": str(output), "request_file": str(args.request_file.resolve()),
               "missing_decisions": ["decision", "post_change_evidence"],
               "authority": "Captured data only; existing formatter validates the full report after current decisions."})
        return 0
    except (OSError, ValueError, TypeError) as exc:
        _json({"status": "invalid", "diagnostics": [str(exc)]})
        return 2


def _preference_command(args: argparse.Namespace) -> int:
    try:
        store = kickoff_preferences.PreferenceStore(
            args.config_dir,
            repository_root=getattr(args, "project_root", None),
        )
        if args.operation == "inspect":
            _json({"scope": args.scope, "preferences": store.inspect(scope=args.scope)})
        elif args.operation == "set":
            value = json.loads(args.value_json)
            changed = store.set(
                args.key, value, scope=args.scope, origin=args.origin, reuse=args.reuse
            )
            _json({"stored": changed, "key": args.key, "scope": args.scope})
        else:
            _json({"cleared": store.clear(args.key, scope=args.scope), "key": args.key, "scope": args.scope})
        return 0
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        _json({"status": "invalid", "diagnostics": [type(exc).__name__]})
        return 2


def _evidence_command(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    repo_key = repository_identity(root)
    collection = "candidates" if args.category == "catalog" else "evidence"
    store = VersionedJsonStore(
        args.cache_dir / f"{args.category}-v1.json", schema_version=1, collection=collection
    )
    records = store.read()
    if args.operation == "inspect":
        _json({"category": args.category, "evidence": records if args.key is None else records.get(args.key)})
        return 0
    if args.operation == "clear":
        if args.key is None:
            records.clear()
            cleared = True
        else:
            cleared = records.pop(args.key, None) is not None
        store.write(records)
        _json({"category": args.category, "cleared": cleared})
        return 0
    try:
        if args.category == "catalog":
            if args.request_file is None:
                raise ValueError("catalog refresh requires --request-file")
            report = kickoff_inputs.discover_kickoff(
                _read_json(args.request_file), cache_dir=args.cache_dir
            )
            _json(report)
            return 0 if report.get("status") in {"ready", "partial"} else 2
        if args.evidence_file is None:
            raise ValueError("delivery and model refresh require --evidence-file")
        evidence = _read_json(args.evidence_file)
        if not isinstance(evidence, dict):
            raise ValueError("evidence file must contain an object")
        if args.category == "delivery":
            module = kickoff_inputs._load_local_module("kickoff_delivery")
            refreshed = module.refresh_delivery({}, evidence=evidence, project_root=root, now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
            if not refreshed["refreshed"]:
                _json(refreshed)
                return 3
            key = repo_key
            records[key] = refreshed["record"]
        else:
            module = kickoff_inputs._load_local_module("kickoff_models")
            from datetime import datetime, timezone

            result = module.assess_model_evidence(evidence, now=datetime.now(timezone.utc))
            if result["status"] != "hit":
                _json(result)
                return 3
            key = ":".join((evidence["provider"], evidence["model"], evidence["version"]))
            records[key] = evidence
        store.write(records)
        _json({"category": args.category, "refreshed": True, "key": key})
        return 0
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        _json({"status": "invalid", "diagnostics": [type(exc).__name__]})
        return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "capture-report":
        return _capture_report(args)
    if args.command == "schema":
        _json(kickoff_inputs.request_schema())
        return 0
    if args.command in {"stores", "discover", "assemble", "render"}:
        return _request_command(args)
    if args.command == "preferences":
        return _preference_command(args)
    return _evidence_command(args)


if __name__ == "__main__":
    raise SystemExit(main())

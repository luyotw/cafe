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

from _kickoff_store import VersionedJsonStore, repository_identity
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
    for name in ("discover", "assemble", "render"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--request-file", type=Path, required=True)
        command.add_argument("--config-dir", type=Path, default=_default_config_dir())
        command.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
        if name in {"discover", "assemble"}:
            command.add_argument("--summary", action="store_true")
        if name == "assemble":
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
            _json(
                kickoff_inputs.compact_discovery_summary(
                    report, inspect_references=inspect_references
                ) if summary_mode else report
            )
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
                    "input_template": draft_request["formatter_inputs"] if assembled.get("status") != "ready" else None,
                    "input_schema": kickoff_inputs.request_schema(),
                    "decision_brief": kickoff_inputs.decision_brief(request, assembled.get("missing_decisions", [])),
                    "draft_output": str(args.draft_output.resolve()) if args.draft_output is not None else None,
                    "status": assembled.get("status", "invalid"),
                    "selected_playbook": assembled.get("selected_playbook"),
                    "missing_decisions": assembled.get("missing_decisions", []),
                    "assembly_diagnostics": assembled.get("diagnostics", []),
                    "formatter_inputs": assembled.get("formatter_inputs"),
                    "formatter_draft": assembled.get("formatter_draft") if assembled.get("status") != "ready" else None,
                })
                if args.with_guidance:
                    compact["guidance"] = kickoff_inputs.kickoff_guidance()
                _json(compact)
                return 0 if assembled.get("status") == "ready" else 3
            _json({"stage": "assembly", **assembled, "discovery": discovery})
            return 0 if assembled.get("status") == "ready" else 3
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
    if args.command == "schema":
        _json(kickoff_inputs.request_schema())
        return 0
    if args.command in {"discover", "assemble", "render"}:
        return _request_command(args)
    if args.command == "preferences":
        return _preference_command(args)
    return _evidence_command(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Publish exact CLI helper skills and emit mandatory pre/post-check evidence."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = 1
SUPPORTED_CLIS = ("claude", "codex", "copilot", "cursor", "gemini")
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_CAPTURE_CHARS = 20_000
COMMAND_TIMEOUT_SECONDS = 120
MAX_CATALOG_ITEMS = 2_048
MAX_SCOPE_SKILLS = 64
MAX_SKILL_NAME_CHARS = 100
MAX_TEXT_FIELD_CHARS = 2_000
MAX_LIST_ITEM_CHARS = 1_024
MAX_LIST_TOTAL_CHARS = MAX_CATALOG_ITEMS * MAX_LIST_ITEM_CHARS
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CATALOG_REASONS = {"identical", "content_mismatch", "missing_global", "invalid_global"}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_executable() -> str:
    resolved = shutil.which("cafe")
    if resolved is None:
        raise ValueError("cafe executable is unavailable")
    path = Path(resolved).resolve()
    if not path.is_file():
        raise ValueError(f"cafe executable is unavailable: {path}")
    return str(path)


def _bounded_output(value: str) -> dict[str, Any]:
    if len(value) <= MAX_CAPTURE_CHARS:
        return {"text": value, "truncated": False}
    return {
        "text": value[:MAX_CAPTURE_CHARS],
        "truncated": True,
        "original_chars": len(value),
    }


def _run_command(executable: str, arguments: Sequence[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [executable, *arguments],
            text=True,
            capture_output=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "executable": executable,
            "command": [executable, *arguments],
            "completed_at": _timestamp(),
            "exit_code": None,
            "error": type(exc).__name__,
            "_stdout_raw": "",
            "_stderr_raw": "",
            "stdout": _bounded_output(""),
            "stderr": _bounded_output(""),
        }
    return {
        "executable": executable,
        "command": [executable, *arguments],
        "completed_at": _timestamp(),
        "exit_code": result.returncode,
        "error": None,
        "_stdout_raw": result.stdout,
        "_stderr_raw": result.stderr,
        "stdout": _bounded_output(result.stdout),
        "stderr": _bounded_output(result.stderr),
    }


def _json_payload(command: dict[str, Any], *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(command["_stdout_raw"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must return a JSON object")
    return payload


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _bounded_text(value: object, *, label: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not _is_nonempty_text(value):
        raise ValueError(f"{label} must be a non-empty string")
    assert isinstance(value, str)
    if len(value) > MAX_TEXT_FIELD_CHARS:
        raise ValueError(f"{label} exceeds the receipt field limit")
    return value


def _bounded_string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    if len(value) > MAX_CATALOG_ITEMS:
        raise ValueError(f"{label} exceeds the receipt item limit")
    result: list[str] = []
    total_chars = 0
    for item in value:
        if not _is_nonempty_text(item):
            raise ValueError(f"{label} must contain non-empty strings")
        assert isinstance(item, str)
        if len(item) > MAX_LIST_ITEM_CHARS:
            raise ValueError(f"{label} contains an oversized item")
        total_chars += len(item)
        if total_chars > MAX_LIST_TOTAL_CHARS:
            raise ValueError(f"{label} exceeds the aggregate receipt limit")
        result.append(item)
    return result


def _validate_update(command: dict[str, Any]) -> dict[str, Any]:
    payload = _json_payload(command, label="runtime update check")
    if command["exit_code"] != 0:
        raise ValueError("runtime update check failed")
    required = {
        "status",
        "installed_version",
        "latest_version",
        "release_url",
        "token",
        "error",
    }
    if not required.issubset(payload):
        raise ValueError("runtime update check omitted required evidence")
    status = payload["status"]
    if status not in {"current", "update_available", "unavailable"}:
        raise ValueError("runtime update check returned an unsupported status")
    if status == "unavailable":
        installed_version = _bounded_text(
            payload["installed_version"],
            label="runtime installed_version",
            allow_none=True,
        )
        if any(payload[field] is not None for field in ("latest_version", "release_url", "token")):
            raise ValueError("unavailable runtime evidence contains successful-check fields")
        error = _bounded_text(payload["error"], label="runtime update error")
        latest_version = release_url = token = None
    else:
        installed_version = _bounded_text(
            payload["installed_version"], label="runtime installed_version"
        )
        latest_version = _bounded_text(payload["latest_version"], label="runtime latest_version")
        release_url = _bounded_text(payload["release_url"], label="runtime update release_url")
        if not _is_sha256(payload["token"]):
            raise ValueError("runtime update token must be a SHA-256 value")
        token = payload["token"]
        if payload["error"] is not None:
            raise ValueError("successful runtime update evidence cannot contain an error")
        error = None
    return {
        "status": status,
        "installed_version": installed_version,
        "latest_version": latest_version,
        "token": token,
        "release_url": release_url,
        "error": error,
    }


def _validate_catalog(command: dict[str, Any]) -> dict[str, Any]:
    payload = _json_payload(command, label="catalog check")
    if payload.get("schema_version") != 1 or isinstance(payload.get("schema_version"), bool):
        raise ValueError("catalog check must use schema_version 1")
    status = payload.get("status")
    if status not in {"identical", "differences", "no_project_entries", "over_budget"}:
        raise ValueError("catalog check returned an unsupported status")
    complete_over_budget = status == "over_budget" and payload.get("discovery_complete") is True
    if complete_over_budget:
        if command["exit_code"] != 1:
            raise ValueError("catalog over_budget status requires exit code 1")
    elif command["exit_code"] != 0:
        raise ValueError("catalog check failed")
    required = {"status", "comparison_token", "effective_digests"}
    if not required.issubset(payload):
        raise ValueError("catalog check omitted required evidence")
    if not _is_sha256(payload["comparison_token"]):
        raise ValueError("catalog comparison_token must be a SHA-256 value")
    digests = payload["effective_digests"]
    if not isinstance(digests, dict) or set(digests) != {"playbook", "phase", "agent"}:
        raise ValueError("catalog effective_digests must cover playbook, phase, and agent")
    if any(not _is_sha256(value) for value in digests.values()):
        raise ValueError("catalog effective_digests must contain SHA-256 values")
    entries: list[object]
    affected_entry_ids: list[str] | None = None
    if status == "over_budget":
        if payload.get("discovery_complete") is not True:
            raise ValueError("catalog over_budget discovery is incomplete")
        affected_entry_ids = _bounded_string_list(
            payload.get("affected_entry_ids"), label="catalog affected_entry_ids"
        )
        if len(set(affected_entry_ids)) != len(affected_entry_ids):
            raise ValueError("catalog affected_entry_ids must be unique")
        compared_entry_count = payload.get("compared_entry_count")
        if (
            not isinstance(compared_entry_count, int)
            or isinstance(compared_entry_count, bool)
            or compared_entry_count < 0
            or compared_entry_count > MAX_CATALOG_ITEMS
        ):
            raise ValueError("catalog compared_entry_count is invalid")
        if len(affected_entry_ids) > compared_entry_count:
            raise ValueError(
                "catalog affected_entry_ids cannot exceed compared_entry_count"
            )
        entries = []
    else:
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("catalog check entries must be a list")
        if len(raw_entries) > MAX_CATALOG_ITEMS:
            raise ValueError("catalog check entries exceed the receipt item limit")
        entries = raw_entries
        entry_ids: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("catalog check contains an invalid entry")
            required_entry_fields = {
                "entry_id",
                "kind",
                "key",
                "effective_source",
                "project_path",
                "global_path",
                "project_digest",
                "global_digest",
                "reason",
            }
            if not required_entry_fields.issubset(entry):
                raise ValueError("catalog check entry omitted required evidence")
            entry_id = _bounded_text(entry["entry_id"], label="catalog entry_id")
            assert isinstance(entry_id, str)
            entry_ids.append(entry_id)
            for field in ("kind", "key", "effective_source"):
                _bounded_text(entry[field], label=f"catalog entry {field}")
            for field in ("project_path", "global_path"):
                if not _is_nonempty_text(entry[field]):
                    raise ValueError(f"catalog entry {field} must be a non-empty string")
            reason = entry["reason"]
            if reason not in CATALOG_REASONS:
                raise ValueError("catalog check entry has an unsupported reason")
            if not _is_sha256(entry["project_digest"]):
                raise ValueError("catalog project_digest must be a SHA-256 value")
            if reason == "missing_global":
                if entry["global_digest"] != "missing":
                    raise ValueError("missing_global entry must use the missing digest marker")
            elif not _is_sha256(entry["global_digest"]):
                raise ValueError("catalog global_digest must be a SHA-256 value")
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("catalog entry IDs must be unique")
        _bounded_string_list(entry_ids, label="catalog entry IDs")
        for field in ("compared_count", "difference_count"):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"catalog {field} must be a non-negative integer")
        if payload["compared_count"] != len(entries):
            raise ValueError("catalog compared_count does not match entries")
        actual_difference_count = sum(
            1 for entry in entries if isinstance(entry, dict) and entry["reason"] != "identical"
        )
        if payload["difference_count"] != actual_difference_count:
            raise ValueError("catalog difference_count does not match entries")
        if status == "no_project_entries" and entries:
            raise ValueError("no_project_entries status cannot contain entries")
        if status == "identical" and (not entries or actual_difference_count):
            raise ValueError("identical catalog status contradicts its entries")
        if status == "differences" and actual_difference_count == 0:
            raise ValueError("differences catalog status requires a difference")
        if status != "no_project_entries" and not entries:
            raise ValueError(f"{status} catalog status requires entries")
    mismatch_ids = sorted(
        {
            entry["entry_id"]
            for entry in entries
            if isinstance(entry, dict) and entry.get("reason") == "content_mismatch"
        }
    )
    return {
        key: payload.get(key)
        for key in (
            "status",
            "comparison_token",
            "effective_digests",
            "compared_count",
            "difference_count",
            "discovery_complete",
            "affected_entry_ids",
        )
    } | {
        "affected_entry_ids": affected_entry_ids,
        "content_mismatch_entry_ids": mismatch_ids,
    }


def _run_checks(executable: str) -> dict[str, Any]:
    update_command = _run_command(executable, ("update", "check", "--json"))
    catalog_command = _run_command(executable, ("catalog", "check", "--json"))
    errors: list[str] = []
    update_payload: dict[str, Any] | None = None
    catalog_payload: dict[str, Any] | None = None
    try:
        update_payload = _validate_update(update_command)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        catalog_payload = _validate_catalog(catalog_command)
    except ValueError as exc:
        errors.append(str(exc))
    if update_payload is not None:
        update_command["stdout"] = {
            "text": "",
            "truncated": False,
            "omitted_after_parse": True,
        }
    if catalog_payload is not None:
        catalog_command["stdout"] = {
            "text": "",
            "truncated": False,
            "omitted_after_parse": True,
        }
    for command in (update_command, catalog_command):
        command.pop("_stdout_raw", None)
        command.pop("_stderr_raw", None)
    return {
        "checked_at": _timestamp(),
        "valid": not errors,
        "errors": errors,
        "runtime_update": {"command": update_command, "payload": update_payload},
        "catalog": {"command": catalog_command, "payload": catalog_payload},
    }


def _comparison(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_update = before["runtime_update"]["payload"]
    after_update = after["runtime_update"]["payload"]
    before_catalog = before["catalog"]["payload"]
    after_catalog = after["catalog"]["payload"]
    assert isinstance(before_update, dict) and isinstance(after_update, dict)
    assert isinstance(before_catalog, dict) and isinstance(after_catalog, dict)
    return {
        "runtime_comparison_token_changed": before_update["token"] != after_update["token"],
        "runtime_version_changed": (
            before_update["installed_version"],
            before_update["latest_version"],
        )
        != (
            after_update["installed_version"],
            after_update["latest_version"],
        ),
        "catalog_comparison_token_changed": (
            before_catalog["comparison_token"] != after_catalog["comparison_token"]
        ),
        "effective_catalog_digests_changed": (
            before_catalog["effective_digests"] != after_catalog["effective_digests"]
        ),
        "semantic_review_required": True,
        "semantic_review_reason": (
            "CLI helper publication may change Driver behavior even when effective "
            "playbook, phase, and agent catalog digests are unchanged."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description=(
            "Publish exact CAFE CLI helper skills, then prove runtime and catalog "
            "post-change checks ran."
        ),
    )
    parser.add_argument("skills", nargs="+", help="Exact bundled helper skill names")
    parser.add_argument(
        "--cli",
        action="append",
        choices=SUPPORTED_CLIS,
        required=True,
        help="Exact destination CLI; repeat for multiple destinations",
    )
    return parser


def _validate_scope(skills: Sequence[str], clis: Sequence[str]) -> None:
    if len(skills) > MAX_SCOPE_SKILLS:
        raise ValueError("helper publication scope exceeds the skill limit")
    if len(set(skills)) != len(skills):
        raise ValueError("helper skill names must be unique")
    if len(set(clis)) != len(clis):
        raise ValueError("destination CLIs must be unique")
    invalid = [
        skill
        for skill in skills
        if len(skill) > MAX_SKILL_NAME_CHARS or SKILL_NAME.fullmatch(skill) is None
    ]
    if invalid:
        raise ValueError(f"invalid helper skill name: {invalid[0]}")


def execute(
    *, executable: str, skills: Sequence[str], clis: Sequence[str]
) -> tuple[int, dict[str, Any]]:
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": {"skills": list(skills), "clis": list(clis)},
        "started_at": _timestamp(),
        "stage": "preflight",
        "preflight": None,
        "publication": None,
        "postflight": None,
        "comparison": None,
        "post_change_verified": False,
    }
    before = _run_checks(executable)
    receipt["preflight"] = before
    if not before["valid"]:
        receipt["stage"] = "preflight_failed"
        receipt["completed_at"] = _timestamp()
        return 1, receipt

    sync_arguments = ["skill", "sync-global"]
    for cli in clis:
        sync_arguments.extend(("--cli", cli))
    sync_arguments.extend(skills)
    publication = _run_command(executable, sync_arguments)
    publication.pop("_stdout_raw", None)
    publication.pop("_stderr_raw", None)
    receipt["publication"] = publication
    receipt["stage"] = "postflight"

    after = _run_checks(executable)
    receipt["postflight"] = after
    if after["valid"]:
        receipt["comparison"] = _comparison(before, after)

    publication_succeeded = publication["exit_code"] == 0
    verified = publication_succeeded and after["valid"]
    receipt["post_change_verified"] = verified
    receipt["stage"] = "complete" if verified else "verification_failed"
    receipt["completed_at"] = _timestamp()
    return (0 if verified else 1), receipt


def main() -> int:
    args = _parser().parse_args()
    try:
        _validate_scope(args.skills, args.cli)
        executable = _resolve_executable()
        exit_code, receipt = execute(
            executable=executable,
            skills=args.skills,
            clis=args.cli,
        )
    except ValueError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

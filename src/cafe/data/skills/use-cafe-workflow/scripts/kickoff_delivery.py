"""Repository-scoped delivery conventions and bounded recent observations."""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timedelta, timezone
from collections.abc import Iterable
from pathlib import Path
from string import Formatter
from typing import Any

from _kickoff_store import repository_identity

OBSERVATION_MAX_AGE = timedelta(hours=24)
_IGNORED_DIRS = {
    ".git", ".venv", ".cache", "__pycache__", "node_modules", "build", "dist",
}
_SOURCE_PREFIXES = ("docs/", ".github/", ".cafe/", "scripts/")
_SOURCE_NAMES = {"README", "README.md", "CONTRIBUTING.md", "Makefile", "pyproject.toml"}


def render_delivery_template(template: Any, context: dict[str, str]) -> dict[str, Any]:
    """Expand a source-backed proposal template as argv, never shell code."""
    if not isinstance(template, dict) or set(template) != {"deliver", "deliver_description"}:
        raise ValueError("delivery_template requires deliver and deliver_description")
    commands, descriptions = template["deliver"], template["deliver_description"]
    if (not isinstance(commands, list) or not isinstance(descriptions, list)
            or len(commands) != len(descriptions)
            or any(not isinstance(argv, list) or not argv for argv in commands)):
        raise ValueError("delivery_template requires argv arrays with one description each")
    allowed = {"issue_name", "issue_id", "project_root", "worktree"}
    formatter = Formatter()

    def expand(text: Any) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("delivery_template arguments and descriptions must be nonempty strings")
        for _, field, spec, conversion in formatter.parse(text):
            if field is not None:
                if field not in allowed or spec or conversion:
                    raise ValueError("unsupported delivery_template placeholder")
                if not context.get(field):
                    raise ValueError(f"delivery_template requires {field}")
        return formatter.vformat(text, (), context)

    return {"deliver": [[expand(arg) for arg in argv] for argv in commands],
            "deliver_description": [expand(text) for text in descriptions]}


def _valid_template(template: Any) -> bool:
    try:
        render_delivery_template(template, {key: key for key in ("issue_name", "issue_id", "project_root", "worktree")})
    except (ValueError, KeyError, TypeError):
        return False
    return True


def _inventory(project_root: Path) -> list[str]:
    root = Path(project_root).resolve()
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode == 0:
        return sorted(
            set(
                line for line in result.stdout.splitlines()
                if line and Path(line).name != "streaming.jsonl"
                and not line.startswith(".cafe/issues/")
                and not line.startswith(".cafe/worktrees/")
            )
        )
    paths: list[str] = []
    for base, dirs, files in os.walk(root):
        relative_base = Path(base).relative_to(root).as_posix()
        runtime_dirs = {"issues", "worktrees"} if relative_base == ".cafe" else set()
        dirs[:] = sorted(name for name in dirs if name not in _IGNORED_DIRS | runtime_dirs)
        for name in files:
            if name == "streaming.jsonl":
                continue
            path = Path(base, name)
            paths.append(path.relative_to(root).as_posix())
    return sorted(paths)


def _is_source(path: str) -> bool:
    name = Path(path).name
    return path.startswith(_SOURCE_PREFIXES) or name in _SOURCE_NAMES


def _hash_file(root: Path, relative: str) -> str | None:
    try:
        path = root / relative
        if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError, RuntimeError):
        return None


def discover_delivery_manifest(project_root: Path, *, referenced_paths: Iterable[str] = ()) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    inventory = _inventory(root)
    referenced = set(referenced_paths)
    sources = [
        {"path": path, "fingerprint": digest}
        for path in inventory
        if (_is_source(path) or path in referenced) and (digest := _hash_file(root, path)) is not None
    ]
    return {
        "repository": repository_identity(root),
        "inventory": inventory,
        "sources": sources,
        "watched": ["docs/", ".github/", ".cafe/", "scripts/", *_SOURCE_NAMES],
    }


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def assess_delivery(
    record: dict[str, Any], *, project_root: Path, now: datetime,
    contradictions: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    sources = record.get("sources")
    sources = sources if isinstance(sources, list) else []
    references = [source["path"] for source in sources
                  if isinstance(source, dict) and isinstance(source.get("path"), str)]
    manifest = discover_delivery_manifest(root, referenced_paths=references)
    diagnostics: list[str] = []
    # Persisted records receive the same required-field validation as refresh.
    conventions = record.get("stable_conventions")
    discovery = record.get("discovery")
    if (not isinstance(conventions, list) or not conventions
            or any(not isinstance(item, str) or not item.strip() for item in conventions)):
        diagnostics.append("stable_conventions_missing")
    if not sources or any(not isinstance(item, dict) for item in sources):
        diagnostics.append("delivery_sources_invalid")
    if not isinstance(record.get("target"), str) or not record["target"].strip():
        diagnostics.append("delivery_target_missing")
    if not isinstance(record.get("observations", []), list):
        diagnostics.append("delivery_observations_invalid")
    if not isinstance(discovery, dict) or any(
        not isinstance(discovery.get(key, []), list)
        or any(not isinstance(item, str) for item in discovery.get(key, []))
        for key in ("inventory", "classified_paths", "watched")
    ) or "inventory" not in discovery:
        diagnostics.append("discovery_manifest_invalid")
    if diagnostics:
        return {"status": "miss", "diagnostics": diagnostics, "discovery_gap": True,
                "stable_conventions": [], "delivery_template": None,
                "current_observations": [], "sources": [], "manifest": manifest}
    discovery_gap = False
    if record.get("repository") != manifest["repository"]:
        diagnostics.append("repository_identity_changed")
    discovery = record.get("discovery")
    if not isinstance(discovery, dict):
        diagnostics.append("discovery_manifest_missing")
        discovery_gap = True
        discovery = {}
    old_inventory = set(discovery.get("inventory", []))
    current_inventory = set(manifest["inventory"])
    added = current_inventory - old_inventory
    removed = old_inventory - current_inventory
    classified = set(discovery.get("classified_paths", []))
    if any(path not in classified for path in added):
        diagnostics.append("new_paths_require_classification")
        discovery_gap = True
    watched = discovery.get("watched", manifest["watched"])
    if any(
        path.startswith(prefix) if prefix.endswith("/") else Path(path).name == prefix
        for path in added | removed
        for prefix in watched
    ):
        diagnostics.append("watched_membership_changed")
    current_hashes = {item["path"]: item["fingerprint"] for item in manifest["sources"]}
    for source in record.get("sources", []):
        if not isinstance(source, dict):
            diagnostics.append("source_record_invalid")
            continue
        path = source.get("path")
        if isinstance(path, str):
            if path not in current_hashes or current_hashes[path] != source.get("fingerprint"):
                diagnostics.append("material_source_changed")
        elif not (isinstance(source.get("url"), str) and source["url"].startswith(("https://", "http://"))
                  and _date(source.get("retrieved_at")) is not None
                  and isinstance(source.get("fingerprint"), str) and source["fingerprint"]):
            diagnostics.append("source_record_invalid")
    instant = now.astimezone(timezone.utc) if now.tzinfo else None
    if instant is None:
        diagnostics.append("current_time_requires_timezone")
        instant = datetime.min.replace(tzinfo=timezone.utc)
    observations: list[dict[str, Any]] = []
    expired = False
    for observation in record.get("observations", []):
        if not isinstance(observation, dict):
            expired = True
            continue
        observed_at = _date(observation.get("observed_at"))
        retrieved_at = _date(observation.get("retrieved_at"))
        valid_until = _date(observation.get("valid_until"))
        target = observation.get("target")
        if (
            ("valid_until" in observation and valid_until is None)
            or observed_at is None
            or retrieved_at is None
            or observed_at > retrieved_at
            or retrieved_at > instant
            or target != record.get("target")
        ):
            expired = True
            continue
        expiry = min(observed_at + OBSERVATION_MAX_AGE, valid_until) if valid_until else observed_at + OBSERVATION_MAX_AGE
        if expiry <= instant:
            expired = True
            continue
        observations.append(observation)
    if expired:
        diagnostics.append("delivery_observation_expired_or_invalid")
    if contradictions:
        diagnostics.append("contradictory_current_delivery_evidence")
    template = record.get("delivery_template")
    template_invalid = "delivery_template" in record and not _valid_template(template)
    if template_invalid:
        diagnostics.append("delivery_template_invalid")
    blocked = any(
        item in diagnostics
        for item in ("repository_identity_changed", "discovery_manifest_missing", "watched_membership_changed", "material_source_changed", "source_record_invalid")
    )
    status = "hit" if not blocked and not discovery_gap and not expired and not contradictions and not template_invalid else "miss"
    return {
        "status": status,
        "diagnostics": diagnostics,
        "discovery_gap": discovery_gap,
        "stable_conventions": list(record.get("stable_conventions", [])) if not blocked else [],
        "delivery_template": None,
        "legacy_template_diagnostic": "obsolete_manager_delivery_template" if template is not None else None,
        "current_observations": observations,
        "sources": list(record.get("sources", [])) if not blocked else [],
        "manifest": manifest,
    }


def refresh_delivery(record: dict[str, Any], *, evidence: Any, project_root: Path, now: datetime) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {"record": record, "refreshed": False, "diagnostic": "refresh_evidence_missing"}
    if "delivery_template" in evidence:
        return {"record": record, "refreshed": False, "diagnostic": "obsolete_manager_delivery_template"}
    references = [source["path"] for source in evidence.get("sources", [])
                  if isinstance(source, dict) and isinstance(source.get("path"), str)] if isinstance(evidence.get("sources"), list) else []
    manifest = discover_delivery_manifest(project_root, referenced_paths=references)
    conventions = evidence.get("stable_conventions")
    sources = evidence.get("sources")
    target = evidence.get("target")
    if not isinstance(conventions, list) or not conventions or any(
        not isinstance(item, str) or not item.strip() for item in conventions
    ):
        return {"record": record, "refreshed": False, "diagnostic": "stable_conventions_missing"}
    if not isinstance(target, str) or not target.strip() or not isinstance(sources, list) or not sources:
        return {"record": record, "refreshed": False, "diagnostic": "delivery_source_or_target_missing"}
    source_hashes = {item["path"]: item["fingerprint"] for item in manifest["sources"]}
    for source in sources:
        if not isinstance(source, dict):
            return {"record": record, "refreshed": False, "diagnostic": "delivery_source_invalid"}
        if isinstance(source.get("path"), str):
            if source["path"] not in source_hashes or source_hashes[source["path"]] != source.get("fingerprint"):
                return {"record": record, "refreshed": False, "diagnostic": "delivery_source_changed"}
        elif not (
            isinstance(source.get("url"), str)
            and source["url"].startswith(("https://", "http://"))
            and _date(source.get("retrieved_at")) is not None
            and isinstance(source.get("fingerprint"), str)
        ):
            return {"record": record, "refreshed": False, "diagnostic": "delivery_source_invalid"}
    updated = {
        **evidence,
        "repository": manifest["repository"],
        "discovery": {
            **manifest,
            "classified_paths": list(
                evidence.get(
                    "classified_paths",
                    [source["path"] for source in sources if isinstance(source, dict) and isinstance(source.get("path"), str)],
                )
            ),
        },
    }
    assessed = assess_delivery(updated, project_root=project_root, now=now)
    if assessed["status"] != "hit":
        return {"record": record, "refreshed": False, "diagnostic": assessed["diagnostics"][0]}
    return {"record": updated, "refreshed": True}

"""Flat repository-owned verification contracts, independent of their consumers."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from cafe.verification.receipt import VerificationReceiptError


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def repository_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = (root / path).resolve()
    if ".." in path.parts or not resolved.is_relative_to(root.resolve()):
        raise VerificationReceiptError(f"path escapes repository: {value}")
    return resolved


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VerificationReceiptError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def string_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if (not isinstance(value, list) or (nonempty and not value)
            or not all(isinstance(x, str) and x and "\0" not in x for x in value)):
        raise VerificationReceiptError(f"{label} must be a list of nonempty strings")
    return value


def _ids(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    values = string_list(value, label, nonempty=nonempty)
    if len(set(values)) != len(values) or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in values):
        raise VerificationReceiptError(f"{label} contains duplicate or invalid IDs")
    return values


@dataclass(frozen=True)
class ProfileRequest:
    root: Path
    source: str
    name: str
    identity: str
    mandatory: tuple[str, ...]
    selected: tuple[str, ...]
    suites: dict[str, dict[str, Any]]


def load_profile(root: Path, path: Path, name: str, extras: Sequence[str] = ()) -> ProfileRequest:
    root = root.resolve()
    source = repository_path(root, path)
    try:
        data = json.loads(source.read_text(), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise VerificationReceiptError(f"cannot read profile manifest: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"schema_version", "profiles", "suites"} or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise VerificationReceiptError("manifest requires schema_version=1, profiles and suites")
    profiles, suites = data["profiles"], data["suites"]
    if not isinstance(profiles, dict) or not isinstance(suites, dict):
        raise VerificationReceiptError("profiles and suites must be objects")
    _ids(list(profiles), "profile IDs", nonempty=True)
    _ids(list(suites), "suite IDs", nonempty=True)
    normalized = {}
    for suite_id, suite in suites.items():
        if not isinstance(suite, dict) or set(suite) - {"argv", "reuse", "context"}:
            raise VerificationReceiptError(f"invalid suite: {suite_id}")
        argv = string_list(suite.get("argv"), "argv", nonempty=True)
        reuse = suite.get("reuse", False)
        if not isinstance(reuse, bool):
            raise VerificationReceiptError("reuse must be boolean")
        context = suite.get("context", {})
        if not isinstance(context, dict) or set(context) - {"files", "env", "probes"}:
            raise VerificationReceiptError("context supports only files, env and probes")
        files = string_list(context.get("files", []), "files")
        for file in files:
            repository_path(root, file)
        env = string_list(context.get("env", []), "env")
        if len(set(env)) != len(env) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x) for x in env):
            raise VerificationReceiptError("invalid or duplicate environment names")
        probes = context.get("probes", [])
        if not isinstance(probes, list):
            raise VerificationReceiptError("probes must be a list of argv lists")
        for probe in probes:
            string_list(probe, "probe argv", nonempty=True)
        normalized[suite_id] = {"argv": argv, "reuse": reuse,
                                "context": {"files": files, "env": env, "probes": probes}}
    for profile in profiles.values():
        if not isinstance(profile, dict) or set(profile) - {"mandatory", "optional"}:
            raise VerificationReceiptError("invalid profile definition")
        mandatory = _ids(profile.get("mandatory"), "mandatory", nonempty=True)
        optional = _ids(profile.get("optional", []), "optional")
        if set(mandatory) & set(optional) or any(x not in suites for x in mandatory + optional):
            raise VerificationReceiptError("profile contains unknown or overlapping suite IDs")
    if name not in profiles:
        raise VerificationReceiptError(f"unknown profile: {name}")
    profile = profiles[name]
    extra_ids = _ids(list(extras), "extra suites")
    if any(x not in profile.get("optional", []) for x in extra_ids):
        raise VerificationReceiptError("extra suite is not optional in this profile")
    selected = tuple(profile["mandatory"] + [x for x in profile.get("optional", []) if x in extra_ids])
    return ProfileRequest(root, source.relative_to(root).as_posix(), name, fingerprint(data),
                          tuple(profile["mandatory"]), selected,
                          {x: normalized[x] for x in selected})

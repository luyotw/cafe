"""Scoped explicit preferences for preparing new CAFE kickoff proposals."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _kickoff_store import VersionedJsonStore, repository_identity

SCHEMA_VERSION = 1
_FORBIDDEN_KEY_PARTS = ("execute", "publish", "authorization", "permission", "issue.target")


@dataclass(frozen=True)
class EffectivePreference:
    value: Any
    scope: str | None
    origin: str | None


@dataclass(frozen=True)
class ValidationResult:
    value: Any
    diagnostic: str | None

    @property
    def value_or_diagnostics(self) -> dict[str, Any]:
        return {"value": self.value, "diagnostic": self.diagnostic}


def _canonical_locale(value: str) -> str:
    parts = value.strip().replace("_", "-").split("-")
    if not parts or not re.fullmatch(r"[A-Za-z]{2,3}", parts[0]):
        raise ValueError("Invalid locale")
    language = parts[0].lower()
    normalized = [language]
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            normalized.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


class PreferenceStore:
    """Store user and repository preferences independently from evidence."""

    def __init__(self, config_dir: Path, *, repository_root: Path | None = None) -> None:
        self.config_dir = Path(config_dir).expanduser()
        self.repo_id = repository_identity(repository_root) if repository_root else None

    def _store(self, scope: str) -> VersionedJsonStore:
        if scope == "repository":
            if self.repo_id is None:
                raise ValueError("Repository preferences require a project context")
            key = self.repo_id.split(":", 1)[1]
            import hashlib

            name = hashlib.sha256(key.encode("utf-8")).hexdigest()
            path = self.config_dir / "repositories" / f"{name}.json"
        elif scope == "user":
            path = self.config_dir / "preferences-v1.json"
        else:
            raise ValueError("Scope must be user or repository")
        return VersionedJsonStore(path, schema_version=SCHEMA_VERSION, collection="preferences")

    def inspect(self, *, scope: str) -> dict[str, dict[str, Any]]:
        return self._store(scope).read()

    def set(
        self,
        key: str,
        value: Any,
        *,
        scope: str,
        origin: str,
        reuse: bool = True,
    ) -> bool:
        if not reuse:
            return False
        if origin != "explicit":
            raise ValueError("Only explicit values may be reused")
        if any(part in key.lower() for part in _FORBIDDEN_KEY_PARTS):
            raise ValueError("Authority and issue targets cannot be preferences")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Preference key is required")
        store = self._store(scope)
        records = store.read()
        records[key] = {"value": value, "scope": scope, "origin": origin}
        store.write(records)
        return True

    def effective(self, key: str, *, explicit: Any = None, defaults: Any = None) -> EffectivePreference:
        if explicit is not None:
            return EffectivePreference(explicit, "current", "explicit")
        if self.repo_id is not None:
            record = self._store("repository").read().get(key)
            if record is not None and record.get("origin") == "explicit":
                return EffectivePreference(record.get("value"), "repository", "explicit")
        record = self._store("user").read().get(key)
        if record is not None and record.get("origin") == "explicit":
            return EffectivePreference(record.get("value"), "user", "explicit")
        return EffectivePreference(defaults, "default" if defaults is not None else None, "policy" if defaults is not None else None)

    def clear(self, key: str, *, scope: str) -> bool:
        store = self._store(scope)
        records = store.read()
        if key not in records:
            return False
        del records[key]
        store.write(records)
        return True


def canonical_language_input(record: dict[str, Any]) -> dict[str, str]:
    """Normalize one selected language instruction without changing provenance."""
    value = record.get("value")
    source = record.get("origin")
    scope = record.get("scope")
    if not isinstance(value, str) or source not in {"explicit", "inferred"}:
        raise ValueError("A language value and its explicit or inferred origin are required")
    if scope not in {"current", "repository", "user", "default"}:
        raise ValueError("Unsupported language preference scope")
    return {"value": _canonical_locale(value), "source": source, "scope": scope}


def validate_reusable_value(key: str, value: Any, *, declarations: dict[str, Any]) -> ValidationResult:
    if any(part in key.lower() for part in _FORBIDDEN_KEY_PARTS):
        return ValidationResult(None, "Authority-bearing values cannot be reused")
    if key == "confirmation.assignments" and isinstance(value, dict):
        requested = value.get("mandatory_task")
        mandatory = declarations.get("mandatory_tasks", [])
        if requested is False and mandatory:
            return ValidationResult(None, "A required confirmation task cannot be disabled")
    return ValidationResult(value, None)

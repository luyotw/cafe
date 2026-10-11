"""Lossless immutable provider tuple shared by configuration and invocation snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ExecutionEntry:
    cli: str
    model: str | None = None
    reasoning_effort: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> ExecutionEntry:
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            cli = value["cli"]
            return cls(
                getattr(cli, "value", cli), value.get("model"), value.get("reasoning_effort")
            )
        if isinstance(value, (tuple, list)) and len(value) in (2, 3):
            return cls(*value)
        cli = value.cli
        return cls(getattr(cli, "value", cli), value.model, value.reasoning_effort)

    def as_dict(self) -> dict[str, Any]:
        entry = {"cli": self.cli, "model": self.model}
        if self.reasoning_effort is not None:
            entry["reasoning_effort"] = self.reasoning_effort
        return entry

    def fingerprint(self) -> str:
        # Retain old sticky identity for omission; explicit tuples are lossless JSON.
        if self.reasoning_effort is None:
            return f'{self.cli}:{self.model or ""}'
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

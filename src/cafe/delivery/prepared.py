"""Delivery choices fixed at preparation; later observations bind exact Git identities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cafe.delivery.contracts import DeliveryVerification
from cafe.utils.issue_config import read_issue_config_strict


class PreparedDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["github", "local"]
    target_branch: str = Field(min_length=1)
    strategy: Literal["merge", "squash", "rebase", "ff-only", "merge-commit"]
    destination: str = ""
    issue_repository: str = ""
    verification: dict

    def request(self) -> dict:
        if self.mode == "github":
            if self.destination or self.strategy not in {"merge", "squash", "rebase"}:
                raise ValueError(
                    "GitHub delivery needs a GitHub merge strategy and no local destination"
                )
        elif not Path(self.destination).is_absolute() or self.strategy not in {
            "ff-only",
            "merge-commit",
        }:
            raise ValueError(
                "local delivery needs an absolute destination and local merge strategy"
            )
        DeliveryVerification.model_validate(self.verification)
        return self.model_dump(mode="json")


def prepared_request(issue_dir: Path, step: str) -> dict | None:
    """Legacy workflows keep their shown choices; prepared choices cannot be overridden by an agent."""
    if not (issue_dir / "issue.yaml").exists():
        return None  # Historical standalone host callers predate prepare policies.
    config = read_issue_config_strict(issue_dir / "issue.yaml")
    return request_from_config(config, step)


def request_from_config(config: dict, step: str) -> dict | None:
    values = config.get(step, {})
    if not isinstance(values, dict):
        raise ValueError("delivery step configuration must be a mapping")
    keys = set(PreparedDelivery.model_fields)
    if not keys.intersection(values):
        return None
    selected = {key: value for key, value in values.items() if key in keys}
    if isinstance(selected.get("verification"), str):
        selected["verification"] = json.loads(selected["verification"])
    return PreparedDelivery.model_validate(selected).request()


def validate_prepared_delivery(config: dict, playbook: dict) -> None:
    for step, definition in playbook["steps"].items():
        if definition.get("delivery") is not None:
            request_from_config(config, step)


def extend_prepare_fields(parsed, playbook: dict, values: list[str]):
    """Expose typed delivery settings only when explicitly supplied by --set."""
    from cafe.core.prepare_fields import ParsedPrepareFields, PrepareField

    fields = list(parsed.fields) if parsed else []
    ids = {item.id for item in fields}
    requested = {raw.partition("=")[0] for raw in values}
    choices = {
        "mode": ["github", "local"],
        "strategy": ["merge", "squash", "rebase", "ff-only", "merge-commit"],
    }
    for step, definition in playbook["steps"].items():
        if definition.get("delivery") is None:
            continue
        for key in PreparedDelivery.model_fields:
            name = f"{step}.{key}"
            if name not in requested or name in ids:
                continue
            fields.append(
                PrepareField.model_validate(
                    {
                        "id": name,
                        "write": name,
                        "label": name,
                        "type": "enum" if key in choices else "text",
                        **(
                            {"choices": [{"value": v, "label": v} for v in choices[key]]}
                            if key in choices
                            else {}
                        ),
                    }
                )
            )
    return ParsedPrepareFields(fields=fields, meta=parsed.meta if parsed else None)

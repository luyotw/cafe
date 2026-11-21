"""Delivery choices fixed at preparation; later observations bind exact Git identities."""

from __future__ import annotations

from copy import deepcopy
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


def local_review_step(playbook):
    candidates = [name for name, step in playbook["steps"].items() if step.get("local_review")]
    if len(candidates) != 1:
        raise ValueError("the playbook must declare one local review endpoint")
    return candidates[0]


def resolve_local_review(playbook, config):
    selected = [name for name in playbook["steps"] if isinstance(config.get(name), dict)
                and config[name].get("endpoint") == "local-review"]
    if not selected:
        return playbook
    name = local_review_step(playbook)
    if selected != [name]:
        raise ValueError("local review must select its declared endpoint")
    result = deepcopy(playbook)
    steps = result["steps"]
    endpoint = steps[name]
    contract = endpoint["local_review"]
    reviewer = steps[contract["review_step"]]
    author = steps[contract["implementation_step"]]
    workspace = endpoint.get("workspace_input_artifact")
    if (not workspace or reviewer.get("workspace_input_artifact") != workspace
            or author.get("workspace_artifact") != workspace or not reviewer.get("role")
            or reviewer.get("role") == author.get("role")
            or reviewer.get("on", {}).get("await_agent") != name
            or not reviewer.get("output_artifact") or not author.get("output_artifact")):
        raise ValueError("local review requires an independent passing route and its declared workspace")
    confirmations = [binding for binding in endpoint["human_tasks"] if binding["trigger"] == "confirm_output"]
    if len(confirmations) != 1 or any(t not in {*steps, "_done"} for t in contract["outcomes"].values()):
        raise ValueError("invalid local acceptance route")
    confirmation = confirmations[0]
    confirmation.update(outcomes=contract["outcomes"], mandatory_confirmation=True)
    endpoint.update(assignee_type="auto", automatic={"executor": "local_review", "inputs": {}},
                    output_artifact=contract["result_artifact"],
                    input_artifacts=[author["output_artifact"], reviewer["output_artifact"], workspace],
                    human_tasks=[confirmation], capability_requests=[], allowed_tools=[], hooks={},
                    allowed_goto=[], behavior={}, on={"confirm_output": name})
    return result


def validate_prepared_delivery(config: dict, playbook: dict) -> None:
    effective = resolve_local_review(playbook, config)
    for step, definition in playbook["steps"].items():
        if definition.get("delivery") is not None:
            if effective is not playbook:
                selected = local_review_step(playbook)
                if definition["delivery"].get("publication_artifact") == playbook["steps"][selected].get("output_artifact"):
                    continue
            request = request_from_config(config, step)
            if definition.get("assignee_type") == "auto" and request is None:
                raise ValueError(f"automatic delivery requires prepare choices: --set {step}.mode=github|local "
                                 f"--set {step}.target_branch=BRANCH --set {step}.strategy=STRATEGY "
                                 f"--set '{step}.verification=JSON' (local also requires {step}.destination)")


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

"""Strict persisted contract for version 2 workflow driver policy."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

DelegatedCLI = Literal["claude", "codex", "gemini", "copilot", "cursor-agent"]


class _StrictPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AttachedDriverPolicy(_StrictPolicyModel):
    mode: Literal["attached"]
    poll_interval_seconds: int = Field(gt=0)


class UnattendedDriverPolicy(_StrictPolicyModel):
    mode: Literal["unattended"]


class DelegatedDriverPolicy(_StrictPolicyModel):
    mode: Literal["delegated"]
    cli: DelegatedCLI
    model: str

    @field_validator("model")
    @classmethod
    def require_exact_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("delegated mode requires a non-empty exact model")
        return value


DriverPolicy = Annotated[
    AttachedDriverPolicy | UnattendedDriverPolicy | DelegatedDriverPolicy,
    Field(discriminator="mode"),
]


class DriverPolicyContract(_StrictPolicyModel):
    contract_version: Literal[2]
    driver: DriverPolicy


POLICY_KEYS = frozenset({"contract_version", "driver"})
REJECTED_POLICY_KEYS = frozenset(
    {"driver_execution", "execution", "advancement", "hosting", "availability"}
)


def extract_driver_policy(issue_config: Mapping[str, Any]) -> DriverPolicyContract:
    """Validate the complete v2 slice without validating unrelated issue metadata."""
    rejected = REJECTED_POLICY_KEYS.intersection(issue_config)
    if rejected:
        raise ValueError(
            "removed workflow driver fields are not accepted by contract version 2: "
            + ", ".join(sorted(rejected))
        )
    policy = {key: issue_config[key] for key in POLICY_KEYS if key in issue_config}
    return DriverPolicyContract.model_validate(policy)


def policy_dict(policy: DriverPolicyContract) -> dict[str, Any]:
    """Return the stable YAML-ready policy shape."""
    return policy.model_dump(mode="json", exclude_none=True)

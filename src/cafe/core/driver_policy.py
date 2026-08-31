"""Strict persisted contract for version 2 workflow driver policy."""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


DelegatedCLI = Literal["claude", "codex", "gemini", "copilot", "cursor-agent"]


class _StrictPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AttachedDriverConfig(_StrictPolicyModel):
    poll_interval_seconds: int = Field(gt=0)


class DelegatedDriverConfig(_StrictPolicyModel):
    cli: DelegatedCLI
    availability: Literal["best_effort", "required"]


class DriverOwnershipPolicy(_StrictPolicyModel):
    mode: Literal["attached", "unattended", "delegated"]
    attached: AttachedDriverConfig | None = None
    delegated: DelegatedDriverConfig | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "DriverOwnershipPolicy":
        if self.mode == "attached":
            if self.attached is None or self.delegated is not None:
                raise ValueError("attached mode requires only driver.attached")
        elif self.mode == "delegated":
            if self.delegated is None or self.attached is not None:
                raise ValueError("delegated mode requires only driver.delegated")
        elif self.attached is not None or self.delegated is not None:
            raise ValueError("unattended mode accepts no mode-specific driver fields")
        return self


class ExecutionPolicy(_StrictPolicyModel):
    advancement: Literal["continuous", "single_step"]
    hosting: Literal["foreground", "background"]


class DriverPolicyContract(_StrictPolicyModel):
    contract_version: Literal[2]
    driver: DriverOwnershipPolicy
    execution: ExecutionPolicy


POLICY_KEYS = frozenset({"contract_version", "driver", "execution"})


def extract_driver_policy(issue_config: Mapping[str, Any]) -> DriverPolicyContract:
    """Validate the complete v2 slice without validating unrelated issue metadata."""
    if "driver_execution" in issue_config:
        raise ValueError("legacy driver_execution is not accepted by contract version 2")
    policy = {key: issue_config[key] for key in POLICY_KEYS if key in issue_config}
    return DriverPolicyContract.model_validate(policy)


def policy_dict(policy: DriverPolicyContract) -> dict[str, Any]:
    """Return the stable YAML-ready policy shape."""
    return policy.model_dump(mode="json", exclude_none=True)


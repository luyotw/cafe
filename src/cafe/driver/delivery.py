"""Versioned delivery facts, owned by the Driver and never read by workflow core."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class DeliveryConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    architecture: list[str]
    dependencies: list[str]
    compatibility: list[str]
    quality: list[str]
    permissions: list[str]
    external_side_effects: list[str]
    cost: list[str]

    @field_validator("*")
    @classmethod
    def _distinct_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("constraints must contain distinct non-empty statements")
        return values


class _DeliveryContractBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    schema_version: StrictInt
    outcome: str = Field(min_length=1)
    motivation: str = Field(min_length=1)
    in_scope: list[str] = Field(min_length=1)
    out_of_scope: list[str]
    acceptance_invariants: list[str] = Field(min_length=1)
    required_evidence: list[str] = Field(min_length=1)
    implementation_direction: str = Field(min_length=1)
    constraints: DeliveryConstraints
    allowed_variations: list[str]
    deviation_triggers: list[str] = Field(min_length=1)

    @field_validator(
        "in_scope",
        "out_of_scope",
        "acceptance_invariants",
        "required_evidence",
        "allowed_variations",
        "deviation_triggers",
    )
    @classmethod
    def _distinct_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("delivery lists must contain distinct non-empty statements")
        return values


class CloseoutCommand(BaseModel):
    """One exact host-side command approved as part of a closeout plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    argv: list[str] = Field(min_length=1)

    @field_validator("argv")
    @classmethod
    def _literal_nonempty_argv(cls, values: list[str]) -> list[str]:
        if not values[0]:
            raise ValueError("closeout argv executable must not be empty")
        for value in values:
            if "{{" in value or "${" in value or (value.startswith("<") and value.endswith(">")):
                raise ValueError("closeout argv must not contain unresolved placeholders")
        return values


class DeliveryCloseoutPlan(BaseModel):
    """Exact commands confirmed with the complete Delivery Contract."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    deliver: list[CloseoutCommand]
    cleanup: list[CloseoutCommand]

    @field_validator("deliver", "cleanup")
    @classmethod
    def _distinct_commands(cls, values: list[CloseoutCommand]) -> list[CloseoutCommand]:
        commands = [tuple(command.argv) for command in values]
        if len(set(commands)) != len(commands):
            raise ValueError("closeout commands must be distinct within each stage")
        return values


class DeliveryContractV1(_DeliveryContractBase):
    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported Delivery Contract version")
        return value


class DeliveryContractV2(_DeliveryContractBase):
    closeout_plan: DeliveryCloseoutPlan

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 2:
            raise ValueError("unsupported Delivery Contract version")
        return value


class DeliveryContractV3(BaseModel):
    """Compact confirmed outcome and its task-specific authority boundaries."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    schema_version: StrictInt
    outcome: str = Field(min_length=1)
    in_scope: list[str] = Field(min_length=1)
    out_of_scope: list[str]
    acceptance_invariants: list[str] = Field(min_length=1)
    implementation_direction: str = Field(min_length=1)
    permissions: list[str]
    constraints: list[str]
    closeout_plan: DeliveryCloseoutPlan

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 3:
            raise ValueError("unsupported Delivery Contract version")
        return value

    @field_validator(
        "in_scope",
        "out_of_scope",
        "acceptance_invariants",
        "permissions",
        "constraints",
    )
    @classmethod
    def _distinct_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("delivery lists must contain distinct non-empty statements")
        return values


def normalize_delivery_contract(value: Any) -> dict[str, Any]:
    """Validate contract structure; activation supplies confirmation authority."""
    if not isinstance(value, dict):
        raise ValueError("Delivery Contract must be a mapping")
    version = value.get("schema_version")
    if version == 1:
        return DeliveryContractV1.model_validate(value).model_dump(mode="json")
    if version == 2:
        return DeliveryContractV2.model_validate(value).model_dump(mode="json")
    if version == 3:
        return DeliveryContractV3.model_validate(value).model_dump(mode="json")
    raise ValueError("unsupported Delivery Contract version")

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


class DeliveryContract(BaseModel):
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

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported Delivery Contract version")
        return value

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


def normalize_delivery_contract(value: Any) -> dict[str, Any]:
    """Validate structure only; text is untrusted data, not executable policy."""
    return DeliveryContract.model_validate(value).model_dump(mode="json")

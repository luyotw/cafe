"""Versioned delivery facts, owned by the Driver and never read by workflow core."""

from __future__ import annotations

import re
from typing import Any, Literal

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


class CICDConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    path: str = Field(min_length=1)
    system: str = Field(min_length=1)
    signals: list[str]

    @field_validator("signals")
    @classmethod
    def _distinct_signals(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("CI/CD signals must contain distinct non-empty values")
        return values


class CICDInference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    schema_version: StrictInt
    fingerprint_sha256: str = Field(min_length=64, max_length=64)
    configurations: list[CICDConfiguration]
    suggested_deliver_scope: list[str] = Field(min_length=1)
    suggested_cleanup_scope: list[str] = Field(min_length=1)
    requires_explicit_confirmation: list[str] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported CI/CD inference version")
        return value

    @field_validator("fingerprint_sha256")
    @classmethod
    def _fingerprint(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("CI/CD inference fingerprint must be a SHA-256 digest")
        return value

    @field_validator(
        "suggested_deliver_scope",
        "suggested_cleanup_scope",
        "requires_explicit_confirmation",
    )
    @classmethod
    def _distinct_actions(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("CI/CD action lists must contain distinct non-empty values")
        return values


class DeliveryCloseoutPlan(BaseModel):
    """A confirmed scope plan, deliberately distinct from action authority."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    ci_cd_inference: CICDInference
    deliver_scope: list[str] = Field(min_length=1)
    cleanup_scope: list[str] = Field(min_length=1)
    execution_authority: Literal["separate_user_confirmation_required"]

    @field_validator("deliver_scope", "cleanup_scope")
    @classmethod
    def _distinct_scopes(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("closeout scopes must contain distinct non-empty values")
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


def normalize_closeout_inference(value: Any) -> dict[str, Any]:
    """Validate read-only CI/CD inference before it becomes confirmed contract data."""
    return CICDInference.model_validate(value).model_dump(mode="json")


def normalize_delivery_contract(value: Any) -> dict[str, Any]:
    """Validate structure only; text is untrusted data, not executable policy."""
    if not isinstance(value, dict):
        raise ValueError("Delivery Contract must be a mapping")
    version = value.get("schema_version")
    if version == 1:
        return DeliveryContractV1.model_validate(value).model_dump(mode="json")
    if version == 2:
        return DeliveryContractV2.model_validate(value).model_dump(mode="json")
    raise ValueError("unsupported Delivery Contract version")

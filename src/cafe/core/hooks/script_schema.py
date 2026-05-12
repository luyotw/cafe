"""Minimal schema validation helpers for script hook arguments."""

from __future__ import annotations

from typing import Any


def validate_script_args_schema(
    *,
    args: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """Validate hook args using a minimal JSON-schema subset."""
    errors: list[str] = []

    schema_type = schema.get("type")
    if schema_type != "object":
        return ["schema.type must be 'object'"]

    properties = schema.get("properties", {})
    if properties is None:
        properties = {}
    if not isinstance(properties, dict):
        return ["schema.properties must be an object"]

    required = schema.get("required", [])
    if required is None:
        required = []
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return ["schema.required must be a list of strings"]

    for key in required:
        if key not in args:
            errors.append(f"missing required key '{key}'")

    additional_properties = schema.get("additionalProperties", True)
    if additional_properties is False:
        unknown = sorted(set(args.keys()) - set(properties.keys()))
        for key in unknown:
            errors.append(f"unknown key '{key}' is not allowed")

    for key, rule in properties.items():
        if key not in args:
            continue
        if not isinstance(rule, dict):
            errors.append(f"schema.properties.{key} must be an object")
            continue

        value = args[key]
        expected_type = rule.get("type")
        if expected_type and not _matches_type(value, expected_type):
            errors.append(
                f"key '{key}' expected type '{expected_type}' but got '{type(value).__name__}'"
            )

        enum_values = rule.get("enum")
        if enum_values is not None:
            if not isinstance(enum_values, list):
                errors.append(f"schema.properties.{key}.enum must be a list")
            elif value not in enum_values:
                errors.append(f"key '{key}' must be one of {enum_values}")

    return errors


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    return False

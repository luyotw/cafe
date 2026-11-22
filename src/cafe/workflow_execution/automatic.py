"""Compose native automatic executors outside the generic workflow runtime."""

from cafe.core.automatic_steps import default_automatic_executor_registry
from cafe.delivery.automatic import execute, validate_inputs
from cafe.delivery.reviewed_result import execute as present_reviewed_result, prepare_integration


def automatic_executor_registry():
    return default_automatic_executor_registry(
        contextual_executors={
            "delivery": execute, "local_review": present_reviewed_result,
            "local_preparation": prepare_integration,
        },
        input_validators={name: validate_inputs for name in ("delivery", "local_review", "local_preparation")},
    )

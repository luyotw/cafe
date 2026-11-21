"""Compose native automatic executors outside the generic workflow runtime."""

from cafe.core.automatic_steps import default_automatic_executor_registry
from cafe.delivery.automatic import execute, validate_inputs
from cafe.delivery.reviewed_result import execute as present_reviewed_result


def automatic_executor_registry():
    return default_automatic_executor_registry(
        contextual_executors={"delivery": execute, "local_review": present_reviewed_result},
        input_validators={"delivery": validate_inputs, "local_review": validate_inputs},
    )

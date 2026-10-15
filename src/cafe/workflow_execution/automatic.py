"""Compose native automatic executors outside the generic workflow runtime."""

from cafe.core.automatic_steps import default_automatic_executor_registry
from cafe.delivery.automatic import execute, validate_inputs


def automatic_executor_registry():
    return default_automatic_executor_registry(
        contextual_executors={"delivery": execute},
        input_validators={"delivery": validate_inputs},
    )

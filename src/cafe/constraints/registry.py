"""Package-owned resource loading; project data cannot override runtime truth."""

from functools import lru_cache
from importlib.resources import files
import json

from .models import Registry


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_registry(raw: str) -> Registry:
    data = json.loads(raw, object_pairs_hook=_unique_object)
    return Registry.model_validate_json(json.dumps(data))


@lru_cache(maxsize=4)
def _validated(raw: str) -> Registry:
    return parse_registry(raw)


def load_registry() -> Registry:
    # Read bytes on each access: live resource changes cannot retain stale facts.
    return _validated(
        files("cafe.data").joinpath("runtime_constraints.json").read_text(encoding="utf-8")
    )

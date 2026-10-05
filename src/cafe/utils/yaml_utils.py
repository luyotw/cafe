"""Safe YAML parsing with the optional LibYAML acceleration."""

from typing import Any

import yaml

SafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def safe_load(stream: Any) -> Any:
    """Keep PyYAML's safe constructors and fall back on pure Python installs."""
    return yaml.load(stream, Loader=SafeLoader)

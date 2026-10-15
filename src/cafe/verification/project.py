"""Repository-bound configuration adapter; issue overrides cannot weaken adoption."""

from pathlib import Path
from typing import Sequence

import yaml

from cafe.utils.yaml_utils import safe_load
from cafe.verification.profiles import ProfileRequest, load_profile
from cafe.verification.receipt import VerificationReceiptError


def resolve_profile_request(
    root: Path,
    profile_file: Path | None = None,
    profile: str | None = None,
    extras: Sequence[str] = (),
) -> ProfileRequest | None:
    """Resolve explicit CLI binding first, then only the project configuration."""
    if profile_file is not None or profile is not None:
        if profile_file is None or not profile:
            raise VerificationReceiptError("--profile-file and --profile must be supplied together")
        return load_profile(root, profile_file, profile, extras)
    config_path = root / ".cafe/config.yaml"
    if not config_path.exists():
        if extras:
            raise VerificationReceiptError("--add-suite requires a profile binding")
        return None
    try:
        config = safe_load(config_path.read_text())
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise VerificationReceiptError(f"cannot read project configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise VerificationReceiptError("project configuration must be an object")
    if "verification" not in config:
        if extras:
            raise VerificationReceiptError("--add-suite requires a profile binding")
        return None
    binding = config["verification"]
    if (
        not isinstance(binding, dict)
        or set(binding) != {"profile_file", "profile"}
        or not all(isinstance(x, str) and x for x in binding.values())
    ):
        raise VerificationReceiptError("verification requires profile_file and profile strings")
    return load_profile(root, Path(binding["profile_file"]), binding["profile"], extras)

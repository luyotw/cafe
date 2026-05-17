"""Crew configuration management for CAFE."""

from pathlib import Path
from typing import Any, Dict, List

import yaml

from cafe.core.types import AgentCLI, CliEntry

# Keys that are not phase names in a role config dict
_RESERVED_ROLE_KEYS = frozenset(
    {"cli", "model", "name", "session_id", "backup", "models", "clis"}
)


def normalize_role_config(role: Dict[str, Any]) -> List[CliEntry]:
    """Convert any crew.yaml role dict into an ordered CliEntry chain.

    New format (clis list) takes priority over old format (cli/backup/models).
    Unknown CLI strings are silently ignored.  Duplicate CLIs are deduplicated
    (first occurrence wins).

    Returns an empty list when no valid primary CLI can be found.
    """
    if not isinstance(role, dict):
        return []

    # New format: use clis list directly
    raw_clis = role.get("clis")
    if isinstance(raw_clis, list):
        seen: set = set()
        entries: List[CliEntry] = []
        for item in raw_clis:
            if not isinstance(item, dict):
                continue
            cli_str = item.get("cli")
            try:
                cli = AgentCLI(cli_str)
            except (ValueError, TypeError):
                continue
            if cli in seen:
                continue
            seen.add(cli)
            phase_models = {
                k: str(v)
                for k, v in item.items()
                if k not in _RESERVED_ROLE_KEYS and isinstance(v, str)
            }
            entries.append(
                CliEntry(
                    cli=cli,
                    model=item.get("model") or None,
                    phase_models=phase_models,
                )
            )
        return entries

    # Old format: build chain from cli / backup / models
    primary_cli_str = role.get("cli")
    if not primary_cli_str:
        return []
    try:
        primary_cli = AgentCLI(primary_cli_str)
    except ValueError:
        return []

    # Phase overrides from models: dict (per-CLI per-phase)
    models_raw = role.get("models", {}) or {}
    if not isinstance(models_raw, dict):
        models_raw = {}

    def _phase_models_from_models_dict(cli_value: str) -> Dict[str, str]:
        raw = models_raw.get(cli_value, {}) or {}
        if not isinstance(raw, dict):
            return {}
        return {k: str(v) for k, v in raw.items() if isinstance(v, str)}

    # Phase overrides from role-level phase keys: spec: {model: X}
    # These apply to the primary CLI only (backward compat with existing preset format).
    def _phase_models_from_role_keys() -> Dict[str, str]:
        result: Dict[str, str] = {}
        for key, val in role.items():
            if key in _RESERVED_ROLE_KEYS:
                continue
            if isinstance(val, dict):
                m = val.get("model")
                if m and isinstance(m, str):
                    result[key] = m
        return result

    primary_phase_models = {
        **_phase_models_from_role_keys(),
        **_phase_models_from_models_dict(primary_cli.value),
    }

    seen_old: set = {primary_cli}
    chain: List[CliEntry] = [
        CliEntry(
            cli=primary_cli,
            model=role.get("model") or None,
            phase_models=primary_phase_models,
        )
    ]

    for cli_str in (role.get("backup") or []):
        try:
            cli = AgentCLI(cli_str)
        except ValueError:
            continue
        if cli in seen_old:
            continue
        seen_old.add(cli)
        chain.append(
            CliEntry(
                cli=cli,
                model=None,
                phase_models=_phase_models_from_models_dict(cli.value),
            )
        )

    return chain


class CrewManager:
    """Manages crew.yaml — the agent role-to-CLI mapping file."""

    def __init__(self, cafe_dir: Path = Path(".cafe")) -> None:
        self.cafe_dir = cafe_dir

    @property
    def crew_file(self) -> Path:
        return self.cafe_dir / "crew.yaml"

    @property
    def config_file(self) -> Path:
        return self.cafe_dir / "config.yaml"

    def exists(self) -> bool:
        return self.crew_file.exists()

    def load(self) -> Dict[str, Any]:
        """Load crew configuration.

        Priority: crew.yaml > config.yaml agents: section > empty dict.
        """
        if self.crew_file.exists():
            try:
                data = yaml.safe_load(self.crew_file.read_text())
                return data if isinstance(data, dict) else {}
            except yaml.YAMLError:
                return {}

        if self.config_file.exists():
            try:
                data = yaml.safe_load(self.config_file.read_text())
                if isinstance(data, dict):
                    agents = data.get("agents")
                    if isinstance(agents, dict):
                        return agents
            except yaml.YAMLError:
                pass

        return {}

    def save(self, agents: Dict[str, Any]) -> None:
        """Write agents config to crew.yaml."""
        self.cafe_dir.mkdir(parents=True, exist_ok=True)
        with open(self.crew_file, "w") as f:
            yaml.dump(agents, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

"""Configuration management for AAF."""

from pathlib import Path
from typing import Any, Dict, Optional
import yaml

from aaf.core.types import WorkflowMode, AgentCLI


class ConfigError(Exception):
    """Configuration error."""

    pass


class ConfigManager:
    """Manages AAF configuration."""

    def __init__(self, config_dir: str = ".aaf") -> None:
        """Initialize config manager.

        Args:
            config_dir: Directory for configuration files
        """
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.yaml"
        self._config: Optional[Dict[str, Any]] = None

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file.

        Returns:
            Configuration dictionary

        Raises:
            ConfigError: If config file is invalid
        """
        if not self.config_file.exists():
            self._config = self.get_default_config()
            return self._config

        try:
            with open(self.config_file, "r") as f:
                self._config = yaml.safe_load(f)
                return self._config
        except yaml.YAMLError as e:
            raise ConfigError(f"Failed to load config: {e}") from e

    def get_default_config(self) -> Dict[str, Any]:
        """Get default configuration.

        Returns:
            Default configuration dictionary
        """
        return {
            "agents": {
                "pm": {
                    "name": "Roger",
                    "cli": "copilot",
                },
                "dev": {
                    "name": "David",
                    "cli": "copilot",
                },
                "reviewer": {
                    "name": "Richard",
                    "cli": "copilot",
                },
            },
            "defaults": {
                "workflow_mode": "local",
                "interactive": True,
            },
        }

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration to file.

        Args:
            config: Configuration to save
        """
        with open(self.config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        self._config = config

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate configuration.

        Args:
            config: Configuration to validate

        Returns:
            True if valid

        Raises:
            ConfigError: If configuration is invalid
        """
        # Check required fields
        required_fields = ["workflow_mode", "agents"]
        for field in required_fields:
            if field not in config:
                raise ConfigError(f"Missing required field: {field}")

        # Validate workflow_mode
        try:
            WorkflowMode(config["workflow_mode"])
        except ValueError:
            raise ConfigError(
                f"Invalid workflow_mode: {config['workflow_mode']}. "
                f"Must be one of: {[m.value for m in WorkflowMode]}"
            )

        # Validate agents
        if isinstance(config["agents"], list):
            for agent in config["agents"]:
                if "cli" in agent:
                    try:
                        AgentCLI(agent["cli"])
                    except ValueError:
                        raise ConfigError(
                            f"Invalid agent CLI: {agent['cli']}. "
                            f"Must be one of: {[t.value for t in AgentCLI]}"
                        )

        return True

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value.

        Args:
            key: Configuration key (supports dot notation for nested keys)
            default: Default value if key not found

        Returns:
            Configuration value
        """
        if self._config is None:
            self.load_config()

        # Handle nested keys with dot notation
        keys = key.split(".")
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def set(self, key: str, value: Any) -> None:
        """Set configuration value.

        Args:
            key: Configuration key (supports dot notation for nested keys)
            value: Value to set

        Note:
            Supports aliases for agent CLI shortcuts:
            - 'pm' → 'agents.pm.cli'
            - 'dev' → 'agents.dev.cli'
            - 'reviewer' → 'agents.reviewer.cli'
        """
        if self._config is None:
            self.load_config()

        # Apply alias logic for agent CLI shortcuts
        key = self._resolve_alias(key)

        # Handle nested keys with dot notation
        keys = key.split(".")
        config = self._config

        # Navigate to the parent of the target key
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # Set the value
        config[keys[-1]] = value

        # Save to file
        self.save_config(self._config)

    def _resolve_alias(self, key: str) -> str:
        """Resolve key aliases for convenience.

        Args:
            key: Original key

        Returns:
            Resolved key

        Examples:
            'pm' → 'agents.pm.cli'
            'dev' → 'agents.dev.cli'
            'pm.name' → 'agents.pm.name' (no change needed, already has agent prefix)
        """
        # Agent CLI shortcuts: pm, dev, reviewer (without dots)
        if key in ['pm', 'dev', 'reviewer']:
            return f'agents.{key}.cli'

        # If it starts with agent name but not agents., add agents prefix
        # e.g., 'pm.cli' → 'agents.pm.cli', 'pm.name' → 'agents.pm.name'
        for agent in ['pm', 'dev', 'reviewer']:
            if key.startswith(f'{agent}.'):
                return f'agents.{key}'

        return key

    def reset(self) -> None:
        """Reset configuration to default values."""
        self._config = self.get_default_config()
        self.save_config(self._config)

    def merge_config(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Merge two configuration dictionaries.

        Args:
            base: Base configuration
            override: Configuration to override base

        Returns:
            Merged configuration (does not modify inputs)
        """
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # Recursively merge nested dictionaries
                result[key] = self.merge_config(result[key], value)
            else:
                result[key] = value

        return result

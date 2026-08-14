"""Configuration management for CAFE."""

from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess
import yaml
import json
import time



class ConfigError(Exception):
    """Configuration error."""


def validate_directories_exist(dirs: List[str], base_dir: Path) -> None:
    """Verify each allowed directory is usable.

    Relative entries must resolve to an existing directory under base_dir.
    Absolute entries are operator-provided (CLI --add-dir / config
    allowed_directories, never agent-controlled) and are allowed to live
    outside base_dir so cross-repo playbooks can grant access to a sibling
    repo; they only need to be an existing directory.

    Raises:
        ConfigError: if any entry is missing, not a directory, or a relative
        path that escapes base_dir.
    """
    base_dir = base_dir.resolve()
    invalid = []
    for directory in dirs:
        directory_path = Path(directory)
        if directory_path.is_absolute():
            # Operator-provided external dir: allow if it exists and is a directory.
            if not directory_path.resolve().is_dir():
                invalid.append(directory)
            continue
        resolved = (base_dir / directory_path).resolve()
        if (
            ".." in directory_path.parts
            or not resolved.is_relative_to(base_dir)
            or not resolved.is_dir()
        ):
            invalid.append(directory)
    if invalid:
        raise ConfigError(
            "Allowed directories must be existing directories (relative ones "
            f"under {base_dir}, or absolute ones that exist): {', '.join(invalid)}"
        )


def get_global_cafe_dir() -> Path:
    """Get global CAFE directory path.
    
    Returns:
        Path to ~/.cafe directory (creates if not exists)
    """
    global_dir = Path.home() / ".cafe"
    global_dir.mkdir(parents=True, exist_ok=True)
    return global_dir


def ensure_global_directories() -> None:
    """Ensure global CAFE directories exist.

    Creates the following directory structure:
    ~/.cafe/
    ├── agents/
    │   ├── pm/
    │   ├── developer/
    │   └── reviewer/
    └── templates/
        ├── plan/
        └── spec/
    """
    global_dir = get_global_cafe_dir()

    # Create agent subdirectories
    for role in ["pm", "developer", "reviewer"]:
        (global_dir / "agents" / role).mkdir(parents=True, exist_ok=True)

    # Create template subdirectories
    for template_type in ["plan", "spec"]:
        (global_dir / "templates" / template_type).mkdir(parents=True, exist_ok=True)


def resolve_sync_github_config(
    cli_value: Optional[bool],
    config_value: Optional[bool],
    has_issue_id: bool
) -> bool:
    """Resolve sync_github configuration value with proper priority.

    Priority order:
    1. CLI-provided value (--sync-github/--no-sync-github)
    2. Config file value (from issue.yaml)
    3. Default based on issue_id presence (True if issue_id exists, False otherwise)

    Args:
        cli_value: Value from CLI flag (None if not provided)
        config_value: Value from config file (None if not in config)
        has_issue_id: Whether issue_id is present in config

    Returns:
        Resolved boolean value for sync_github
    """
    # Priority: CLI value > config value > default based on issue_id
    if cli_value is not None:
        # CLI value takes precedence
        return cli_value
    elif config_value is not None:
        # Use value from config
        return config_value
    elif has_issue_id:
        # Default to True if issue_id is present (backward compatibility)
        return True
    else:
        # Default to False if no issue_id
        return False


class ConfigManager:
    """Manages CAFE configuration."""

    def __init__(self, config_dir: str = ".cafe") -> None:
        """Initialize config manager.

        Args:
            config_dir: Directory for configuration files
        """
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.yaml"
        self._config: Optional[Dict[str, Any]] = None

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Deep merge override into base. Override values take precedence."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigManager._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _get_issue_config(self) -> Optional[Dict[str, Any]]:
        """Load issue-level config.yaml if it exists for the current branch."""
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if not branch or branch == "HEAD":
                return None
            issue_config = self.config_dir / "issues" / branch / "config.yaml"
            if issue_config.exists():
                with open(issue_config, "r") as f:
                    return yaml.safe_load(f)
        except Exception:
            pass
        return None

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file.

        Loads .cafe/config.yaml as the base config, then overlays
        .cafe/issues/{current_branch}/config.yaml if it exists.

        Returns:
            Configuration dictionary

        Raises:
            ConfigError: If config file is invalid or doesn't exist
        """
        # Try issue-level override first (may exist without base config after restore)
        issue_config = self._get_issue_config()

        if not self.config_file.exists():
            if issue_config:
                self._config = issue_config
                return self._config
            raise ConfigError(
                f"Configuration file not found: {self.config_file}\n"
                "Please run 'cafe init' first to initialize CAFE."
            )

        try:
            with open(self.config_file, "r") as f:
                self._config = yaml.safe_load(f)
            if issue_config:
                self._config = self._deep_merge(self._config, issue_config)
            return self._config
        except yaml.YAMLError as e:
            raise ConfigError(f"Failed to load config: {e}") from e

    def get_default_config(self) -> Dict[str, Any]:
        """Get default configuration.

        Returns:
            Default configuration dictionary
        """
        return {
            "python_bin": "python3",
        }

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration to file.

        Args:
            config: Configuration to save
        """
        with open(self.config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
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
        if not isinstance(config, dict):
            raise ConfigError("Configuration must be a mapping")
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

    def get_allowed_directories(self) -> List[str]:
        """Return the project-level allowed_directories list from config.

        Returns an empty list when the key is absent or not a list, so callers
        never need to guard against None or unexpected types.
        """
        value = self.get("allowed_directories", [])
        if not isinstance(value, list):
            return []
        return value

    def list_allowed_directories(self) -> List[str]:
        """Return the current allowed_directories list."""
        return self.get_allowed_directories()

    def add_allowed_directory(self, path: str) -> bool:
        """Append a normalized path to allowed_directories if not already present."""
        normalized = self._normalize_allowed_directory_path(path)
        current = self.get_allowed_directories()
        existing = {self._normalize_allowed_directory_path(entry) for entry in current}
        if normalized in existing:
            return False
        self.set("allowed_directories", [*current, normalized])
        return True

    def remove_allowed_directory(self, path: str) -> bool:
        """Remove the first allowed_directories entry matching the normalized path."""
        normalized = self._normalize_allowed_directory_path(path)
        current = self.get_allowed_directories()
        updated: List[str] = []
        removed = False
        for entry in current:
            if not removed and self._normalize_allowed_directory_path(entry) == normalized:
                removed = True
                continue
            updated.append(entry)
        if not removed:
            return False
        self.set("allowed_directories", updated)
        return True

    def _normalize_allowed_directory_path(self, path: str) -> str:
        """Normalize a user-provided directory path for storage and comparison."""
        expanded = Path(path.strip()).expanduser()
        try:
            resolved = expanded.resolve()
        except (OSError, RuntimeError):
            resolved = expanded

        cwd = Path.cwd()
        try:
            if resolved.is_relative_to(cwd):
                return resolved.relative_to(cwd).as_posix()
        except ValueError:
            pass
        return resolved.as_posix()

    def set(self, key: str, value: Any) -> None:
        """Set configuration value.

        Args:
            key: Configuration key (supports dot notation for nested keys)
            value: Value to set

        """
        if self._config is None:
            self.load_config()

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


def get_last_update_check_file() -> Path:
    """Get path to last update check timestamp file.

    Returns:
        Path to ~/.cafe/last_update_check.json
    """
    return get_global_cafe_dir() / "last_update_check.json"


def should_check_for_updates() -> bool:
    """Check if enough time has passed since last update check.

    Checks if more than 24 hours have passed since the last update check.
    Returns True if check file doesn't exist or if 24+ hours have passed.

    Returns:
        True if update check should be performed, False otherwise
    """
    check_file = get_last_update_check_file()

    if not check_file.exists():
        return True

    try:
        with open(check_file, "r") as f:
            data = json.load(f)
            last_check = data.get("timestamp", 0)

        # Check if 24 hours (86400 seconds) have passed
        elapsed = time.time() - last_check
        return elapsed > 86400
    except (json.JSONDecodeError, IOError):
        # If file is corrupted, allow update check
        return True


def update_last_check_timestamp() -> None:
    """Update the last update check timestamp.

    Writes current timestamp to ~/.cafe/last_update_check.json
    """
    check_file = get_last_update_check_file()
    check_file.parent.mkdir(parents=True, exist_ok=True)

    with open(check_file, "w") as f:
        json.dump({"timestamp": time.time()}, f)

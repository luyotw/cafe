"""Shared helper functions and constants used by the CLI module."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

from cafe.agents.manager import AgentManager
from cafe.core.types import AgentCLI, AgentConfig
from cafe.services.delta_display import DeltaDisplay
from cafe.utils.config import ConfigManager

VALID_CONTENT_TYPES = [
    "context",
    "output",
    "streaming",
    "error",
    "status",
    "iterations",
    "checklist",
    "user_input",
    "questions",
]

CONTENT_TYPE_FILE_MAP = {
    "context": "context.json",
    "output": "output.md",
    "streaming": "streaming.jsonl",
    "error": "error.json",
    "status": "status.json",
    "iterations": "iterations.jsonl",
    "checklist": "checklist.md",
    "user_input": "user_input.md",
    "questions": "questions.xml",
}


def check_agent_clis_available(config_manager: ConfigManager) -> List[str]:
    """Check if all configured agent CLIs are installed."""
    pm_config = config_manager.get("agents.pm", {"name": "Roger", "cli": "copilot"})
    dev_config = config_manager.get("agents.developer", {"name": "David", "cli": "copilot"})
    reviewer_config = config_manager.get("agents.reviewer", {"name": "Richard", "cli": "copilot"})

    required_clis = [pm_config["cli"], dev_config["cli"], reviewer_config["cli"]]

    missing_clis = []
    for cli in required_clis:
        if shutil.which(cli) is None and cli not in missing_clis:
            missing_clis.append(cli)

    return missing_clis


def setup_agents(
    config_manager: ConfigManager,
    issue_name: Optional[str] = None,
    phase_name: Optional[str] = None,
) -> AgentManager:
    """Setup agent manager with configured role agents."""
    agent_manager = AgentManager(issue_name=issue_name)

    pm_config = config_manager.get("agents.pm", {"name": "Roger", "cli": "copilot"})
    dev_config = config_manager.get("agents.developer", {"name": "David", "cli": "copilot"})
    reviewer_config = config_manager.get("agents.reviewer", {"name": "Richard", "cli": "copilot"})

    def resolve_model(config: dict, phase: Optional[str]) -> Optional[str]:
        model = None
        if phase and phase in config:
            phase_config = config[phase]
            if isinstance(phase_config, dict):
                model = phase_config.get("model")
        if model is None:
            model = config.get("model")
        return model

    def resolve_backup_clis(config: dict, primary_cli: AgentCLI) -> List[AgentCLI]:
        backup_raw = config.get("backup", [])
        seen = {primary_cli}
        result = []
        for cli_str in backup_raw:
            try:
                cli = AgentCLI(cli_str)
            except ValueError:
                continue
            if cli not in seen:
                seen.add(cli)
                result.append(cli)
        return result

    def resolve_models_config(config: dict) -> Dict[str, Dict[str, str]]:
        raw = config.get("models", {})
        if not isinstance(raw, dict):
            return {}
        result: Dict[str, Dict[str, str]] = {}
        for cli_name, phase_models in raw.items():
            if isinstance(phase_models, dict):
                result[cli_name] = {k: str(v) for k, v in phase_models.items()}
        return result

    pm_cli = AgentCLI(pm_config["cli"])
    agent_manager.register_agent(
        AgentConfig(
            name=pm_config["name"],
            cli=pm_cli,
            model=resolve_model(pm_config, phase_name),
            backup_clis=resolve_backup_clis(pm_config, pm_cli),
            models_config=resolve_models_config(pm_config),
        )
    )
    dev_cli = AgentCLI(dev_config["cli"])
    agent_manager.register_agent(
        AgentConfig(
            name=dev_config["name"],
            cli=dev_cli,
            model=resolve_model(dev_config, phase_name),
            backup_clis=resolve_backup_clis(dev_config, dev_cli),
            models_config=resolve_models_config(dev_config),
        )
    )
    reviewer_cli = AgentCLI(reviewer_config["cli"])
    agent_manager.register_agent(
        AgentConfig(
            name=reviewer_config["name"],
            cli=reviewer_cli,
            model=resolve_model(reviewer_config, phase_name),
            backup_clis=resolve_backup_clis(reviewer_config, reviewer_cli),
            models_config=resolve_models_config(reviewer_config),
        )
    )

    return agent_manager


def get_latest_versioned_file(phase_name: str, issue_name: str) -> Optional[Path]:
    """Get the latest output.md for a phase."""
    phase_dir = Path(f".cafe/issues/{issue_name}/{phase_name}")
    if not phase_dir.exists():
        return None

    output_files = sorted(phase_dir.glob("iteration_*/output.md"))
    if output_files:
        return output_files[-1]
    return None


def find_latest_iteration_dir(phase_dir: Path) -> Optional[Path]:
    """Find latest iteration directory by numeric suffix."""
    iteration_dirs = sorted(phase_dir.glob("iteration_*"))
    valid: List[tuple[int, Path]] = []
    for path in iteration_dirs:
        if not path.is_dir():
            continue
        try:
            number = int(path.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        valid.append((number, path))
    if not valid:
        return None
    valid.sort(key=lambda item: item[0])
    return valid[-1][1]


def resolve_iteration_index(iteration_numbers: List[int], iteration_input: int) -> int:
    """Resolve concrete iteration number from positive/zero/negative input."""
    if not iteration_numbers:
        raise ValueError("No iterations available")

    if iteration_input == 0:
        return iteration_numbers[-1]
    if iteration_input > 0:
        if iteration_input not in iteration_numbers:
            raise ValueError(
                f"Iteration {iteration_input} not found. "
                f"Available iterations: {iteration_numbers}"
            )
        return iteration_input

    try:
        return iteration_numbers[iteration_input - 1]
    except IndexError:
        raise ValueError(
            f"Iteration index {iteration_input} out of range. "
            f"Available iterations: {iteration_numbers}"
        )


def resolve_iteration_number(phase_dir: Path, iteration_input: int, content_type: str) -> int:
    """Resolve iteration number that contains the requested content file."""
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    all_iteration_files = sorted(phase_dir.glob("iteration_*/context.json"))
    if not all_iteration_files:
        raise ValueError(f"No iterations found in {phase_dir}")

    all_iteration_numbers = []
    for file_path in all_iteration_files:
        dir_name = file_path.parent.name
        if dir_name.startswith("iteration_"):
            try:
                num = int(dir_name.split("_")[1])
                all_iteration_numbers.append(num)
            except (IndexError, ValueError):
                continue

    if not all_iteration_numbers:
        raise ValueError(f"No valid iterations found in {phase_dir}")

    iteration_numbers_with_file = []
    for iter_num in all_iteration_numbers:
        iteration_dir = phase_dir / f"iteration_{iter_num:03d}"
        file_path = iteration_dir / filename
        if file_path.exists():
            iteration_numbers_with_file.append(iter_num)

    if not iteration_numbers_with_file:
        raise ValueError(
            f"No iterations found with file '{filename}'. "
            f"Available iterations: {all_iteration_numbers}"
        )

    return resolve_iteration_index(iteration_numbers_with_file, iteration_input)


def get_show_file_path(phase_dir: Path, iteration: int, content_type: str) -> Path:
    """Get path for a show-command content type."""
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    if content_type in ["status", "iterations"]:
        return phase_dir / filename

    iteration_dir = phase_dir / f"iteration_{iteration:03d}"
    return iteration_dir / filename


def get_latest_review_iteration(issue_name: str) -> int:
    """Get latest review iteration number from directory names."""
    review_dir = Path(f".cafe/issues/{issue_name}/review")
    if not review_dir.exists():
        return 0

    iteration_dirs = sorted(review_dir.glob("iteration_*"))
    if not iteration_dirs:
        return 0

    latest_dir = iteration_dirs[-1]
    try:
        return int(latest_dir.name.split("_")[1])
    except (IndexError, ValueError):
        return 0


def display_iteration_delta(
    iteration_count: int,
    output_file: Optional[str],
    console: Console,
) -> None:
    """Display delta between current and previous iteration output files."""
    if iteration_count > 1 and output_file:
        current_file = Path(output_file)
        iteration_dir = current_file.parent
        phase_dir = iteration_dir.parent
        prev_iteration_num = iteration_count - 1
        previous_file = phase_dir / f"iteration_{prev_iteration_num:03d}" / "output.md"

        delta_display = DeltaDisplay()
        delta_display.display_delta(current_file, previous_file, console)

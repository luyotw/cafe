"""Show 命令 - 顯示迭代檔案內容"""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from cafe.core.git import GitOperations

console = Console()

app = typer.Typer()

# Constants for cafe show command
VALID_PHASES = ["spec", "plan", "develop", "review", "pr"]
VALID_CONTENT_TYPES = [
    "context", "output", "streaming", "error",
    "title", "body", "status", "iterations"
]
CONTENT_TYPE_FILE_MAP = {
    "context": "context.json",
    "output": "output.md",
    "streaming": "streaming.jsonl",
    "error": "error.json",
    "title": "title.txt",
    "body": "body.md",
    "status": "status.json",
    "iterations": "iterations.jsonl",
}


def _resolve_iteration_number(phase_dir: Path, iteration_input: int, content_type: str) -> int:
    """Resolve iteration number based on iterations that have the specified file.

    Args:
        phase_dir: Phase directory path (e.g., .cafe/issues/issue84/spec)
        iteration_input: User input iteration number (can be positive, 0, negative)
        content_type: Content type to search for (output, context, etc.)

    Returns:
        Actual iteration number (positive integer)

    Raises:
        ValueError: When iteration number does not exist or file not found
    """
    # Get filename for the content type
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    # Get all iteration directories (verified by context.json file)
    all_iteration_files = sorted(phase_dir.glob("iteration_*/context.json"))

    if not all_iteration_files:
        raise ValueError(f"No iterations found in {phase_dir}")

    # Extract all iteration numbers
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

    # Find iterations that have the requested file
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

    # Handle different iteration number formats
    if iteration_input == 0:
        # Zero means latest iteration with the file
        return iteration_numbers_with_file[-1]
    elif iteration_input > 0:
        # Positive number used directly, but need to verify existence
        if iteration_input not in iteration_numbers_with_file:
            raise ValueError(
                f"Iteration {iteration_input} not found or does not have '{filename}'. "
                f"Iterations with this file: {iteration_numbers_with_file}"
            )
        return iteration_input
    else:
        # Negative number: -1 means one before latest, -2 means two before, etc.
        # Since 0 already represents latest (iteration_numbers_with_file[-1]),
        # we need to offset: -1 -> [-2], -2 -> [-3], etc.
        try:
            return iteration_numbers_with_file[iteration_input - 1]
        except IndexError:
            raise ValueError(
                f"Iteration index {iteration_input} out of range. "
                f"Iterations with '{filename}': {iteration_numbers_with_file}"
            )


def _get_show_file_path(phase_dir: Path, iteration: int, content_type: str) -> Path:
    """Get file path for specified content type.

    Args:
        phase_dir: Phase directory path
        iteration: Iteration number (resolved to positive integer)
        content_type: Content type (context, output, streaming, etc.)

    Returns:
        Complete file path
    """
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    # status and iterations are located at phase directory root level
    if content_type in ["status", "iterations"]:
        return phase_dir / filename
    else:
        # Other files are located in iteration directory
        iteration_dir = phase_dir / f"iteration_{iteration:03d}"
        return iteration_dir / filename


def _get_latest_review_iteration(issue_name: str) -> int:
    """Get the latest review iteration number from iteration directories.

    Args:
        issue_name: Issue name

    Returns:
        Latest iteration number, or 0 if no iterations exist
    """
    review_dir = Path(f".cafe/issues/{issue_name}/review")
    if not review_dir.exists():
        return 0

    # Find all iteration directories
    iteration_dirs = sorted(review_dir.glob("iteration_*"))
    if not iteration_dirs:
        return 0

    # Extract iteration number from the latest directory (e.g., iteration_005 -> 5)
    latest_dir = iteration_dirs[-1]
    try:
        iteration_num = int(latest_dir.name.split("_")[1])
        return iteration_num
    except (IndexError, ValueError):
        return 0


@app.command()
def show(
    phase_name: str = typer.Argument(
        ...,
        help="Phase name (spec, plan, develop, review, pr)"
    ),
    content_type: Optional[str] = typer.Argument(
        None,
        help="Content type (default: output)"
    ),
    iteration: int = typer.Option(
        0,
        "--iteration", "-i",
        help="Iteration number (positive, 0=latest, negative=relative index)"
    ),
) -> None:
    """Display iteration file contents.

    Shows the content of files from different phases and iterations.

    Examples:
        cafe show spec                    # Show latest spec output
        cafe show spec context            # Show latest spec context
        cafe show spec output -i 2        # Show spec iteration 2 output
        cafe show spec context -i -1      # Show previous spec iteration context
        cafe show plan status -i -2       # Show plan status 2 iterations ago
    """
    # Validate phase name
    if phase_name not in VALID_PHASES:
        console.print(f"[red]Error: Invalid phase '{phase_name}'[/red]")
        console.print(f"[dim]Valid phases: {', '.join(VALID_PHASES)}[/dim]")
        raise typer.Exit(1)

    # Set default content type
    if content_type is None:
        content_type = "output"

    # Validate content type
    if content_type not in VALID_CONTENT_TYPES:
        console.print(f"[red]Error: Invalid content type '{content_type}'[/red]")
        console.print(f"[dim]Valid types: {', '.join(VALID_CONTENT_TYPES)}[/dim]")
        raise typer.Exit(1)

    # Get current branch name (issue_name)
    try:
        git_ops = GitOperations()
        issue_name = git_ops.get_current_branch()
    except Exception as e:
        console.print(f"[red]Error: Failed to get current branch: {e}[/red]")
        raise typer.Exit(1)

    # Build phase directory path
    cafe_dir = Path.cwd() / ".cafe"
    phase_dir = cafe_dir / "issues" / issue_name / phase_name

    # Check if phase directory exists
    if not phase_dir.exists():
        console.print(f"[red]Error: Phase directory not found: {phase_dir}[/red]")
        console.print(f"[dim]The '{phase_name}' phase has not been executed yet[/dim]")
        raise typer.Exit(1)

    try:
        # Resolve iteration number (only for non-status/iterations files)
        if content_type not in ["status", "iterations"]:
            resolved_iteration = _resolve_iteration_number(phase_dir, iteration, content_type)
            # Get file path
            file_path = _get_show_file_path(phase_dir, resolved_iteration, content_type)
        else:
            # status and iterations don't need iteration number
            file_path = _get_show_file_path(phase_dir, 0, content_type)
            resolved_iteration = None

        # Check if file exists
        if not file_path.exists():
            console.print(f"[red]Error: File not found: {file_path}[/red]")
            if resolved_iteration is not None:
                console.print(f"[dim]File '{content_type}' does not exist in iteration {resolved_iteration}[/dim]")
            raise typer.Exit(1)

        # Read and display file content
        try:
            content = file_path.read_text(encoding="utf-8")

            # Use syntax highlighting for JSON files
            if file_path.suffix == ".json":
                try:
                    json_data = json.loads(content)
                    console.print_json(data=json_data)
                except json.JSONDecodeError:
                    # If JSON parsing fails, output raw content
                    console.print(content)
            else:
                # Output other files directly
                console.print(content)

        except UnicodeDecodeError:
            console.print(f"[red]Error: Failed to read file (not UTF-8 encoded)[/red]")
            raise typer.Exit(1)

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)

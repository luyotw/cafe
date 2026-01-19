"""Pull Request creation phase."""

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

import yaml

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.ui.inquirer_prompts import prompt_confirm
from cafe.utils.github import GitHubOps, GitHubError
from cafe.utils.prompt_utils import format_checklist_instruction


class PRPhase(Phase):
    """Phase 5: Pull Request creation."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        github_ops: GitHubOps,
        spec_file: str,
        issue_id: Optional[str] = None,
        issue_name: Optional[str] = None,
        dev_agent: str = "David",
        draft: bool = True,
        custom_title: Optional[str] = None,
        custom_body: Optional[str] = None,
        update: bool = False,
        force_push: bool = False,
        interactive: bool = True,
        base_branch: Optional[str] = None,
    ) -> None:
        """Initialize PR phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            github_ops: GitHub operations
            spec_file: Path to spec file (deprecated - will be computed from latest version)
            issue_id: GitHub issue ID (optional, for linking PR to issue)
            issue_name: Issue name (for branch naming)
            dev_agent: Developer agent name (default: David)
            draft: Create as draft PR (default: True)
            custom_title: Custom PR title (None for auto-generation)
            custom_body: Custom PR body (None for auto-generation)
            update: Force update existing PR title/body (default: False)
            force_push: Force push to remote (default: False)
            interactive: Enable interactive mode (default: True)
            base_branch: Target base branch (None for auto-detection from config)
        """
        super().__init__(interactive=interactive)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.github_ops = github_ops
        self.issue_id = issue_id
        self.issue_name = issue_name
        self.dev_agent = dev_agent
        self.draft = draft
        self.custom_title = custom_title
        self.custom_body = custom_body
        self.update = update
        self.force_push = force_push

        # Get issue directory from current branch
        self.issue_dir = self._get_issue_dir(git_ops)

        # Get latest versioned spec file
        spec_dir = self.issue_dir / "spec"
        latest_spec = self._get_latest_versioned_file("spec", spec_dir)

        # Use latest versioned file if available, otherwise use provided path
        self.spec_file = str(latest_spec) if latest_spec else spec_file

        # Read base_branch from config if not provided via CLI
        if base_branch is not None:
            # CLI parameter takes precedence
            self.base_branch = base_branch
        else:
            # Try to read from config.yaml, fallback to "main"
            config_file = self.issue_dir / "issue.yaml"
            config_base = self._get_issue_config_value(config_file, "base_branch")
            self.base_branch = config_base if config_base else "main"

        # Set up history tracking (like other phases)
        self.phase_dir = self.issue_dir / "pr"
        self.history_dir = self.phase_dir / "history"
        self.phase_name = "pr"
        # Load iteration counter (same pattern as other phases)
        self.iteration = self._load_iteration_counter()

    def _get_latest_iteration_dir(self, pr_dir: Path) -> Optional[Path]:
        """Get the latest iteration directory in pr folder.

        Args:
            pr_dir: Path to the pr directory

        Returns:
            Path to latest iteration directory, or None if none exist
        """
        if not pr_dir.exists():
            return None

        # Find all iteration directories
        iteration_dirs = sorted(pr_dir.glob("iteration_*"))
        if not iteration_dirs:
            return None

        # Return the latest one (highest number)
        return iteration_dirs[-1]

    def _get_pr_review_timestamp(self) -> Optional["datetime"]:
        """Get PR review timestamp.

        For GitHub mode: use latest PR comment timestamp
        For local mode: use pr/status.json timestamp

        Returns:
            datetime object of PR review, or None if no review exists
        """
        from datetime import datetime

        # Try GitHub PR comments first (if pr_number is available)
        if hasattr(self, 'pr_number') and self.pr_number:
            try:
                from cafe.utils.github import get_pr_comments
                comments = get_pr_comments(self.pr_number)
                if comments:
                    latest_timestamp = None
                    for comment in comments:
                        timestamp_str = comment.created_at
                        if timestamp_str.endswith('Z'):
                            timestamp_str = timestamp_str.replace('Z', '+00:00')
                        comment_time = datetime.fromisoformat(timestamp_str)

                        if latest_timestamp is None or comment_time > latest_timestamp:
                            latest_timestamp = comment_time

                    if latest_timestamp:
                        return latest_timestamp
            except Exception:
                pass  # Fall through to local mode

        # Fall back to local pr/status.json
        pr_status_file = self.issue_dir / "pr" / "status.json"
        if pr_status_file.exists():
            try:
                with open(pr_status_file, encoding='utf-8') as f:
                    pr_data = json.load(f)
                return datetime.fromisoformat(pr_data["timestamp"])
            except Exception:
                pass

        return None

    def _check_if_develop_is_newer_than_pr(self) -> bool:
        """Check if develop phase timestamp is newer than last PR review.

        Works for both GitHub mode (PR comments) and local mode (pr/status.json).

        Returns:
            True if develop is newer (needs re-review), False otherwise
        """
        from datetime import datetime

        try:
            # Get develop timestamp
            develop_status_file = self.issue_dir / "develop" / "status.json"
            if not develop_status_file.exists():
                return False  # No develop status, no need to re-review

            with open(develop_status_file, encoding='utf-8') as f:
                develop_data = json.load(f)
            develop_time = datetime.fromisoformat(develop_data["timestamp"])

            # Get PR review timestamp (GitHub comments or local status.json)
            pr_time = self._get_pr_review_timestamp()
            if pr_time is None:
                # First PR review, need to review
                return True

            # If develop is newer than PR review, need to re-review
            return develop_time > pr_time

        except Exception:
            # On error, conservatively return True (re-review)
            return True

    def _save_progress(self, status_code) -> None:
        """Save phase progress to status.json.

        Args:
            status_code: Phase status code (CONFIRMED or NEEDS_CHANGES for PR phase)
        """
        from datetime import datetime
        from cafe.core.types import PhaseProgress
        from cafe.core.status_codes import PhaseStatusCode

        status_file = self.issue_dir / "pr" / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Both CONFIRMED and NEEDS_CHANGES are completion statuses for PR
        complete_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEEDS_CHANGES]
        phase_status = PhaseStatus.COMPLETED if status_code in complete_codes else PhaseStatus.IN_PROGRESS

        # Set end_time when phase completes
        end_time = datetime.now().astimezone() if phase_status == PhaseStatus.COMPLETED else None

        progress = PhaseProgress(
            phase=self.phase_name,
            status=phase_status,
            status_code=status_code.value,
            timestamp=datetime.now().astimezone(),
            iteration=self.iteration,
            message=f"PR phase completed with {status_code.value}" if phase_status == PhaseStatus.COMPLETED else f"Iteration {self.iteration}",
            end_time=end_time,
        )

        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

    def execute(self) -> PhaseResult:
        """Execute PR creation phase.

        Returns:
            Phase result
        """
        try:
            # Prompt for auto_create if not set (only in interactive mode)
            config_file = self.issue_dir / "issue.yaml"
            pr_auto_create = self._get_issue_config_value(config_file, "pr.auto_create")

            if pr_auto_create is None:
                if self.interactive:
                    # Interactive mode: ask user using shared prompt function
                    from cafe.ui.phase_prompts import prompt_and_save_auto_create
                    pr_auto_create = prompt_and_save_auto_create(config_file, "pr.auto_create")
                else:
                    # Non-interactive mode: default to True (GitHub PR mode)
                    pr_auto_create = True

            # If pr.auto_create is False, use local review mode
            if pr_auto_create is False:
                result = self._execute_local_review_mode()
                # Save progress if there's a status code
                if result.data and "status_code" in result.data:
                    from cafe.core.status_codes import PhaseStatusCode
                    status_code_str = result.data["status_code"]
                    # Convert string to PhaseStatusCode enum
                    status_code = PhaseStatusCode(status_code_str)
                    # iteration is already set in _execute_local_review_mode()
                    self._save_progress(status_code)
                return result

            # Otherwise, continue with GitHub PR creation
            # Increment iteration for this execution
            self.iteration += 1

            # Check gh CLI authentication status
            try:
                if not self.github_ops.check_gh_auth():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message="gh CLI is not authenticated. Please run: gh auth login",
                    )
            except GitHubError as e:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Failed to check gh authentication: {e}",
                )

            # Read issue_id from config if not provided
            if not self.issue_id:
                config_file = self.issue_dir / "issue.yaml"
                self.issue_id = self._get_issue_config_value(config_file, "issue_id")

            # Check requirements file exists
            req_path = Path(self.spec_file)
            if not req_path.exists():
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Spec file not found: {self.spec_file}",
                )

            # Get branch name
            branch_name = self._get_branch_name()

            # Check if there are unpushed commits before pushing
            if self.git_ops.has_unpushed_commits():
                unpushed_commits = self.git_ops.get_unpushed_commits()
                from rich.console import Console
                console = Console()
                console.print()
                console.print(f"[bold]Pushing {len(unpushed_commits)} unpushed commit(s) to remote...[/bold]")
                for commit in unpushed_commits:
                    console.print(f"  [dim]- {commit['hash'][:8]} {commit['message']}[/dim]")
                console.print()

                self.git_ops.push(branch_name, set_upstream=True, force=self.force_push)
            else:
                if not self.git_ops.has_upstream_branch():
                    from rich.console import Console
                    console = Console()
                    console.print()
                    console.print(f"[bold]Pushing branch '{branch_name}' to remote for the first time...[/bold]")
                    console.print()

                    self.git_ops.push(branch_name, set_upstream=True, force=self.force_push)
                else:
                    from rich.console import Console
                    console = Console()
                    console.print()
                    console.print(f"[yellow]ℹ️  No new commits to push - branch '{branch_name}' is already up to date[/yellow]")
                    console.print()

            # Check if PR already exists on GitHub
            existing_pr = self.github_ops.get_pr_for_branch(branch_name)

            if existing_pr:
                # PR already exists on GitHub
                pr_number = str(existing_pr["number"])
                pr_url = existing_pr["url"]

                if not self.update:
                    # Ask user if they want to update
                    if self.interactive:
                        from rich.console import Console
                        console = Console()
                        console.print(f"\n[yellow]⚠️  PR #{pr_number} already exists for branch '{branch_name}'.[/yellow]")
                        console.print(f"  URL: {pr_url}")
                        console.print()

                        try:
                            update_pr = prompt_confirm("Do you want to update it?", default=False)
                        except (KeyboardInterrupt, EOFError):
                            console.print("\n[dim]Cancelled[/dim]")
                            return PhaseResult(
                                status=PhaseStatus.COMPLETED,
                                message=f"Pull Request #{pr_number} already exists (no update)",
                                data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name},
                            )

                        if not update_pr:
                            return PhaseResult(
                                status=PhaseStatus.COMPLETED,
                                message=f"Pull Request #{pr_number} already exists (no update)",
                                data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name},
                            )
                    else:
                        # Non-interactive mode without --update flag, fail
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message=f"PR #{pr_number} already exists for branch '{branch_name}'. Use --update to update it.",
                        )

                # User wants to update or --update flag is set
                result, content = self._prepare_pr_content()
                if result:
                    return result
                pr_title, pr_body = content

                # Display updating message
                from rich.console import Console
                console = Console()
                console.print()
                console.print("[bold]Updating pull request...[/bold]")
                console.print()

                # Update existing PR
                self.github_ops.update_pr(pr_number, title=pr_title, body=pr_body)

                result = PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Pull Request #{pr_number} updated successfully",
                    data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name, "status_code": "CAFE_CONFIRMED"},
                )

                # Save progress for GitHub PR mode
                from cafe.core.status_codes import PhaseStatusCode
                self._save_progress(PhaseStatusCode.CONFIRMED)

                return result

            # PR doesn't exist, create new one
            result, content = self._prepare_pr_content()
            if result:
                return result
            pr_title, pr_body = content

            # Display creating message
            from rich.console import Console
            console = Console()
            console.print()
            console.print("[bold]Creating pull request...[/bold]")
            console.print()

            # Create PR using GitHub operations
            pr_url = self.github_ops.create_pr(
                title=pr_title, body=pr_body, draft=self.draft, base=self.base_branch
            )

            # Extract PR number from URL
            match = re.search(r"/pull/(\d+)", pr_url)
            if not match:
                raise RuntimeError(f"Failed to extract PR number from: {pr_url}")
            pr_number = match.group(1)

            # Add PR link to GitHub issue if issue_id is configured
            if self.issue_id:
                try:
                    comment = f"Pull Request created: #{pr_number}\n\n{pr_url}"
                    self.github_ops.add_issue_comment(self.issue_id, comment)
                except Exception as e:
                    # Don't fail the entire PR phase if commenting fails
                    # Just log the warning (user can manually add comment)
                    from rich.console import Console
                    console = Console()
                    console.print(f"[yellow]⚠️  Warning: Failed to add PR link to issue #{self.issue_id}: {e}[/yellow]")

            result = PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Pull Request #{pr_number} created successfully",
                data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name, "status_code": "CAFE_CONFIRMED"},
            )

            # Save progress for GitHub PR mode
            from cafe.core.status_codes import PhaseStatusCode
            self._save_progress(PhaseStatusCode.CONFIRMED)

            return result

        except FileNotFoundError as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"gh CLI not found: {e}",
            )
        except Exception as e:
            return self._handle_exception_in_execute(e, "PR phase failed")

    def _execute_local_review_mode(self) -> PhaseResult:
        """Execute local review mode (no GitHub PR).

        Returns:
            Phase result
        """
        from rich.console import Console
        from rich.syntax import Syntax
        from rich.panel import Panel
        from cafe.core.status_codes import PhaseStatusCode

        console = Console()

        # Check if we already have a status from previous run
        pr_dir = self.issue_dir / "pr"
        status_file = pr_dir / "status.json"

        if status_file.exists():
            try:
                with open(status_file, 'r', encoding='utf-8') as f:
                    status_data = json.load(f)

                previous_status_code = status_data.get("status_code")

                # If previously CONFIRMED, check if develop has newer changes
                if previous_status_code == PhaseStatusCode.CONFIRMED.value:
                    if not self._check_if_develop_is_newer_than_pr():
                        # No new changes, just return completion message
                        console.print()
                        console.print("[bold green]✅ Local review already completed and confirmed![/bold green]")
                        console.print()

                        return PhaseResult(
                            status=PhaseStatus.COMPLETED,
                            message="Local review completed - changes confirmed",
                            data={"status_code": PhaseStatusCode.CONFIRMED.value, "local_review": True},
                        )
                    # If develop is newer, continue with normal review flow

                # If previously NEEDS_CHANGES, check if develop has addressed the changes
                elif previous_status_code == PhaseStatusCode.NEEDS_CHANGES.value:
                    if not self._check_if_develop_is_newer_than_pr():
                        # Develop hasn't run since last PR review, no new changes to review
                        console.print()
                        console.print("[bold yellow]⏳ Waiting for changes to be addressed...[/bold yellow]")
                        console.print()
                        console.print("[dim]Last PR review requested changes, but no new development since then.[/dim]")
                        console.print("[dim]Next step: Run [bold]cafe develop --auto[/bold] to address the changes[/dim]")
                        console.print()

                        return PhaseResult(
                            status=PhaseStatus.COMPLETED,
                            message="Waiting for changes to be addressed",
                            data={"status_code": PhaseStatusCode.NEEDS_CHANGES.value, "local_review": True},
                        )
                    # If develop is newer, continue with normal review flow (developer may have addressed changes)

            except Exception:
                # If error reading status, continue with normal flow
                pass

        # Get git diff
        try:
            diff_output = self.git_ops.get_diff(self.base_branch, "HEAD")
        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Failed to get git diff: {e}",
            )

        # Display diff
        console.print()
        console.print(Panel.fit(
            "📋 Local Review Mode - Code Changes",
            style="bold cyan"
        ))
        console.print()

        if diff_output.strip():
            syntax = Syntax(diff_output, "diff", theme="monokai", line_numbers=False)
            console.print(syntax)
        else:
            console.print("[yellow]No changes to review[/yellow]")

        console.print()

        # Ask user for decision (c/r/m)
        if self.interactive:
            choice = self._ask_user_for_review_decision("code changes", agent_name="Reviewer")
        else:
            # Non-interactive mode not supported for local review
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message="Local review mode requires interactive mode",
            )

        # Process decision using base class method
        result = self._process_review_decision(
            choice=choice,
            prev_data={},
            phase_name="Local review",
            phase_specific_data={},
        )

        # If result is a string, it's a modification request
        if isinstance(result, str):
            modification_request = result

            # Save to versioned pr file
            pr_dir = self.issue_dir / "pr"
            pr_dir.mkdir(exist_ok=True)

            # Get next version number
            existing_files = sorted(pr_dir.glob("pr_*.md"))
            next_version = len(existing_files) + 1
            pr_file = pr_dir / f"pr_{next_version:03d}.md"

            # Save modification request
            pr_file.write_text(modification_request)

            # Get relative path (works with both regular and worktree modes)
            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                pr_file_display = to_cwd_relative_path(pr_file)
            except ValueError:
                # Fallback to absolute path if not under cwd
                pr_file_display = str(pr_file)

            console.print()
            console.print(f"[green]✓ Modification request saved to {pr_file_display}[/green]")
            console.print()
            console.print("[bold]Next step:[/bold] cafe develop --auto (or cafe make)")
            console.print()

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Local review completed - modification requested (saved to {pr_file.name})",
                data={"status_code": PhaseStatusCode.NEEDS_CHANGES.value, "pr_file": str(pr_file), "local_review": True},
            )

        # Otherwise, it's a PhaseResult (confirm)
        # Add custom message for local review
        if result.status == PhaseStatus.COMPLETED:
            console.print()
            console.print("[green]✓ Changes confirmed![/green]")
            console.print()

        # Mark result.data to indicate this is local review mode
        if "status_code" not in result.data:
            result.data["status_code"] = PhaseStatusCode.CONFIRMED.value
        result.data["local_review"] = True

        return result

    def _get_branch_name(self) -> str:
        """Get branch name.

        Returns:
            Branch name
        """
        # Use issue_name directly if provided
        if self.issue_name:
            return self.issue_name

        # Fallback: Extract from requirements filename
        # e.g., "20250101-feature.md" -> "feature"
        filename = Path(self.spec_file).stem
        # Remove date prefix if exists
        match = re.match(r"^\d{8}-(.+)$", filename)
        if match:
            return match.group(1)
        return filename

    def _prepare_pr_content(self) -> tuple[PhaseResult | None, tuple[str, str] | None]:
        """Prepare PR title and body.

        Returns:
            Tuple of (title, body)
        """
        # Prepare PR directory with iteration subdirectory
        spec_path = Path(self.spec_file)
        # spec_file is like: .cafe/issues/issue99/spec/iteration_001/output.md
        # Go up 3 levels to get issue dir, then add pr
        pr_dir = spec_path.parent.parent.parent / "pr"
        iteration_dir = pr_dir / f"iteration_{self.iteration:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        output_file = iteration_dir / "output.md"

        # Check if output.md already exists from a previous run
        if not output_file.exists():
            # For iteration > 1, copy from previous iteration as starting point
            if self.iteration > 1:
                prev_iteration_num = self.iteration - 1
                prev_output_file = pr_dir / f"iteration_{prev_iteration_num:03d}" / "output.md"
                if prev_output_file.exists():
                    import shutil
                    shutil.copy2(prev_output_file, output_file)
                    # File now exists, skip to agent generation step
                    result = self._generate_pr_content()
                    if result:
                        return result, None
                # If previous iteration file doesn't exist, continue with normal flow

            # File doesn't exist yet, so we need to either write custom content or generate
            if not output_file.exists():
                final_title = self.custom_title
                final_body = self.custom_body

                # Write custom values if provided
                if final_title and final_body:
                    output_file.write_text(f"# {final_title}\n\n{final_body}")
                elif final_title:
                    output_file.write_text(f"# {final_title}\n\n")
                elif final_body:
                    output_file.write_text(f"# TODO: Write PR title\n\n{final_body}")
                else:
                    # No custom values and file doesn't exist - need agent generation
                    result = self._generate_pr_content()
                    # If agent returned a result (e.g., NEED_PERMISSION), propagate it
                    if result:
                        return result, None

        # Read PR title and body from files (unified approach)
        pr_title = self._get_pr_title()
        pr_body = self._get_pr_body()

        return None, (pr_title, pr_body)

    def _generate_prompt(self, user_input: str = "") -> str:
        """Generate prompt for agent (required by _execute_and_handle_agent_response).

        Args:
            user_input: Not used for PR phase

        Returns:
            The prompt stored in _current_prompt
        """
        return self._current_prompt

    def _generate_pr_content(self) -> PhaseResult | None:
        """Generate PR title and body using agent.

        Agent writes to:
        - .cafe/issues/{issue_name}/pr/iteration_XXX/output.md

        Returns:
            PhaseResult if agent returns NEED_PERMISSION, None otherwise
        """
        # Derive issue name and pr directory with iteration subdirectory
        spec_path = Path(self.spec_file)
        # spec_file is like: .cafe/issues/issue99/spec/iteration_001/output.md
        # Go up 3 levels to get issue dir
        issue_dir = spec_path.parent.parent.parent
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / f"iteration_{self.iteration:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Derive plan file path - use latest versioned file if available
        plan_dir = issue_dir / "plan"
        latest_plan = self._get_latest_versioned_file("plan", plan_dir)
        if latest_plan and latest_plan.exists():
            plan_file = latest_plan
        else:
            # Fallback to legacy plan.md
            plan_file = plan_dir / "plan.md"

        # Generate checklist for this iteration
        from cafe.utils.checklist_generator import generate_pr_checklist

        # Define basic principles
        basic_principles = """- Use same language as commit messages for PR title and description"""

        checklist_path = iteration_dir / "checklist.md"
        output_file = iteration_dir / "output.md"

        # Get previous PR file for iteration > 1
        prev_pr_file = None
        if self.iteration > 1:
            prev_iteration_num = self.iteration - 1
            prev_pr_file = str(pr_dir / f"iteration_{prev_iteration_num:03d}" / "output.md")

        generate_pr_checklist(
            agent_name=self.dev_agent,
            spec_file_path=self.spec_file,
            plan_file_path=str(plan_file),
            pr_file=str(output_file),
            checklist_file_path=checklist_path,
            basic_principles=basic_principles,
            iteration=self.iteration,
            prev_pr_file=prev_pr_file,
        )

        # Use path relative to current working directory (supports worktree)
        from cafe.utils.git_utils import to_cwd_relative_path

        try:
            output_file_pattern = to_cwd_relative_path(output_file)
        except ValueError:
            # Fallback to absolute path if file is not under cwd
            output_file_pattern = str(output_file.resolve())

        try:
            checklist_path_str = to_cwd_relative_path(checklist_path)
        except ValueError:
            checklist_path_str = str(checklist_path.resolve())

        # Get commits for context
        # Use shared method to get only commits from current feature branch
        commits = self._get_current_branch_commits(self.git_ops, self.base_branch)

        # Build issue reference if issue_id is provided
        issue_instruction = f"\n- Add `Closes #{self.issue_id}` at the beginning of body" if self.issue_id else ""

        # Generate prompt for agent
        checklist_instruction = format_checklist_instruction(checklist_path_str)
        prompt = f"""# PR Phase

**Task:** Edit `{output_file_pattern}` to generate PR title and description for this Pull Request.

{checklist_instruction}

**Context:**
- Requirements Specification: {self.spec_file}
- Implementation Plan: {plan_file}

**Commits:**
{commits}

## Requirements

**Title (first line after #):**
- Concise and clear (max 80 characters)
- Describe what this PR does
- Example: "Add user authentication with OAuth2 support"

**Body:**
- Use Markdown format
- Write in same language as commit messages
- Keep the existing structure: Summary, Changes, and Test Plan sections
- Fill in each section with specific details
"""

        # Execute agent
        from cafe.core.status_codes import PhaseStatusCode, generate_status_code_prompt

        status_code_prompt = generate_status_code_prompt(
            valid_codes=[PhaseStatusCode.CONFIRMED],
            descriptions={
                PhaseStatusCode.CONFIRMED: "PR content generation completed",
            },
        )

        checklist_reminder = self._get_checklist_completion_reminder()

        full_prompt = prompt + "\n\n" + checklist_reminder + "\n\n" + status_code_prompt

        # Write initial template content to output.md (for iteration 1)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if not output_file.exists():
            issue_ref = f"Closes #{self.issue_id}\n\n" if self.issue_id else ""
            initial_content = f"""# [Your PR Title Here]

{issue_ref}## Summary
[Brief description in 2-3 sentences]

## Changes
[Main changes as bullet points]

## Test Plan
[How to test these changes]
"""
            output_file.write_text(initial_content)

        # Set allowed tools - only edit permission for output.md
        allowed_tools = ["read", "grep", "glob", "ls", "web_fetch", "web_search"]
        allowed_tools.append(f"edit({output_file_pattern})")

        # Get checklist path for this iteration and add edit permission
        from cafe.utils.git_utils import to_cwd_relative_path
        iteration_dir = self._get_iteration_dir(self.iteration)
        checklist_file = iteration_dir / "checklist.md"
        try:
            checklist_pattern = to_cwd_relative_path(checklist_file)
        except ValueError:
            checklist_pattern = str(checklist_file.resolve())
        allowed_tools.append(f"edit({checklist_pattern})")

        # Store prompt for _generate_prompt method
        self._current_prompt = full_prompt

        # Use common agent execution method (handles all history saving automatically)
        result, response = self._execute_and_handle_agent_response(
            agent_name=self.dev_agent,
            user_input="",  # No user input for PR generation
            valid_status_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_PERMISSION],
            allowed_tools=allowed_tools,
            complete_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_PERMISSION],
            # Note: NEED_PERMISSION will be automatically moved to continue_codes by base class
        )

        # Check if we should return early (only for NEED_PERMISSION)
        if result and result.data.get("status_code") == "CAFE_NEED_PERMISSION":
            return result

        # Verify output file was created
        if not output_file.exists():
            raise RuntimeError("Agent failed to generate PR output file")

    @staticmethod
    def _parse_pr_title(content: str) -> str:
        """Parse PR title from output.md content.

        Args:
            content: Full content of output.md file

        Returns:
            PR title (text after first # heading)

        Raises:
            ValueError: If no H1 heading found
        """
        lines = content.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('# '):
                # Extract title after '# ' and strip whitespace
                title = stripped[2:].strip()
                return title

        raise ValueError("No H1 heading found in output.md")

    @staticmethod
    def _parse_pr_body(content: str) -> str:
        """Parse PR body from output.md content.

        Args:
            content: Full content of output.md file

        Returns:
            PR body (all content after first # heading), empty string if no content
        """
        lines = content.split('\n')
        h1_found = False
        body_lines = []

        for line in lines:
            if not h1_found:
                # Look for first H1
                if line.strip().startswith('# '):
                    h1_found = True
                continue
            else:
                # Collect all lines after H1
                body_lines.append(line)

        # Join and strip leading/trailing whitespace
        return '\n'.join(body_lines).strip()

    def _get_pr_title(self) -> str:
        """Read PR title from agent-generated or user-provided file.

        Returns:
            PR title
        """
        spec_path = Path(self.spec_file)
        # spec_file is like: .cafe/issues/issue99/spec/iteration_001/output.md
        # Go up 3 levels to get issue dir, then add pr
        pr_dir = spec_path.parent.parent.parent / "pr"

        # Find latest iteration directory
        latest_iteration_dir = self._get_latest_iteration_dir(pr_dir)
        if not latest_iteration_dir:
            raise FileNotFoundError("No PR iteration directory found")

        output_file = latest_iteration_dir / "output.md"
        content = output_file.read_text()
        return PRPhase._parse_pr_title(content)

    def _get_pr_body(self) -> str:
        """Read PR body from agent-generated or user-provided file.

        Returns:
            PR body
        """
        spec_path = Path(self.spec_file)
        # spec_file is like: .cafe/issues/issue99/spec/iteration_001/output.md
        # Go up 3 levels to get issue dir, then add pr
        pr_dir = spec_path.parent.parent.parent / "pr"

        # Find latest iteration directory
        latest_iteration_dir = self._get_latest_iteration_dir(pr_dir)
        if not latest_iteration_dir:
            raise FileNotFoundError("No PR iteration directory found")

        output_file = latest_iteration_dir / "output.md"
        content = output_file.read_text()
        return PRPhase._parse_pr_body(content)

    def _get_status_analysis_prompt(self) -> str:
        """Get prompt for analyzing status code.

        Returns:
            Analysis prompt string
        """
        spec_path = Path(self.spec_file)
        # spec_file is like: .cafe/issues/issue99/spec/iteration_001/output.md
        # Go up 3 levels to get issue dir, then add pr
        pr_dir = spec_path.parent.parent.parent / "pr"

        # Find latest iteration directory
        latest_iteration_dir = self._get_latest_iteration_dir(pr_dir)
        if not latest_iteration_dir:
            raise FileNotFoundError("No PR iteration directory found")

        output_file = latest_iteration_dir / "output.md"

        return f"""Please check if the following file exists and has complete content:
- {output_file}

The file should contain:
- A PR title (H1 heading starting with `#`)
- A PR body (content after the H1 heading)

Based on the following conditions, determine which status code to return:

- CAFE_CONFIRMED: File exists and has complete content (both title and body)

Please return only one status code (example: CAFE_CONFIRMED), with no other content."""

    def _rebuild_checklist_for_iteration(self, iteration: int) -> None:
        """Rebuild checklist for current iteration using PR phase rules.

        Args:
            iteration: Iteration number
        """
        from cafe.utils.checklist_generator import generate_pr_checklist

        iteration_dir = self._get_iteration_dir(iteration)
        checklist_path = iteration_dir / "checklist.md"

        # PR phase uses output.md for PR content
        output_file = str(iteration_dir / "output.md")

        # Get plan file path
        spec_path = Path(self.spec_file)
        plan_dir = spec_path.parent.parent.parent / "plan"
        plan_file = self._get_versioned_file_path("plan", None, plan_dir)

        # Define basic principles
        basic_principles = """- Use same language as commit messages for PR title and description"""

        # Generate checklist using the same rules as normal execution
        generate_pr_checklist(
            agent_name=self.dev_agent,
            spec_file_path=self.spec_file,
            plan_file_path=str(plan_file),
            pr_file=output_file,
            checklist_file_path=checklist_path,
            basic_principles=basic_principles,
        )

        print(f"✅ Rebuilt checklist for PR phase iteration {iteration}")


"""Pull Request creation phase."""

from dataclasses import dataclass
from datetime import datetime
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
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.ui.inquirer_prompts import prompt_confirm
from cafe.utils.github import GitHubOps, GitHubError, get_all_pr_comments
from cafe.utils.prompt_utils import format_checklist_instruction


@dataclass(frozen=True)
class PRCommentPersistOutcome:
    """Result of persisting PR discussion into the issue workspace."""

    path: Optional[str] = None
    error: Optional[str] = None


class PRPhase(Phase):
    """Legacy compatibility implementation for the PR phase."""

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
        post_todo_list: Optional[bool] = None,
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
            post_todo_list: Post organized todo list as PR comment (None for auto-detect from config)
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
            # Try to read from config.yaml, fallback to default base branch
            config_file = self.issue_dir / "issue.yaml"
            config_base = self._get_issue_config_value(config_file, "base_branch")
            self.base_branch = config_base if config_base else self.git_ops.get_default_base_branch()

        # Resolve post_todo_list: CLI value > config file value > default (True)
        if post_todo_list is not None:
            # CLI parameter takes precedence
            self.post_todo_list = post_todo_list
        else:
            config_file = self.issue_dir / "issue.yaml"
            config_value = self._get_issue_config_value(config_file, "pr.post_todo_list")
            if config_value is not None:
                self.post_todo_list = bool(config_value)
            else:
                # Default to True to maintain backward compatibility
                self.post_todo_list = True

        # Set up history tracking (like other phases)
        self.phase_dir = self.issue_dir / "pr"
        self.history_dir = self.phase_dir / "history"
        self.phase_name = "pr"
        # Load iteration counter (same pattern as other phases)
        self.iteration = self._load_iteration_counter()

    def _last_seen_comments_artifact_file(self) -> Path:
        """Return artifact path used to persist previously seen PR comment IDs."""
        return self.phase_dir / "artifacts" / "pr_last_seen_comments.json"

    def _persist_last_seen_comment_ids(self, comment_ids: list[str]) -> None:
        """Persist the latest seen PR comment IDs to a runtime artifact file."""
        artifact_file = self._last_seen_comments_artifact_file()
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_seen_comment_ids": [str(comment_id) for comment_id in comment_ids],
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        artifact_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_last_seen_comment_ids_from_artifact(self) -> Optional[set[str]]:
        """Load seen PR comment IDs from runtime artifact file."""
        artifact_file = self._last_seen_comments_artifact_file()
        if not artifact_file.exists():
            return None
        try:
            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                raw_ids = payload.get("last_seen_comment_ids", [])
            elif isinstance(payload, list):
                raw_ids = payload
            else:
                raw_ids = []
            if not isinstance(raw_ids, list):
                return set()
            return {str(item) for item in raw_ids}
        except (json.JSONDecodeError, OSError, TypeError):
            return set()

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
        For local mode: use latest completed PR iteration context

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

        # Fall back to latest completed local PR iteration context
        pr_context = self._get_latest_iteration_context("pr", require_completed=True)
        if pr_context:
            for key in ("end_time", "timestamp"):
                timestamp_str = pr_context.get(key)
                if not isinstance(timestamp_str, str) or not timestamp_str:
                    continue
                try:
                    review_time = datetime.fromisoformat(timestamp_str)
                except ValueError:
                    continue
                if review_time.tzinfo is None:
                    from datetime import timezone
                    review_time = review_time.replace(tzinfo=timezone.utc)
                return review_time

        return None

    def _check_if_develop_is_newer_than_pr(self) -> bool:
        """Check if develop phase timestamp is newer than last PR review.

        Works for both GitHub mode (PR comments) and local mode (latest PR iteration context).

        Returns:
            True if develop is newer (needs re-review), False otherwise
        """
        try:
            develop_time = self._get_latest_develop_end_time()
            if develop_time is None:
                return False  # No develop status, no need to re-review

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

    def _get_incomplete_iteration_info(self) -> Optional[dict]:
        """Get information about the latest incomplete iteration.

        Returns:
            Dictionary with iteration info, or None if no incomplete iteration exists:
            {
                "iteration_dir": Path,
                "iteration_number": int,
                "has_user_input": bool,
                "user_input_path": Path or None
            }
        """
        pr_dir = self.issue_dir / "pr"
        if not pr_dir.exists():
            return None

        # Find all iteration directories
        iteration_dirs = sorted(pr_dir.glob("iteration_*"))
        if not iteration_dirs:
            return None

        # Check the last iteration - if it has no completion marker, it's incomplete
        last_iter_dir = iteration_dirs[-1]
        context_file = last_iter_dir / "context.json"

        if context_file.exists():
            with open(context_file, "r", encoding="utf-8") as f:
                context = json.load(f)
                if self._context_marks_completed(context):
                    return None

        # Last iteration is incomplete - check for user_input.md
        user_input_file = last_iter_dir / "user_input.md"
        has_user_input = user_input_file.exists() and user_input_file.read_text(encoding="utf-8").strip()

        iteration_num = int(last_iter_dir.name.split("_")[1])

        return {
            "iteration_dir": last_iter_dir,
            "iteration_number": iteration_num,
            "has_user_input": bool(has_user_input),
            "user_input_path": user_input_file if has_user_input else None,
        }

    def _get_latest_pr_iteration_info(self) -> Optional[dict]:
        """Get information about the latest PR iteration.

        Returns:
            Dictionary with iteration info, or None if no iterations exist:
            {
                "iteration_dir": Path,
                "iteration_number": int,
                "end_time": datetime or None,
                "status_code": str or None,
                "has_user_input": bool,
                "user_input_path": Path or None
            }
        """
        from datetime import datetime

        pr_dir = self.issue_dir / "pr"
        if not pr_dir.exists():
            return None

        # Find latest completed iteration directory
        iteration_dirs = sorted(pr_dir.glob("iteration_*"))
        if not iteration_dirs:
            return None

        # Find the latest completed iteration
        latest_iteration_dir = None
        iteration_num = None
        end_time = None
        status_code = None

        for iter_dir in reversed(iteration_dirs):
            context_file = iter_dir / "context.json"
            if context_file.exists():
                with open(context_file, "r", encoding="utf-8") as f:
                    context = json.load(f)
                    if self._context_marks_completed(context):
                        latest_iteration_dir = iter_dir
                        iteration_num = int(iter_dir.name.split("_")[1])
                        status_code = self._context_status_code(context)

                        # Get end_time only, do not fall back to timestamp
                        end_time_str = context.get("end_time")
                        if end_time_str:
                            end_time = datetime.fromisoformat(end_time_str)
                            if end_time.tzinfo is None:
                                from datetime import timezone
                                end_time = end_time.replace(tzinfo=timezone.utc)
                        break

        if not latest_iteration_dir:
            return None

        # Check for user_input.md
        user_input_file = latest_iteration_dir / "user_input.md"
        has_user_input = user_input_file.exists() and user_input_file.read_text(encoding="utf-8").strip()

        return {
            "iteration_dir": latest_iteration_dir,
            "iteration_number": iteration_num,
            "end_time": end_time,
            "status_code": status_code,
            "has_user_input": bool(has_user_input),
            "user_input_path": user_input_file if has_user_input else None,
        }

    def _get_last_seen_comment_ids(self) -> "Set[str]":
        """Get last seen comment IDs from runtime artifact with legacy fallback.

        Primary source is pr/artifacts/pr_last_seen_comments.json.
        For backward compatibility with existing issue history, falls back to
        scanning legacy context.json records.

        Returns:
            Set of comment IDs seen at the last push, or empty set if none exists.
        """
        artifact_ids = self._load_last_seen_comment_ids_from_artifact()
        if artifact_ids is not None:
            return artifact_ids

        pr_dir = self.issue_dir / "pr"
        if not pr_dir.exists():
            return set()

        iteration_dirs = sorted(pr_dir.glob("iteration_*"))
        if not iteration_dirs:
            return set()

        # Legacy fallback: search old context.json snapshots
        for iter_dir in reversed(iteration_dirs):
            context_file = iter_dir / "context.json"
            if not context_file.exists():
                continue
            try:
                with open(context_file, "r", encoding="utf-8") as f:
                    context = json.load(f)
                if "last_seen_comment_ids" in context:
                    return set(context["last_seen_comment_ids"])
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        return set()

    def _get_latest_develop_end_time(self) -> Optional["datetime"]:
        """Get end_time of the latest develop phase iteration.

        Returns:
            datetime object or None if develop has never run
        """
        develop_dir = self.issue_dir / "develop"
        if not develop_dir.exists():
            return None

        iteration_dirs = sorted(develop_dir.glob("iteration_*"))
        for iter_dir in reversed(iteration_dirs):
            context_file = iter_dir / "context.json"
            if not context_file.exists():
                continue
            try:
                with open(context_file, "r", encoding="utf-8") as f:
                    context = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            if not self._context_marks_completed(context):
                continue

            end_time_str = context.get("end_time")
            if not end_time_str:
                return None

            try:
                end_time = datetime.fromisoformat(end_time_str)
            except ValueError:
                return None

            if end_time.tzinfo is None:
                from datetime import timezone
                end_time = end_time.replace(tzinfo=timezone.utc)
            return end_time

        return None

    def _should_start_new_iteration(self, pr_iteration_info: Optional[dict]) -> bool:
        """Determine if we should start a new PR iteration.

        Args:
            pr_iteration_info: Latest PR iteration info from _get_latest_pr_iteration_info()

        Returns:
            True if should start new iteration, False otherwise
        """
        # If no PR iterations exist yet, start first iteration
        if not pr_iteration_info:
            return True

        # If latest PR iteration has no user_input, don't start new iteration
        # (waiting for user feedback)
        if not pr_iteration_info["has_user_input"]:
            return False

        # If latest PR iteration has user_input, check if develop has processed it
        pr_end_time = pr_iteration_info["end_time"]
        develop_end_time = self._get_latest_develop_end_time()

        # If either end_time is None, wait for phase to complete (graceful skip)
        if pr_end_time is None or develop_end_time is None:
            return False

        # If develop hasn't processed this PR iteration yet, don't start new iteration
        if pr_end_time > develop_end_time:
            return False

        # Develop has processed the PR iteration, should start new iteration
        return True

    def _has_new_commits(self) -> bool:
        """Check if there are new commits to push/review.

        Returns:
            True if there are unpushed commits, False otherwise
        """
        return self.git_ops.has_unpushed_commits()

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

            # Route to appropriate mode
            if pr_auto_create is False:
                return self._execute_local_mode()
            else:
                return self._execute_github_mode()

        except FileNotFoundError as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"gh CLI not found: {e}",
            )
        except Exception as e:
            return self._handle_exception_in_execute(e, "PR phase failed")

    def _check_and_resume_incomplete_iteration(self, pr_number: int = 0, pr_url: str = "", branch_name: str = "") -> Optional[PhaseResult]:
        """Check for incomplete iteration and resume if exists.

        This is Step 0 of the PR phase workflow, shared by both GitHub and local modes.

        Args:
            pr_number: PR number (0 for local mode)
            pr_url: PR URL ("" for local mode)
            branch_name: Branch name ("" for local mode)

        Returns:
            PhaseResult if incomplete iteration was resumed, None otherwise
        """
        from rich.console import Console
        from cafe.core.status_codes import PhaseStatusCode
        from cafe.core.types import PhaseStatus, PhaseResult

        console = Console()

        incomplete_iteration_info = self._get_incomplete_iteration_info()
        if incomplete_iteration_info:
            # Update iteration number to the incomplete iteration
            self.iteration = incomplete_iteration_info["iteration_number"]

            console.print()
            console.print(f"[dim]Resuming incomplete iteration_{self.iteration:03d}...[/dim]")

            if incomplete_iteration_info["has_user_input"]:
                # Has user_input - resume organizing comments into todo list
                result = self._organize_comments_to_todo_list(pr_number, pr_url, branch_name)

                # Save progress with the actual status code returned by agent
                result_status = result.data.get("status_code", PhaseStatusCode.NEEDS_CHANGES.value) if result and result.data else PhaseStatusCode.NEEDS_CHANGES.value
                self._save_progress(PhaseStatusCode(result_status))
                return result
            else:
                # No user_input - this was a PR create/update iteration that is incomplete
                # Check if output.md was actually updated from template
                iteration_dir = incomplete_iteration_info["iteration_dir"]
                output_file = iteration_dir / "output.md"
                pr_dir = self.issue_dir / "pr"

                was_updated = self._check_output_file_updated(
                    output_file=output_file,
                    iteration=self.iteration,
                    phase_dir=pr_dir,
                    compare_content=self._get_pr_template_content(),
                )

                if not was_updated:
                    # Still template content - need to retry generation
                    console.print("[dim]PR content not generated - retrying...[/dim]")

                    result = self._generate_pr_content()
                    if result:
                        return result

                # PR content is ready - now create/update the actual PR
                console.print("[dim]PR content ready - creating/updating PR...[/dim]")

                # Build existing_pr dict for _create_or_update_pr
                existing_pr = {"number": pr_number, "url": pr_url} if pr_number > 0 else None

                return self._create_or_update_pr(existing_pr, branch_name)

        return None

    def _check_waiting_for_develop(self) -> Optional[dict]:
        """Check if we're waiting for develop phase to process feedback.

        Returns:
            pr_iteration_info if waiting for develop, None if should continue
        """
        pr_iteration_info = self._get_latest_pr_iteration_info()

        # If no completed iteration exists, continue
        if not pr_iteration_info:
            return None

        # Only wait if status_code is NEEDS_CHANGES (not READY_FOR_REVIEW or CONFIRMED)
        status_code = pr_iteration_info.get("status_code")
        if status_code != "needs_changes":
            return None

        # Latest iteration has NEEDS_CHANGES - check if develop has processed it
        should_start_new = self._should_start_new_iteration(pr_iteration_info)

        # If should NOT start new (develop hasn't processed feedback yet), return the info
        if not should_start_new:
            return pr_iteration_info

        # Develop has processed feedback, should start new iteration
        return None

    def _execute_github_mode(self) -> PhaseResult:
        """Execute GitHub PR mode.

        Workflow:
        0. Check for incomplete iteration -> resume if exists
        1-2. Check iteration state -> return early if waiting
        3. GitHub-specific: push commits, create/update PR, or fetch comments

        Returns:
            Phase result
        """
        from rich.console import Console
        from cafe.core.status_codes import PhaseStatusCode

        console = Console()

        # Check gh CLI authentication
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
            if not self.issue_id:
                self.issue_id = self._get_issue_config_value(config_file, "spec.issue_id")
            if self.issue_id is not None:
                self.issue_id = str(self.issue_id)

        # Check requirements file exists
        req_path = Path(self.spec_file)
        if not req_path.exists():
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Spec file not found: {self.spec_file}",
            )

        # Get branch name
        branch_name = self._get_branch_name()

        # Check if PR exists on GitHub
        existing_pr = self.github_ops.get_pr_for_branch(branch_name)

        # Step 0: Check for incomplete iteration (shared logic)
        pr_number = existing_pr["number"] if existing_pr else 0
        pr_url = existing_pr["url"] if existing_pr else ""
        result = self._check_and_resume_incomplete_iteration(pr_number, pr_url, branch_name)
        if result:
            return result

        # Step 1: Check if waiting for develop phase (shared logic)
        waiting_iteration_info = self._check_waiting_for_develop()
        if waiting_iteration_info:
            console.print()
            console.print("[yellow]ℹ️  Latest PR iteration is waiting for develop phase to process feedback[/yellow]")
            console.print()
            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="Waiting for develop phase to process PR feedback",
                data={"branch": branch_name},
            )

        # Step 2: Check for new commits first - always prioritize pushing commits
        has_new_commits = self._has_new_commits()

        if has_new_commits:
            # Push new commits and update/create PR
            unpushed_commits = self.git_ops.get_unpushed_commits()
            console.print()

            if unpushed_commits:
                # Show commits when we have them (incremental push)
                console.print(f"[bold]Pushing {len(unpushed_commits)} unpushed commit(s) to remote...[/bold]")
                for commit in unpushed_commits:
                    console.print(f"  [dim]- {commit['hash'][:8]} {commit['message']}[/dim]")
            else:
                # First push - no commit list
                console.print(f"[bold]Pushing branch to remote...[/bold]")

            console.print()
            self.git_ops.push(branch_name, set_upstream=True, force=self.force_push)

            # Determine iteration number BEFORE calling _prepare_pr_content
            pr_iteration_info = self._get_latest_pr_iteration_info()
            self.iteration = (pr_iteration_info["iteration_number"] + 1) if pr_iteration_info else 1

            # Create or update PR with prepared content
            return self._create_or_update_pr(existing_pr, branch_name)

        # Step 3: No new commits - check state and decide action
        pr_iteration_info = self._get_latest_pr_iteration_info()

        # No PR exists - nothing to do
        if not existing_pr:
            console.print()
            console.print("[yellow]ℹ️  No new commits to push and no existing PR[/yellow]")
            console.print()
            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="No new commits to push",
                data={"branch": branch_name},
            )

        pr_number = existing_pr["number"]
        pr_url = existing_pr["url"]

        # Check latest iteration status_code to decide action
        if pr_iteration_info:
            status_code = pr_iteration_info.get("status_code")

            # If CONFIRMED, we're done - nothing more to do
            if status_code == "confirmed":
                console.print()
                console.print(f"[green]✓ PR #{pr_number} feedback has been fully addressed[/green]")
                console.print()
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"PR #{pr_number} is complete",
                    data={"pr_number": str(pr_number), "pr_url": pr_url, "branch": branch_name},
                )

            # If READY_FOR_REVIEW, fetch comments from GitHub
            if status_code == "ready_for_review":
                console.print()
                console.print(f"[dim]Checking for new comments on PR #{pr_number}...[/dim]")

                # Create new iteration for comments
                self.iteration = pr_iteration_info["iteration_number"] + 1

                # Fetch comments from GitHub
                outcome = self._save_pr_comments_to_user_input(int(pr_number))
                if outcome.error:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=outcome.error,
                    )

                if outcome.path:
                    user_input_path = outcome.path
                    # New comments found - organize into todo list
                    pr_dir = self.issue_dir / "pr"
                    iteration_dir = pr_dir / f"iteration_{self.iteration:03d}"
                    user_input_file = iteration_dir / "user_input.md"
                    from cafe.utils.git_utils import to_cwd_relative_path
                    try:
                        user_input_display = to_cwd_relative_path(user_input_file)
                    except ValueError:
                        user_input_display = str(user_input_file)

                    console.print()
                    console.print(f"[green]✓ Fetched new PR comments to {user_input_display}[/green]")
                    console.print()
                    console.print("[dim]Organizing comments into todo list...[/dim]")

                    # Call agent to organize comments into todo list
                    result = self._organize_comments_to_todo_list(pr_number, pr_url, branch_name)

                    # Save progress with the actual status code returned by agent
                    result_status = result.data.get("status_code", PhaseStatusCode.NEEDS_CHANGES.value) if result and result.data else PhaseStatusCode.NEEDS_CHANGES.value
                    self._save_progress(PhaseStatusCode(result_status))
                    return result
                else:
                    # No comments yet - still waiting for review
                    console.print()
                    console.print(f"[yellow]ℹ️  No new comments on PR #{pr_number}[/yellow]")
                    console.print()
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"PR #{pr_number} has no new comments",
                        data={"pr_number": str(pr_number), "pr_url": pr_url, "branch": branch_name},
                    )

        # No iteration exists yet but PR exists - this shouldn't happen normally
        # (PR should have been created with an iteration)
        console.print()
        console.print(f"[yellow]ℹ️  PR #{pr_number} exists but no iteration found[/yellow]")
        console.print()
        return PhaseResult(
            status=PhaseStatus.COMPLETED,
            message=f"PR #{pr_number} exists but no iteration",
            data={"pr_number": str(pr_number), "pr_url": pr_url, "branch": branch_name},
        )

    def _execute_local_mode(self) -> PhaseResult:
        """Execute local review mode (no GitHub PR).

        Workflow:
        0. Check for incomplete iteration -> resume if exists (shared logic)
        1-2. Check iteration state -> return early if waiting (shared logic)
        3. Local-specific: show diff and ask for review

        Returns:
            Phase result
        """
        from rich.console import Console

        console = Console()

        # Step 0: Check for incomplete iteration (shared logic)
        result = self._check_and_resume_incomplete_iteration(pr_number=0, pr_url="local", branch_name="local")
        if result:
            return result

        # Step 1: Check if waiting for develop phase (shared logic)
        waiting_iteration_info = self._check_waiting_for_develop()
        if waiting_iteration_info:
            console.print()
            console.print("[yellow]ℹ️  Latest PR iteration is waiting for develop phase to process feedback[/yellow]")
            console.print()
            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="Waiting for develop phase to process PR feedback",
                data={"local_review": True},
            )

        # Step 2: Local-specific logic - show diff and ask for review
        return self._execute_local_review_mode()

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
        latest_pr_iteration = self._get_latest_pr_iteration_info()
        previous_status_code = latest_pr_iteration.get("status_code") if latest_pr_iteration else None

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
                console.print("[dim]Continue the workflow with:[/dim] [bold]cafe make[/bold]")
                console.print()

                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Waiting for changes to be addressed",
                    data={"status_code": PhaseStatusCode.NEEDS_CHANGES.value, "local_review": True},
                )
            # If develop is newer, continue with normal review flow (developer may have addressed changes)

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
            def _redisplay_diff() -> None:
                from rich.console import Console
                from rich.syntax import Syntax
                _console = Console()
                _console.print()
                _console.print(f"{'=' * 60}")
                _syntax = Syntax(diff_output, "diff", theme="monokai", line_numbers=False)
                _console.print(_syntax)
                _console.print(f"{'=' * 60}")

            choice = self._ask_user_for_review_decision(
                "code changes",
                agent_name=self.dev_agent,
                role="developer",
                display_callback=_redisplay_diff if diff_output.strip() else None,
            )
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

            # Increment iteration for this feedback
            self.iteration += 1

            # Save to iteration directory structure (pr/iteration_XXX/user_input.md)
            pr_dir = self.issue_dir / "pr"
            iteration_dir = pr_dir / f"iteration_{self.iteration:03d}"
            iteration_dir.mkdir(parents=True, exist_ok=True)

            # Save modification request to user_input.md
            user_input_file = iteration_dir / "user_input.md"
            user_input_file.write_text(modification_request)

            # Save context.json for this iteration using standardized method
            self._update_iteration_history(
                phase_specific_data={
                    "user_input": modification_request,
                    "local_review": True,
                },
            )

            # Get relative path (works with both regular and worktree modes)
            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                user_input_file_display = to_cwd_relative_path(user_input_file)
            except ValueError:
                # Fallback to absolute path if not under cwd
                user_input_file_display = str(user_input_file)

            console.print()
            console.print(f"[green]✓ Modification request saved to {user_input_file_display}[/green]")
            console.print()
            console.print("[dim]Organizing comments into todo list...[/dim]")

            # Call agent to organize comments into todo list (same as GitHub mode)
            result = self._organize_comments_to_todo_list(pr_number=0, pr_url="local", branch_name="local")

            # Save progress with NEEDS_CHANGES status
            self._save_progress(PhaseStatusCode.NEEDS_CHANGES)

            return result

        # Otherwise, it's a PhaseResult (confirm)
        # Increment iteration for this confirmation
        self.iteration += 1

        # Save context.json for confirmation using standardized method
        pr_dir = self.issue_dir / "pr"
        iteration_dir = pr_dir / f"iteration_{self.iteration:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        self._update_iteration_history(
            phase_specific_data={"local_review": True},
            status_code=PhaseStatusCode.CONFIRMED,
        )

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

    def _save_pr_comments_to_user_input(self, pr_number: int) -> PRCommentPersistOutcome:
        """Fetch PR comments from GitHub and save to pr/iteration_XXX/user_input.md.

        This centralizes PR comment detection in the PR phase. When a PR exists on GitHub,
        this method fetches all comments (review, timeline, and review body) and persists
        them to the current iteration's user_input.md file.

        Args:
            pr_number: GitHub PR number

        Returns:
            ``path`` set when comments were saved; empty outcome when there is nothing new;
            ``error`` set when discussion data could not be loaded or persisted.
        """
        from cafe.utils.github import format_comments_for_prompt, GitHubOps
        from datetime import datetime
        from rich.console import Console

        console = Console()

        try:
            # Get previously seen comment IDs to filter out already-processed comments
            exclude_ids = self._get_last_seen_comment_ids()

            # Fetch PR comments, excluding already-seen ones
            print(f"  → Fetching PR comments for PR #{pr_number}")
            comments = get_all_pr_comments(pr_number, exclude_ids=exclude_ids)
            print(f"  → Got {len(comments)} new comments (excluded {len(exclude_ids)} previously seen)")

            if not comments:
                print(f"  → No new comments found for PR #{pr_number}")
                return PRCommentPersistOutcome()

            # Format comments for saving
            formatted_comments = format_comments_for_prompt(comments)

            if not formatted_comments or not formatted_comments.strip():
                print(f"  → No non-empty comments to save")
                return PRCommentPersistOutcome()

            # Save to pr/iteration_XXX/user_input.md
            pr_dir = self.issue_dir / "pr"
            iteration_dir = pr_dir / f"iteration_{self.iteration:03d}"
            iteration_dir.mkdir(parents=True, exist_ok=True)

            user_input_file = iteration_dir / "user_input.md"
            user_input_file.write_text(formatted_comments, encoding="utf-8")

            # Extract and download images from PR comments
            all_image_urls = []
            for comment in comments:
                image_urls = GitHubOps.extract_image_urls(comment.body)
                all_image_urls.extend(image_urls)

            # Deduplicate image URLs
            unique_image_urls = list(set(all_image_urls))

            # Download images if present
            image_paths = []
            if unique_image_urls:
                images_dir = iteration_dir / "images"
                try:
                    gh_ops = GitHubOps()
                    saved_paths = gh_ops.download_issue_images(unique_image_urls, images_dir)
                    if saved_paths:
                        console.print()
                        console.print(f"✅ Downloaded {len(saved_paths)} image(s):")
                        for path in saved_paths:
                            size_bytes = path.stat().st_size
                            if size_bytes >= 1024 * 1024:
                                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                            elif size_bytes >= 1024:
                                size_str = f"{size_bytes / 1024:.1f} KB"
                            else:
                                size_str = f"{size_bytes} B"
                            console.print(f"   {path} ({size_str})")
                            # Store relative path from iteration directory
                            image_paths.append(f"images/{path.name}")
                    if len(saved_paths) < len(unique_image_urls):
                        failed_count = len(unique_image_urls) - len(saved_paths)
                        console.print(f"⚠️  Warning: {failed_count} image(s) failed to download")
                except Exception as e:
                    # Don't fail the whole process if image download fails
                    console.print(f"⚠️  Warning: Failed to download images: {e}")

            # Save context.json for this iteration using standardized method
            phase_specific_data = {
                "pr_number": pr_number,
                "comment_count": len(comments),
                "source": "github_pr_comments",
            }
            if image_paths:
                phase_specific_data["image_count"] = len(image_paths)
                phase_specific_data["image_paths"] = image_paths

            self._update_iteration_history(phase_specific_data=phase_specific_data)

            from cafe.utils.git_utils import to_cwd_relative_path
            try:
                user_input_file_display = to_cwd_relative_path(user_input_file)
            except ValueError:
                user_input_file_display = str(user_input_file)

            console.print()
            console.print(f"[green]✓ Saved {len(comments)} PR comments to {user_input_file_display}[/green]")
            console.print()

            return PRCommentPersistOutcome(path=str(user_input_file))

        except Exception as e:
            message = f"Failed to fetch/save PR comments: {e}"
            console.print(f"[yellow]⚠️  Warning: {message}[/yellow]")
            return PRCommentPersistOutcome(error=message)

    @staticmethod
    def _build_todo_list_comment(todo_content: str, user_input_path: str) -> str:
        """Build PR comment body with todo list and user_input.md reference.

        Args:
            todo_content: The organized todo list content
            user_input_path: File path to user_input.md for reference

        Returns:
            Formatted comment body with file path reference and todo list
        """
        return f"""> 📋 Original review comments: `{user_input_path}`

{todo_content}"""

    def _organize_comments_to_todo_list(self, pr_number: int, pr_url: str, branch_name: str) -> PhaseResult:
        """Organize PR comments into actionable todo list format.

        Calls agent to read user_input.md and organize comments into a todo list
        written to output.md for developer reference.

        Args:
            pr_number: PR number
            pr_url: PR URL
            branch_name: Branch name

        Returns:
            PhaseResult
        """
        from cafe.core.status_codes import PhaseStatusCode
        from cafe.utils.git_utils import to_cwd_relative_path
        from cafe.utils.checklist_generator import generate_pr_comments_checklist
        from cafe.utils.prompt_utils import format_checklist_instruction

        # Get iteration directory
        iteration_dir = self.issue_dir / "pr" / f"iteration_{self.iteration:03d}"
        output_file = iteration_dir / "output.md"
        user_input_file = iteration_dir / "user_input.md"
        checklist_file = iteration_dir / "checklist.md"

        # Find previous todo list output.md (most recent iteration with user_input.md)
        pr_dir = self.issue_dir / "pr"
        prev_output_pattern = None

        if self.iteration > 1:
            # Look backwards from current iteration - 1 for iterations with user_input.md
            for i in range(self.iteration - 1, 0, -1):
                prev_iter_dir = pr_dir / f"iteration_{i:03d}"
                prev_user_input = prev_iter_dir / "user_input.md"
                prev_output = prev_iter_dir / "output.md"

                # Only consider iterations that have user_input.md (indicating it's a todo list, not PR title/body)
                if prev_user_input.exists() and prev_output.exists():
                    try:
                        prev_output_pattern = to_cwd_relative_path(prev_output)
                    except ValueError:
                        prev_output_pattern = str(prev_output.resolve())
                    break

        # Generate checklist for this iteration
        try:
            user_input_pattern = to_cwd_relative_path(user_input_file)
        except ValueError:
            user_input_pattern = str(user_input_file.resolve())

        try:
            output_pattern = to_cwd_relative_path(output_file)
        except ValueError:
            output_pattern = str(output_file.resolve())

        generate_pr_comments_checklist(
            agent_name=self.dev_agent,
            user_input_file_path=user_input_pattern,
            output_file_path=output_pattern,
            prev_output_file_path=prev_output_pattern,
            checklist_file_path=checklist_file,
            basic_principles="- Only include todo items you actually intend to do. Do NOT add items that will be ignored or skipped — if it's not going to be done, leave it out of the todo list entirely",
        )

        # Create empty output.md file for agent to write todo list only
        # Original PR comments remain in user_input.md for reference
        if not output_file.exists():
            output_file.write_text("", encoding="utf-8")

        # Get relative paths for tool permissions
        try:
            output_file_pattern = to_cwd_relative_path(output_file)
        except ValueError:
            output_file_pattern = str(output_file.resolve())

        try:
            checklist_pattern = to_cwd_relative_path(checklist_file)
        except ValueError:
            checklist_pattern = str(checklist_file.resolve())

        # Get agent file path
        from cafe.agents.manager import AgentManager
        agent_file = AgentManager.get_agent_file_path(self.dev_agent, "developer")

        # Create prompt for agent
        checklist_instruction = format_checklist_instruction(checklist_pattern)

        # Check for images
        images_dir = iteration_dir / "images"
        images_instruction = ""
        if images_dir.exists() and any(images_dir.iterdir()):
            image_files = sorted(images_dir.iterdir())
            image_paths = []
            for img in image_files:
                try:
                    image_paths.append(to_cwd_relative_path(img))
                except (ValueError, OSError):
                    image_paths.append(str(img.resolve()))
            image_list = "\n".join(f"  - `{p}`" for p in image_paths)
            images_instruction = f"\n\n**Images:** PR comments include screenshots/images. Use the Read tool to view these images for visual context:\n{image_list}"

        status_code_prompt = ""

        prompt = f"""# PR Comment Organization

**Your Role:** Developer
Read {agent_file} to understand your complete role definition and responsibilities.

**Task Checklist:**
Read {checklist_pattern} for detailed execution steps and requirements.

IMPORTANT: You MUST edit the checklist file and mark each completed item with [x] format (e.g., "[x] Read agent file").
Do NOT return a status code until ALL checklist items are marked as [x].

**Task:** Organize PR comments into actionable todo list.

**Context:**
- PR comments source: {user_input_pattern} (original review comments)
- Output file: {output_pattern} (write organized todo list here){images_instruction}

**Output format to write:**
```markdown
## Todo List

### [Category/Theme]
- [ ] Todo item 1
- [ ] Todo item 2
```


⚠️ **IMPORTANT - Checklist Completion Requirement:**

Before returning ANY status code, you MUST:
1. Review and complete ALL items in {checklist_pattern}
2. Mark each completed item with [x] (change [ ] to [x])
3. Verify that NO unchecked items [ ] remain in the checklist
4. ONLY return a status code after ALL checklist items are marked as complete [x]

The system will verify checklist completion. If unchecked items remain, you will be asked to complete them.

{status_code_prompt}

**Response format:**
- Return ONLY the status code on the first line
- Do NOT include any summary or explanation
"""

        # Define allowed tools (consistent with other phases: spec, plan, review)
        base_allowed_tools = [
            "read",
            "grep",
            "glob",
            "ls",
            "web_fetch",
            "web_search",
            f"edit({output_file_pattern})",
            f"edit({checklist_pattern})",
        ]
        allowed_tools = self._merge_allowed_tools(base_allowed_tools)

        # Execute agent
        response, status_code = self._execute_agent_iteration(
            agent_name=self.dev_agent,
            prompt=prompt,
            user_input="",
            valid_intents=[PhaseStatusCode.NEEDS_CHANGES, PhaseStatusCode.CONFIRMED],
            allowed_tools=allowed_tools,
        )

        # Validate checklist and output.md with retry loop
        from cafe.utils.checklist_validator import validate_checklist

        max_retries = 3
        for retry in range(max_retries + 1):  # 0 = first check, 1-3 = retries
            # Validate checklist completion
            validation_result = validate_checklist(checklist_file)

            # Validate output.md contains todo list content
            has_todo_list = False
            if output_file.exists():
                output_content = output_file.read_text(encoding="utf-8")
                has_todo_list = ("## Todo List" in output_content or
                                "## Todo" in output_content or
                                "- [ ]" in output_content or
                                "- [x]" in output_content)

            # Both validations passed - break out of retry loop
            if validation_result.is_complete and has_todo_list:
                break

            # Last retry exhausted - return FAILED
            if retry == max_retries:
                if not validation_result.is_complete:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Checklist validation failed - {validation_result.unchecked_count} items not marked as complete (after {max_retries} retries)",
                    )
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Agent did not write todo list to output.md (missing todo list markers, after {max_retries} retries)",
                )

            # Build retry prompt describing what's incomplete
            issues = []
            if not validation_result.is_complete:
                issues.append(f"Checklist at {checklist_pattern} has {validation_result.unchecked_count} unchecked items remaining")
            if not has_todo_list:
                issues.append(f"output.md at {output_file_pattern} is missing todo list content (needs '## Todo List' header and '- [ ]' items)")

            retry_prompt = (
                "Your previous response was incomplete. The following issues were found:\n"
                + "\n".join(f"- {issue}" for issue in issues)
                + "\n\nPlease complete the remaining work. "
                + "Write the todo list to the output file and mark all checklist items as [x], then return the status code."
            )

            print(f"⚠️  Validation failed, re-invoking agent... (retry {retry + 1}/{max_retries})")

            # Re-execute agent in the same session to complete the work
            try:
                retry_response, _, _, _, _, retry_model = self.agent_manager.execute(
                    self.dev_agent,
                    retry_prompt,
                    allowed_tools=allowed_tools,
                    allowed_directories=self._get_allowed_directories(),
                )

                # Extract status code from retry response
                retry_status_code = self._extract_status_code_from_response(
                    retry_response,
                    valid_codes=[PhaseStatusCode.NEEDS_CHANGES, PhaseStatusCode.CONFIRMED],
                )
                if retry_status_code is not None:
                    status_code = retry_status_code

            except Exception as e:
                print(f"⚠️  Retry {retry + 1} failed with error: {e}")
                # Continue to next retry attempt

        # Additional validation: if agent returned CONFIRMED, verify this is correct
        # CONFIRMED means: all PR review comments are already addressed or not applicable
        # This iteration's task is to organize PR comments into a todo list for the developer
        if status_code == PhaseStatusCode.CONFIRMED:
            # Re-confirm with agent that all comments are truly complete/not applicable
            try:
                output_display_confirm = to_cwd_relative_path(output_file)
            except ValueError:
                output_display_confirm = str(output_file)

            try:
                user_input_display_confirm = to_cwd_relative_path(user_input_file)
            except ValueError:
                user_input_display_confirm = str(user_input_file)

            confirmation_prompt = f"""You returned confirmed for the PR comment organization task.

IMPORTANT: This iteration's task is to organize PR review comments from {user_input_display_confirm} into a todo list in {output_display_confirm}.

confirmed should ONLY be returned when:
- All PR review comments have already been fully addressed/completed (mark them as [x] in the todo list), OR
- All PR review comments are invalid/not applicable (no action needed)

needs_changes should be returned when:
- There are PR review comments that need to be addressed by the developer (create unchecked todo items - [ ])

Please re-evaluate the PR comments and confirm:
- If there are comments that require code changes or actions, return needs_changes
- If all comments are already addressed or not applicable, return confirmed

Return ONLY the status code (confirmed or needs_changes) with no explanation."""

            # Re-execute agent with confirmation prompt
            confirmation_response, confirmation_status_code = self._execute_agent_iteration(
                agent_name=self.dev_agent,
                prompt=confirmation_prompt,
                user_input="",
                valid_intents=[PhaseStatusCode.NEEDS_CHANGES, PhaseStatusCode.CONFIRMED],
                allowed_tools=allowed_tools,
            )

            # Use the confirmed status code
            status_code = confirmation_status_code

        # Use the status code returned by agent
        # Agent decides based on whether all todo items are completed
        # Return success result
        pr_dir = self.issue_dir / "pr"
        iteration_dir = pr_dir / f"iteration_{self.iteration:03d}"
        output_file = iteration_dir / "output.md"
        # Note: todo list is posted by _post_pr_todo_list() at PR create/update time,
        # only when all items are checked. No need to post here.

        from cafe.utils.git_utils import to_cwd_relative_path
        try:
            output_display = to_cwd_relative_path(output_file)
        except ValueError:
            output_display = str(output_file)

        # Display token usage summary before returning
        self._print_token_usage_summary()

        # Persist final status_code in iteration metadata.
        # _execute_agent_iteration intentionally saves status_code=None (deferred for checklist validation),
        # so we must persist the final status_code here after validation passes.
        self._update_iteration_history(
            phase_specific_data={},
            status_code=status_code,
        )

        return PhaseResult(
            status=PhaseStatus.COMPLETED,
            message=f"Organized PR comments into todo list ({output_display})",
            data={"pr_number": str(pr_number), "pr_url": pr_url, "branch": branch_name, "status_code": status_code.value},
        )

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

    def _get_pr_template_content(self) -> str:
        """Get PR template content.

        Returns:
            PR template content string
        """
        issue_ref = f"Closes #{self.issue_id}\n\n" if self.issue_id else ""
        return f"""# [Your PR Title Here]

{issue_ref}## Summary
[Brief description in 2-3 sentences]

## Changes
[Main changes as bullet points]

## Test Plan
[How to test these changes]
"""

    def _display_pr_success(self, pr_number: str, pr_url: str, action: str) -> None:
        """Display PR success message.

        Args:
            pr_number: PR number
            pr_url: PR URL
            action: Action performed (created/updated/completed)
        """
        from rich.console import Console

        console = Console()
        console.print()
        console.print(f"[green]✓ Pull Request #{pr_number} {action} successfully[/green]")
        console.print(f"  URL: {pr_url}")
        console.print()

    def _post_pr_todo_list(self, pr_number: str) -> None:
        """Post the PR todo list as a PR comment if all items are checked.

        Called at PR creation/update time. Finds the latest PR iteration that
        has a user_input.md (indicating it's a comment-organization iteration)
        and posts its output.md if all todo items are marked [x].

        Args:
            pr_number: GitHub PR number (string)
        """
        if not self.post_todo_list:
            return

        # Find the latest PR iteration with user_input.md (comment-organization iteration)
        pr_dir = self.issue_dir / "pr"
        if not pr_dir.exists():
            return

        iteration_dirs = sorted(pr_dir.glob("iteration_*"), reverse=True)
        for iteration_dir in iteration_dirs:
            user_input_file = iteration_dir / "user_input.md"
            output_file = iteration_dir / "output.md"
            if not user_input_file.exists() or not output_file.exists():
                continue
            output_content = output_file.read_text(encoding="utf-8")
            if not output_content.strip():
                continue
            # Verify this is a todo list (not PR body content)
            is_todo_list = (
                "## Todo List" in output_content
                or "## Todo" in output_content
                or "- [ ]" in output_content
                or "- [x]" in output_content
            )
            if not is_todo_list:
                continue

            # Only post if all todo items are completed
            from cafe.utils.checklist_validator import validate_checklist
            try:
                result = validate_checklist(output_file)
            except FileNotFoundError:
                return
            if not result.is_complete:
                return

            # Post the todo list as a PR comment
            try:
                todo_content = output_file.read_text(encoding="utf-8")
                from cafe.utils.git_utils import to_cwd_relative_path
                try:
                    user_input_display = to_cwd_relative_path(user_input_file)
                except ValueError:
                    user_input_display = str(user_input_file)

                comment_body = self._build_todo_list_comment(todo_content, user_input_display)
                self.github_ops.add_pr_comment(pr_number, comment_body)
            except Exception as e:
                from rich.console import Console
                console = Console()
                console.print(f"[yellow]⚠️  Warning: Failed to post PR todo list as PR comment: {e}[/yellow]")
            return

    def _snapshot_current_comment_ids(self, pr_number: str) -> list:
        """Snapshot current comment IDs on the PR for incremental filtering next iteration.

        Args:
            pr_number: PR number (string or int)

        Returns:
            List of comment ID strings currently on the PR, or empty list on failure
        """
        try:
            current_comments = get_all_pr_comments(int(pr_number))
            return [c.id for c in current_comments]
        except Exception:
            return []

    def _create_or_update_pr(self, existing_pr: dict | None, branch_name: str) -> PhaseResult:
        """Create or update PR with prepared content.

        Args:
            existing_pr: Existing PR info (None if no PR exists)
            branch_name: Branch name

        Returns:
            PhaseResult
        """
        from rich.console import Console
        from cafe.core.status_codes import PhaseStatusCode

        console = Console()

        # Prepare PR content (calls agent to generate title/body)
        result, content = self._prepare_pr_content()
        if result:
            return result
        pr_title, pr_body = content

        if existing_pr:
            # Update existing PR
            pr_number = str(existing_pr["number"])
            pr_url = existing_pr["url"]

            console.print("[bold]Updating pull request...[/bold]")
            console.print()

            self.github_ops.update_pr(pr_number, title=pr_title, body=pr_body)

            self._display_pr_success(pr_number, pr_url, "updated")
            # Post todo list first so its comment ID is included in the snapshot below
            self._post_pr_todo_list(pr_number)

            # Snapshot all current comment IDs so next iteration only fetches new ones
            last_seen_comment_ids = self._snapshot_current_comment_ids(pr_number)
            self._persist_last_seen_comment_ids(last_seen_comment_ids)

            # Save progress - READY_FOR_REVIEW means waiting for reviewer feedback
            self._save_progress(PhaseStatusCode.READY_FOR_REVIEW)

            # Update iteration history with status_code
            self._update_iteration_history(
                phase_specific_data={
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "branch": branch_name,
                },
                status_code=PhaseStatusCode.READY_FOR_REVIEW,
            )

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Pull Request #{pr_number} updated successfully",
                data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name, "status_code": "ready_for_review"},
            )
        else:
            # Create new PR
            console.print("[bold]Creating pull request...[/bold]")
            console.print()

            pr_url = self.github_ops.create_pr(
                title=pr_title, body=pr_body, draft=self.draft, base=self.base_branch
            )

            # Extract PR number
            match = re.search(r"/pull/(\d+)", pr_url)
            if not match:
                raise RuntimeError(f"Failed to extract PR number from: {pr_url}")
            pr_number = match.group(1)

            # Add PR link to issue if configured
            if self.issue_id:
                try:
                    comment = f"Pull Request created: {pr_url}"
                    self.github_ops.add_issue_comment(self.issue_id, comment)
                except Exception as e:
                    console.print(f"[yellow]⚠️  Warning: Failed to add PR link to issue #{self.issue_id}: {e}[/yellow]")

            self._display_pr_success(pr_number, pr_url, "created")
            # Post todo list first so its comment ID is included in the snapshot below
            self._post_pr_todo_list(pr_number)

            # Snapshot all current comment IDs so next iteration only fetches new ones
            last_seen_comment_ids = self._snapshot_current_comment_ids(pr_number)
            self._persist_last_seen_comment_ids(last_seen_comment_ids)

            # Save progress - READY_FOR_REVIEW means waiting for reviewer feedback
            self._save_progress(PhaseStatusCode.READY_FOR_REVIEW)

            # Update iteration history with status_code
            self._update_iteration_history(
                phase_specific_data={
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "branch": branch_name,
                },
                status_code=PhaseStatusCode.READY_FOR_REVIEW,
            )

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Pull Request #{pr_number} created successfully",
                data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name, "status_code": "ready_for_review"},
            )

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
        basic_principles = """- Use the same language as the "Initial Requirements, not in your native language" section in the spec document for PR title and description"""

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

        # Generate prompt for agent - different instructions for first vs subsequent iterations
        checklist_instruction = format_checklist_instruction(checklist_path_str)

        if self.iteration == 1:
            task_instruction = f"**Task:** Edit `{output_file_pattern}` to generate PR title and description for this Pull Request."
            body_instruction = """**Body:**
- Use Markdown format
- Keep the existing structure: Summary, Changes, and Test Plan sections
- Fill in each section with specific details"""
        else:
            task_instruction = f"**Task:** Edit `{output_file_pattern}` to UPDATE the existing PR content with new changes (iteration {self.iteration})."
            body_instruction = """**Body:**
- Use Markdown format
- UPDATE existing sections to reflect the complete PR state
- ADD new changes to the Changes section (preserve previous changes)"""

        prompt = f"""# PR Phase

{task_instruction}

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

{body_instruction}
"""
        from cafe.agents.manager import AgentManager
        from cafe.skills.bridge import try_load_skill_body

        agent_file = AgentManager.get_agent_file_path(self.dev_agent, "developer")
        skill_body = try_load_skill_body(
            "pr",
            context={
                "agent_file": agent_file,
                "spec_file": self.spec_file,
                "plan_file": str(plan_file),
                "commits": commits,
                "output_file": str(output_file),
                "status_code_instruction": "",
            },
        )
        if skill_body:
            prompt = f"# PR Phase\n\n{skill_body}\n\n{task_instruction}\n\n{checklist_instruction}\n\n**Context:**\n- Requirements Specification: {self.spec_file}\n- Implementation Plan: {plan_file}\n\n**Commits:**\n{commits}\n\n## Requirements\n\n**Title (first line after #):**\n- Concise and clear (max 80 characters)\n- Describe what this PR does\n- Example: \"Add user authentication with OAuth2 support\"\n\n{body_instruction}\n"

        # Execute agent
        status_code_prompt = ""

        checklist_reminder = self._get_checklist_completion_reminder()

        full_prompt = prompt + "\n\n" + checklist_reminder + "\n\n" + status_code_prompt

        # Write initial template content to output.md (for iteration 1)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if not output_file.exists():
            output_file.write_text(self._get_pr_template_content())

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
            valid_intents=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_PERMISSION],
            allowed_tools=allowed_tools,
            complete_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_PERMISSION],
            # Note: NEED_PERMISSION will be automatically moved to continue_codes by base class
        )

        # Check if we should return early (only for NEED_PERMISSION)
        if result and result.data.get("status_code") == "need_permission":
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

- confirmed: File exists and has complete content (both title and body)

Please return only one status code (example: confirmed), with no other content."""

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
        basic_principles = """- Use the same language as the "Initial Requirements" section in the spec document for PR title and description"""

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

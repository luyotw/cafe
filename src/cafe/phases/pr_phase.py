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
from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode
from cafe.utils.github import GitHubOps, GitHubError


class PRPhase(Phase):
    """Phase 5: Pull Request creation."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        github_ops: GitHubOps,
        spec_file: str,
        workflow_mode: WorkflowMode,
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
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            issue_name: Issue name (for local mode branch naming)
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
        self.workflow_mode = workflow_mode
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
            config_file = self.issue_dir / "config.yaml"
            config_base = self._get_issue_config_value(config_file, "base_branch")
            self.base_branch = config_base if config_base else "main"

        # Set up history tracking (like other phases)
        pr_dir = self.issue_dir / "pr"
        self.history_dir = pr_dir / "history"
        self.phase_name = "pr"
        self.iteration = self._load_iteration_counter()

    def execute(self) -> PhaseResult:
        """Execute PR creation phase.

        Returns:
            Phase result
        """
        try:
            # Check if local review mode is enabled
            config_file = self.issue_dir / "config.yaml"
            pr_auto_create = self._get_issue_config_value(config_file, "pr.auto_create")

            # If pr.auto_create is False, use local review mode
            if pr_auto_create is False:
                return self._execute_local_review_mode()

            # Otherwise, continue with GitHub PR creation
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
                config_file = self.issue_dir / "config.yaml"
                self.issue_id = self._get_issue_config_value(config_file, "issue_id")

            # Validate inputs
            if self.workflow_mode == WorkflowMode.GITHUB and not self.issue_id:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="GitHub mode requires issue_id",
                )

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check requirements file exists
                req_path = Path(self.spec_file)
                if not req_path.exists():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Spec file not found: {self.spec_file}",
                    )

            # Get branch name
            branch_name = self._get_branch_name()

            # Push branch to remote
            self.git_ops.push(branch_name, set_upstream=True, force=self.force_push)

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
                        choice = input("Do you want to update it? [y/N]: ").strip().lower()

                        if choice not in ['y', 'yes']:
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

                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Pull Request #{pr_number} updated successfully",
                    data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name},
                )

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

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Pull Request #{pr_number} created successfully",
                data={"pr_number": pr_number, "pr_url": pr_url, "branch": branch_name},
            )

        except FileNotFoundError as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"gh CLI not found: {e}",
            )
        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"PR phase failed: {e}",
            )

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

        # Get git diff
        try:
            diff_output = self.git_ops.run_command(f"git diff {self.base_branch}..HEAD")
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
            choice = self._ask_user_for_review_decision("程式碼變更")
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

            console.print()
            console.print(f"[green]✓ Modification request saved to {pr_file.relative_to(Path.cwd())}[/green]")
            console.print()
            console.print("[bold]Next step:[/bold] cafe develop --auto (or cafe make)")
            console.print()

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Local review completed - modification requested (saved to {pr_file.name})",
                data={"status_code": PhaseStatusCode.NEEDS_CHANGES.value, "pr_file": str(pr_file)},
            )

        # Otherwise, it's a PhaseResult (confirm or reject)
        # Add custom messages for local review
        if result.status == PhaseStatus.COMPLETED:
            console.print()
            console.print("[green]✓ Changes confirmed![/green]")
            console.print()
            console.print("[bold]Next step:[/bold] cafe close")
            console.print()
        elif result.status == PhaseStatus.FAILED:
            console.print()
            console.print("[red]✗ Changes rejected[/red]")
            console.print()

        return result

    def _get_branch_name(self) -> str:
        """Get branch name based on workflow mode.

        Returns:
            Branch name
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return f"issue-{self.issue_id}"
        else:
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
        # Prepare PR directory
        spec_path = Path(self.spec_file)
        pr_dir = spec_path.parent.parent / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)

        title_file = pr_dir / "title.txt"
        body_file = pr_dir / "body.md"

        # Use custom title/body if provided via CLI, otherwise let agent generate
        final_title = self.custom_title
        final_body = self.custom_body

        # Determine what needs to be generated by agent
        need_title = final_title is None
        need_body = final_body is None

        # Write custom values if provided
        if final_title:
            title_file.write_text(final_title)
        if final_body:
            body_file.write_text(final_body)

        # Generate missing content using agent
        if need_title or need_body:
            result = self._generate_pr_content(generate_title=need_title, generate_body=need_body)
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

    def _generate_pr_content(self, generate_title: bool = True, generate_body: bool = True) -> None:
        """Generate PR title and/or body using agent.

        Args:
            generate_title: Whether to generate title (default: True)
            generate_body: Whether to generate body (default: True)

        Agent writes to:
        - .cafe/issues/{issue_name}/pr/title.txt (if generate_title=True)
        - .cafe/issues/{issue_name}/pr/body.md (if generate_body=True)
        """
        if not generate_title and not generate_body:
            return  # Nothing to generate

        # Derive issue name and pr directory
        spec_path = Path(self.spec_file)
        issue_name = spec_path.parent.parent.name
        pr_dir = spec_path.parent.parent / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)

        # Use path relative to current working directory (supports worktree)
        from cafe.utils.git_utils import to_cwd_relative_path

        title_file = pr_dir / "title.txt"
        body_file = pr_dir / "body.md"

        try:
            title_file_pattern = to_cwd_relative_path(title_file)
        except ValueError:
            # Fallback to absolute path if file is not under cwd
            title_file_pattern = str(title_file.resolve())

        try:
            body_file_pattern = to_cwd_relative_path(body_file)
        except ValueError:
            # Fallback to absolute path if file is not under cwd
            body_file_pattern = str(body_file.resolve())

        # Derive plan file path - use latest versioned file if available
        plan_dir = spec_path.parent.parent / "plan"
        latest_plan = self._get_latest_versioned_file("plan", plan_dir)
        if latest_plan and latest_plan.exists():
            plan_file = latest_plan
        else:
            # Fallback to legacy plan.md
            plan_file = plan_dir / "plan.md"

        # Get commits for context
        # Use shared method to get only commits from current feature branch
        commits = self._get_current_branch_commits(self.git_ops, self.base_branch)

        # Build issue reference for GitHub mode
        issue_instruction = f"\n- 在 body.md 開頭加上 `Closes #{self.issue_id}`" if self.workflow_mode == WorkflowMode.GITHUB else ""

        # Build tasks based on what needs to be generated
        tasks = []
        if generate_title:
            tasks.append(f"""1. 修改既有檔案 `{title_file}`，用 PR title 取代原本內容
   - 一行，簡潔明確（不超過 80 字元）
   - 描述這個 PR 做了什麼
   - 範例：Add user authentication with OAuth2 support""")

        if generate_body:
            task_num = "2" if generate_title else "1"
            tasks.append(f"""{task_num}. 修改既有檔案 `{body_file}`，用 PR description 取代原本內容（Markdown 格式）
   - ## Summary - 簡要說明（2-3 句話）
   - ## Changes - 主要變更（bullet points）
   - ## Test Plan - 如何測試{issue_instruction}""")

        tasks_str = "\n\n".join(tasks)

        # Determine what is being generated for the prompt
        if generate_title and generate_body:
            what_to_generate = "title 和 description"
            status_desc = "PR title 和 body 已產生完成"
        elif generate_title:
            what_to_generate = "title"
            status_desc = "PR title 已產生完成"
        else:
            what_to_generate = "description"
            status_desc = "PR body 已產生完成"

        # Generate prompt for agent
        prompt = f"""你需要為這個 Pull Request 產生 {what_to_generate}。

**需求規格：** {self.spec_file}
**實作計畫：** {plan_file}

**Commits：**
{commits}

**任務：**
{tasks_str}

完成後請回傳 CAFE_CONFIRMED。
"""

        # Execute agent
        from cafe.core.status_codes import PhaseStatusCode, generate_status_code_prompt

        status_code_prompt = generate_status_code_prompt(
            valid_codes=[PhaseStatusCode.CONFIRMED],
            descriptions={
                PhaseStatusCode.CONFIRMED: status_desc,
            },
        )

        full_prompt = prompt + "\n\n" + status_code_prompt

        # Increment iteration counter before agent execution
        self.iteration += 1

        # Set allowed tools for editing
        allowed_tools = ["read", "grep", "glob", "ls", "web_fetch", "web_search"]

        # Touch files with placeholder content before agent execution to ensure they exist for edit tool
        if generate_title:
            title_file.parent.mkdir(parents=True, exist_ok=True)
            if not title_file.exists():
                title_file.write_text("# TODO: Write PR title here\n")
            allowed_tools.append(f"edit({title_file_pattern})")
        if generate_body:
            body_file.parent.mkdir(parents=True, exist_ok=True)
            if not body_file.exists():
                body_file.write_text("# TODO: Write PR body here\n")
            allowed_tools.append(f"edit({body_file_pattern})")

        # Store prompt for _generate_prompt method
        self._current_prompt = full_prompt

        # Use common agent execution method (handles all history saving automatically)
        result, response = self._execute_and_handle_agent_response(
            agent_name=self.dev_agent,
            user_input="",  # No user input for PR generation
            valid_status_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_PERMISSION],
            allowed_tools=allowed_tools,
            complete_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_PERMISSION],
        )

        # Check if we should return early (only for NEED_PERMISSION)
        if result and result.data.get("status_code") == "CAFE_NEED_PERMISSION":
            return result

        # Verify requested files were created
        if generate_title and not title_file.exists():
            raise RuntimeError("Agent failed to generate PR title file")
        if generate_body and not body_file.exists():
            raise RuntimeError("Agent failed to generate PR body file")

    def _get_pr_title(self) -> str:
        """Read PR title from agent-generated or user-provided file.

        Returns:
            PR title
        """
        spec_path = Path(self.spec_file)
        issue_name = spec_path.parent.parent.name
        title_file = spec_path.parent.parent / "pr" / "title.txt"

        return title_file.read_text().strip()

    def _get_pr_body(self) -> str:
        """Read PR body from agent-generated or user-provided file.

        Returns:
            PR body
        """
        spec_path = Path(self.spec_file)
        issue_name = spec_path.parent.parent.name
        body_file = spec_path.parent.parent / "pr" / "body.md"

        return body_file.read_text().strip()

    def _get_status_analysis_prompt(self) -> str:
        """取得分析 status code 的 prompt.

        Returns:
            分析 prompt 字串
        """
        spec_path = Path(self.spec_file)
        pr_dir = spec_path.parent.parent / "pr"
        title_file = pr_dir / "title.txt"
        body_file = pr_dir / "body.md"

        return f"""請檢查以下檔案是否存在且內容完整：
- {title_file}
- {body_file}

根據以下條件判斷應該回傳哪個狀態碼：

- CAFE_CONFIRMED: 兩個檔案都存在且內容完整

請只回傳一個狀態碼（例如：CAFE_CONFIRMED），不要有任何其他內容。"""


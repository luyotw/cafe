"""Pull Request creation phase."""

import re
import subprocess
from pathlib import Path
from typing import Optional

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode


class PRPhase(Phase):
    """Phase 5: Pull Request creation."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        issue_name: Optional[str] = None,
        draft: bool = True,
        custom_title: Optional[str] = None,
        custom_body: Optional[str] = None,
        interactive: bool = True,
    ) -> None:
        """Initialize PR phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            issue_name: Issue name (for local mode branch naming)
            draft: Create as draft PR (default: True)
            custom_title: Custom PR title (None for auto-generation)
            custom_body: Custom PR body (None for auto-generation)
            interactive: Enable interactive mode (default: True)
        """
        super().__init__(interactive=interactive)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.issue_name = issue_name
        self.draft = draft
        self.custom_title = custom_title
        self.custom_body = custom_body

    def execute(self) -> PhaseResult:
        """Execute PR creation phase.

        Returns:
            Phase result
        """
        try:
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
            self.git_ops.push(branch_name, set_upstream=True)

            # Get PR title (use custom or auto-generate)
            pr_title = self.custom_title if self.custom_title else self._get_pr_title()

            # Get PR body (use custom or auto-generate)
            pr_body = self.custom_body if self.custom_body else self._get_pr_body()

            # Create PR using gh CLI
            pr_number, pr_url = self._create_pr(pr_title, pr_body, branch_name)

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

    def _get_pr_title(self) -> str:
        """Get PR title based on workflow mode.

        Returns:
            PR title
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            # Get title from GitHub issue
            result = subprocess.run(
                ["gh", "issue", "view", self.issue_id, "--json", "title", "-q", ".title"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        else:
            # Get title from first line of requirements file
            req_path = Path(self.spec_file)
            first_line = req_path.read_text().split("\n")[0]
            # Remove markdown heading markers and whitespace
            title = re.sub(r"^#+\s*", "", first_line).strip()
            return title

    def _get_pr_body(self) -> str:
        """Get PR body (commit list between main and HEAD).

        Returns:
            PR body with commit list
        """
        main_branch = self.git_ops.get_main_branch()
        commits = self.git_ops.get_commits_between(
            base=f"origin/{main_branch}", head="HEAD"
        )

        # Format body
        if self.workflow_mode == WorkflowMode.GITHUB:
            body = f"Closes #{self.issue_id}\n\n{commits}"
        else:
            body = commits

        return body

    def _create_pr(self, title: str, body: str, branch_name: str) -> tuple[str, str]:
        """Create PR using gh CLI.

        Args:
            title: PR title
            body: PR body
            branch_name: Branch name

        Returns:
            Tuple of (PR number, PR URL)

        Raises:
            subprocess.CalledProcessError: If gh pr create fails
        """
        # Build gh pr create command
        cmd = ["gh", "pr", "create", "--title", title, "--body", body]

        # Add --draft flag if draft mode is enabled
        if self.draft:
            cmd.append("--draft")

        # Create PR
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"gh pr create failed: {result.stderr}")

        # Extract PR number from URL
        # Expected format: https://github.com/user/repo/pull/123
        pr_url = result.stdout.strip()
        match = re.search(r"/pull/(\d+)", pr_url)
        if match:
            return match.group(1), pr_url

        raise RuntimeError(f"Failed to extract PR number from: {pr_url}")

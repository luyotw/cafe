"""Native GenericPhase hooks backed by existing CAFE UI flows."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any, Optional


from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
from cafe.core.status_codes import PhaseStatusCode
from cafe.ui.interactive_qa import interactive_qa_flow
from cafe.ui.inquirer_prompts import prompt_multiline
from cafe.utils.github import (
    GitHubError,
    GitHubOps,
    filter_unresolved_comments,
    format_comments_for_prompt,
    get_all_pr_comments,
    get_processed_comment_ids_from_history,
)


def _get_previous_iteration_status(phase: Any) -> Optional[str]:
    """Load the previous iteration status code for the current step."""
    if getattr(phase, "iteration", 0) <= 1:
        return None

    context_file = phase._get_iteration_dir(phase.iteration - 1) / "context.json"
    if not context_file.exists():
        return None

    try:
        import json

        raw = json.loads(context_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw.get("status_code")


class UserInputCollector(NoOpHook):
    """Collect user input before agent execution when the previous round requested it."""

    name = "UserInputCollector"

    @staticmethod
    def _get_previous_output_file(phase: Any, step_name: str) -> Optional[Path]:
        if getattr(phase, "iteration", 0) <= 1:
            return None
        return phase._get_versioned_file_path(step_name, phase.iteration - 1, phase.phase_dir)

    @staticmethod
    def _display_previous_output(phase: Any, step_name: str, previous_output_file: Optional[Path]) -> None:
        if previous_output_file is None:
            return
        title_map = {
            "spec": "requirements specification",
            "plan": "plan",
        }
        title = title_map.get(step_name, f"{step_name} output")
        print(f"\nLoading latest {title} file: {previous_output_file}\n")
        if previous_output_file.exists():
            print("=" * 60)
            print(previous_output_file.read_text(encoding="utf-8"))
            print("=" * 60)
            print()

    @staticmethod
    def _display_previous_iteration_delta(phase: Any, previous_output_file: Optional[Path]) -> None:
        if previous_output_file is None:
            return
        from cafe.ui.cli import _display_iteration_delta, console

        _display_iteration_delta(
            phase.iteration - 1,
            str(previous_output_file),
            console,
        )

    @staticmethod
    def _resolve_review_item_name(step_name: str) -> str:
        return {
            "spec": "Requirements specification",
            "plan": "Implementation plan",
        }.get(step_name, step_name)

    @staticmethod
    def _resolve_phase_specific_data(step_name: str, agent_name: str) -> dict[str, str]:
        if not agent_name:
            return {}
        if step_name == "spec":
            return {"pm_agent": agent_name}
        if step_name == "plan":
            return {"dev_agent": agent_name}
        return {"agent_name": agent_name}

    def run(self, **kwargs: Any) -> HookResult:
        stage = kwargs.get("stage")
        if stage != "prepare_input":
            return HookResult()

        phase = kwargs.get("phase")
        if phase is None:
            return HookResult()

        step_name = str(kwargs.get("step_name") or kwargs["step_def"].get("name") or "")
        agent_name = str(kwargs.get("agent_name") or "")
        role = str(kwargs["step_def"].get("role", "developer"))

        # Restore plan phase iteration-1 initial user input (development guide).
        if step_name == "plan" and getattr(phase, "iteration", 0) == 1:
            if step_name not in phase.step_user_inputs:
                user_input = prompt_multiline(
                    "Please enter development guide (can be left empty)"
                ).strip()
                phase.step_user_inputs[step_name] = user_input
            return HookResult(
                context_updates={"user_input": phase.step_user_inputs.get(step_name, "")},
                events=[
                    {
                        "type": "user_input_collected",
                        "step": step_name,
                        "source": "initial_prompt",
                    }
                ],
            )

        previous_status = _get_previous_iteration_status(phase)
        if previous_status not in {"CAFE_NEED_CLARIFICATION", "CAFE_READY_FOR_REVIEW"}:
            return HookResult()

        # PR step uses CAFE_READY_FOR_REVIEW to loop back and check for new comments,
        # not to request user confirmation — skip the review prompt entirely.
        if step_name == "pr" and previous_status == "CAFE_READY_FOR_REVIEW":
            return HookResult()

        prompt_role = {"pm": "pm", "reviewer": "reviewer"}.get(role, "developer")
        previous_output_file = self._get_previous_output_file(phase, step_name)
        # For spec/plan READY_FOR_REVIEW flow, delta view is sufficient and less noisy.
        if not (
            step_name in {"spec", "plan"}
            and previous_status == "CAFE_READY_FOR_REVIEW"
        ):
            self._display_previous_output(phase, step_name, previous_output_file)

        current_iter_dir = phase._get_iteration_dir(phase.iteration)
        current_user_input_file = current_iter_dir / "user_input.md"
        if current_user_input_file.exists():
            existing_user_input = current_user_input_file.read_text(encoding="utf-8").strip()
            if existing_user_input:
                phase.step_user_inputs[step_name] = existing_user_input
                return HookResult(
                    context_updates={"user_input": existing_user_input},
                    events=[
                        {
                            "type": "user_input_collected",
                            "step": step_name,
                            "source": "user_input_file",
                        }
                    ],
                )

        if previous_status == "CAFE_READY_FOR_REVIEW":
            self._display_previous_iteration_delta(phase, previous_output_file)
            prev_data = phase._load_previous_iteration_data() or {}
            # Show diff again after returning from chat/edit, but never print full output.
            redisplay_callback = (
                lambda: self._display_previous_iteration_delta(phase, previous_output_file)
            )
            choice = phase._ask_user_for_review_decision(
                self._resolve_review_item_name(step_name),
                agent_name=agent_name,
                role=prompt_role,
                output_file=previous_output_file,
                display_callback=redisplay_callback,
                edit_option_label="Edit manually - Open in editor",
            )
            result_or_input = phase._process_review_decision(
                choice,
                prev_data,
                self._resolve_review_item_name(step_name),
                self._resolve_phase_specific_data(step_name, agent_name),
            )
            if choice == "confirm":
                return HookResult(
                    continue_pipeline=False,
                    override_status_code=PhaseStatusCode.CONFIRMED,
                    events=[
                        {"type": "review_confirmed", "step": step_name},
                        {"type": "review_confirmed_advance", "step": step_name},
                    ],
                )

            phase.step_user_inputs[step_name] = str(result_or_input)
            return HookResult(
                context_updates={"user_input": str(result_or_input)},
                events=[
                    {
                        "type": "review_modification_requested",
                        "step": step_name,
                    }
                ],
            )

        prev_iter_dir = phase._get_iteration_dir(phase.iteration - 1)
        questions_xml_path = prev_iter_dir / "questions.xml"
        if questions_xml_path.exists() and validate_questions_xml(questions_xml_path):
            questions = parse_questions_xml(questions_xml_path)
            user_input = interactive_qa_flow(
                questions,
                role=prompt_role,
                issue_name=phase.issue_name,
                agent_name=agent_name,
            )
        else:
            user_input = prompt_multiline(
                f"Answer the pending clarification for {step_name}"
            ).strip()

        phase.step_user_inputs[step_name] = user_input
        return HookResult(
            context_updates={"user_input": user_input},
            events=[
                {
                    "type": "user_input_collected",
                    "step": step_name,
                    "source": "questions_xml" if questions_xml_path.exists() else "prompt",
                }
            ],
        )


class GitHubIssueFetcher(NoOpHook):
    """Collect initial requirements for spec iteration 1.

    Reads issue.yaml to determine the input method (manual / github).
    When no config exists, prompts the user to choose.  Writes the
    initial requirements into the output file so the agent has content
    to analyze.
    """

    name = "GitHubIssueFetcher"

    def run(self, **kwargs: Any) -> HookResult:
        stage = kwargs.get("stage")
        if stage != "prepare_input":
            return HookResult()

        phase = kwargs.get("phase")
        if phase is None:
            return HookResult()

        if getattr(phase, "iteration", 0) > 1:
            return HookResult()

        step_name = str(kwargs.get("step_name") or "")
        if step_name != "spec":
            return HookResult()

        output_file: Optional[Path] = kwargs.get("output_file")
        if output_file is None:
            return HookResult()

        if output_file.exists() and output_file.read_text(encoding="utf-8").strip():
            return HookResult()

        config_file = phase.issue_dir / "issue.yaml"
        input_method, issue_id = self._load_input_config(config_file)

        if input_method is None:
            input_method, issue_id = self._prompt_input_method()
            self._save_input_config(config_file, input_method, issue_id)

        if input_method == "github" and issue_id is not None:
            content = self._fetch_github_issue(issue_id)
        else:
            content = self._prompt_manual_input()

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            f"# Initial Requirements\n\n{content}\n", encoding="utf-8"
        )

        return HookResult(
            context_updates={"user_input": content},
            events=[
                {
                    "type": "user_input_collected",
                    "step": step_name,
                    "source": "github" if input_method == "github" else "manual",
                }
            ],
        )

    @staticmethod
    def _load_input_config(config_file: Path) -> tuple[Optional[str], Optional[int]]:
        if not config_file.exists():
            return None, None
        try:
            import yaml

            data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return None, None
        spec = data.get("spec", {})
        method = spec.get("input_method")
        raw_id = spec.get("issue_id")
        return method, int(raw_id) if raw_id else None

    @staticmethod
    def _save_input_config(
        config_file: Path, method: str, issue_id: Optional[int]
    ) -> None:
        import yaml

        try:
            data = (
                yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
                if config_file.exists()
                else {}
            )
        except Exception:
            data = {}
        if "spec" not in data:
            data["spec"] = {}
        data["spec"]["input_method"] = method
        if issue_id is not None:
            data["spec"]["issue_id"] = issue_id
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    @staticmethod
    def _prompt_input_method() -> tuple[str, Optional[int]]:
        from cafe.ui.display import Display
        from cafe.ui.phase_prompts import prompt_for_input_method

        return prompt_for_input_method(Display(), GitHubOps())

    @staticmethod
    def _prompt_manual_input() -> str:
        print()
        print("=" * 70)
        print("Please describe your requirements:")
        print("=" * 70)
        print()
        print("Recommended to write as user stories:")
        print("   Format: As a [role], I want [feature], so that [purpose/value]")
        print()
        print("Or describe requirements in general terms:")
        print("   - Add a CSV export feature")
        print("   - Fix bug where login page cannot submit")
        print()

        content = prompt_multiline("Please enter your requirements").strip()
        if not content:
            raise ValueError("No requirements provided, cannot continue")
        print()
        print("\u2705 Requirements recorded, starting clarification...")
        print()
        return content

    @staticmethod
    def _fetch_github_issue(issue_id: int) -> str:
        from cafe.ui.phase_prompts import fetch_github_issue as _fetch

        gh_ops = GitHubOps()
        fetched_content, _image_urls = _fetch(gh_ops, issue_id)

        lines = fetched_content.split("\n", 1)
        if lines[0].startswith("# "):
            title = lines[0][2:].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            content = (
                f"**Issue Title:** {title}\n\n{body}"
                if body
                else f"**Issue Title:** {title}"
            )
        else:
            content = fetched_content

        print()
        print(f"\u2705 Requirements loaded from GitHub Issue #{issue_id}")
        print("   Starting clarification...")
        print()
        return content


class GitHubPRCreator(NoOpHook):
    """Prepare generic PR iterations for GitHub mode and sync PR metadata."""

    name = "GitHubPRCreator"

    def run(self, **kwargs: Any) -> HookResult:
        stage = kwargs.get("stage")
        if stage == "prepare_input":
            return self._prepare_input(**kwargs)
        if stage == "publish_output":
            return self._publish_output(**kwargs)
        return HookResult()

    def _prepare_input(self, **kwargs: Any) -> HookResult:
        phase = kwargs.get("phase")
        if phase is None:
            return HookResult()

        try:
            branch_name = phase.git_ops.get_current_branch()
        except Exception:
            return HookResult()
        if not branch_name:
            return HookResult()

        try:
            github_ops = GitHubOps()
            existing_pr = github_ops.get_pr_for_branch(branch_name)
        except Exception:
            return HookResult()

        if not existing_pr:
            return HookResult()

        try:
            has_unpushed_commits = phase.git_ops.has_unpushed_commits()
        except Exception:
            has_unpushed_commits = False

        context_updates = {
            "pr_number": str(existing_pr["number"]),
            "pr_url": str(existing_pr["url"]),
        }
        if has_unpushed_commits:
            return HookResult(context_updates=context_updates)

        try:
            exclude_ids = get_processed_comment_ids_from_history(phase.phase_dir)
            comments = get_all_pr_comments(int(existing_pr["number"]), exclude_ids=exclude_ids)
            unresolved_comments = filter_unresolved_comments(comments)
        except Exception:
            return HookResult(context_updates=context_updates)

        if not unresolved_comments:
            return HookResult(context_updates=context_updates)

        formatted_comments = format_comments_for_prompt(unresolved_comments).strip()
        if not formatted_comments:
            return HookResult(context_updates=context_updates)

        phase.step_user_inputs[str(kwargs.get("step_name") or "pr")] = formatted_comments
        context_updates["user_input"] = formatted_comments
        context_updates["pr_comment_count"] = str(len(unresolved_comments))
        context_updates["pr_mode"] = "comments"
        return HookResult(
            context_updates=context_updates,
            events=[
                {
                    "type": "pr_comments_loaded",
                    "count": len(unresolved_comments),
                    "pr_number": str(existing_pr["number"]),
                }
            ],
        )

    def _publish_output(self, **kwargs: Any) -> HookResult:
        # PR creation/update is now handled by scripts/sync_pr.sh in the pr skill.
        return HookResult()


class PRCommentPoster(NoOpHook):
    """Post generated PR todo lists back to the PR when comments require follow-up."""

    name = "PRCommentPoster"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()

        if kwargs.get("status_code") != PhaseStatusCode.NEEDS_CHANGES:
            return HookResult()

        phase = kwargs.get("phase")
        output_file = kwargs.get("output_file")
        if phase is None or not isinstance(output_file, Path) or not output_file.exists():
            return HookResult()

        try:
            branch_name = phase.git_ops.get_current_branch()
            if not branch_name:
                return HookResult()
            github_ops = GitHubOps()
            existing_pr = github_ops.get_pr_for_branch(branch_name)
            if not existing_pr:
                return HookResult()
            todo_list = output_file.read_text(encoding="utf-8").strip()
            if not todo_list:
                return HookResult()

            comment_body = (
                "> CAFE organized the latest PR feedback into a follow-up todo list.\n\n"
                f"{todo_list}"
            )
            github_ops.add_pr_comment(str(existing_pr["number"]), comment_body)
        except Exception:
            return HookResult()

        return HookResult(
            events=[
                {
                    "type": "pr_todo_comment_posted",
                    "pr_number": str(existing_pr["number"]),
                }
            ]
        )


class PRLinkOpener(NoOpHook):
    """Open the created/updated PR in the user's browser."""

    name = "PRLinkOpener"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()

        status_code = kwargs.get("status_code")
        if status_code != PhaseStatusCode.CONFIRMED:
            return HookResult()

        try:
            pr_url = GitHubOps().get_current_pr_url()
        except GitHubError:
            return HookResult()
        except Exception:
            return HookResult()

        try:
            webbrowser.open(pr_url)
        except Exception:
            return HookResult()

        return HookResult(events=[{"type": "pr_link_opened", "url": pr_url}])

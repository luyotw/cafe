"""Native GenericPhase hooks backed by existing CAFE UI flows."""

from __future__ import annotations

import json
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Optional


from cafe.core.blackboard import HandoffContract, HandoffIntent, HandoffOwner
from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser
from cafe.skills.loader import SkillLoader
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
    saved_status = raw.get("status_code")
    if isinstance(saved_status, str) and saved_status:
        return saved_status

    response = raw.get("response")
    if not isinstance(response, str) or not response.strip():
        return None

    parsed = StatusCodeParser.extract(response, valid_codes=list(PhaseStatusCode))
    if parsed is not None:
        return parsed.value

    aliased = StatusCodeParser.coerce_completion_alias(response, list(PhaseStatusCode))
    return aliased.value if aliased is not None else None


def _hook_status_value(raw_status: Any) -> str:
    """Normalize hook status input to its string value."""
    if isinstance(raw_status, PhaseStatusCode):
        return raw_status.value
    if isinstance(raw_status, str):
        return raw_status
    return ""


def _pr_publish_requested(
    *,
    phase: Any,
    step_name: str,
    status_code: Any,
    context: Optional[dict[str, Any]] = None,
) -> bool:
    """Return True when the PR step has reached its publish handoff."""
    if step_name and step_name != "pr":
        return False
    if _hook_status_value(status_code) == PhaseStatusCode.CONFIRMED.value:
        return True

    baton_file: Optional[Path] = None
    if isinstance(context, dict):
        next_step_path = context.get("next_step_path")
        if next_step_path:
            baton_file = Path(str(next_step_path))
    if baton_file is None:
        issue_dir = getattr(phase, "issue_dir", None)
        if isinstance(issue_dir, Path):
            baton_file = issue_dir / "next_step.txt"
    if baton_file is None or not baton_file.exists():
        return False

    try:
        contract = HandoffContract.from_dict(json.loads(baton_file.read_text(encoding="utf-8")))
    except Exception:
        return False

    return (
        contract.from_step == "pr"
        and contract.to_owner == HandoffOwner.DONE
        and contract.to_step == "done"
        and contract.intent == HandoffIntent.WORKFLOW_COMPLETE
    )


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
    def _display_previous_iteration_delta(phase: Any, previous_output_file: Optional[Path]) -> bool:
        if previous_output_file is None:
            return False
        from cafe.ui.cli import _display_iteration_delta, console

        # Delta view requires at least two historical snapshots.
        # If current review target is the first iteration output, there is no
        # meaningful "previous iteration" to diff against.
        if (phase.iteration - 1) <= 1:
            return False

        _display_iteration_delta(
            phase.iteration - 1,
            str(previous_output_file),
            console,
        )
        return True

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
                development_guide_prompt = (
                    "Please enter development guide (can be left empty)\n"
                    "Suggested content:\n"
                    "- Technical solution/direction\n"
                    "- Related code locations\n"
                    "- Technical constraints or dependencies\n"
                    "- Key background information\n"
                    "(Press Esc + Enter to finish)"
                )
                user_input = prompt_multiline(
                    development_guide_prompt
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
            delta_displayed = self._display_previous_iteration_delta(phase, previous_output_file)
            if not delta_displayed:
                self._display_previous_output(phase, step_name, previous_output_file)
            prev_data = phase._load_previous_iteration_data() or {}
            # Show diff again after returning from chat/edit, but never print full output.
            if delta_displayed:
                redisplay_callback = (
                    lambda: self._display_previous_iteration_delta(phase, previous_output_file)
                )
            else:
                redisplay_callback = (
                    lambda: self._display_previous_output(phase, step_name, previous_output_file)
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
    TRUSTED_PR_SCRIPT = "src/cafe/data/skills/pr/scripts/sync_pr.sh"

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
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        if phase is None:
            return HookResult()
        if not _pr_publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
        ):
            return HookResult()
        output_file = kwargs.get("output_file")
        if phase is None or not isinstance(output_file, Path) or not output_file.exists():
            return HookResult()
        if self._is_local_pr_mode(phase):
            return HookResult()

        repo_root = self._resolve_repo_root(phase)
        publish_request_file = kwargs.get("publish_request_file")
        cmd = self._build_publish_command(
            repo_root=repo_root,
            output_file=output_file,
            publish_request_file=publish_request_file if isinstance(publish_request_file, Path) else None,
        )

        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"PR sync script failed: {details}")

        payload = self._parse_sync_result(result.stdout)
        pr_url = str(payload.get("pr_url") or "").strip()
        pr_number = str(payload.get("pr_number") or "").strip()
        action = str(payload.get("action") or "synced").strip()
        events = [
            {
                "type": "pr_synced",
                "url": pr_url,
                "pr_number": pr_number,
                "action": action,
                "source": "skill_script",
            }
        ]
        return HookResult(
            context_updates={
                key: value
                for key, value in {
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "pr_sync_action": action,
                }.items()
                if value
            },
            events=events,
        )

    @staticmethod
    def _is_local_pr_mode(phase: Any) -> bool:
        try:
            value = phase._get_issue_config_value(
                phase.issue_dir / "issue.yaml",
                "pr.auto_create",
            )
        except Exception:
            return False
        return value is False

    @staticmethod
    def _resolve_repo_root(phase: Any) -> Path:
        try:
            repo_root = phase.git_ops.get_repo_root()
        except Exception:
            repo_root = Path.cwd()
        return Path(repo_root).resolve()

    @staticmethod
    def _resolve_sync_script(repo_root: Path) -> Path:
        loader = SkillLoader(project_root=repo_root)
        skill_dir = loader.get_skill_dir("pr")
        script_path = skill_dir / "scripts" / "sync_pr.sh"
        if script_path.exists():
            return script_path

        fallback = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "skills"
            / "pr"
            / "scripts"
            / "sync_pr.sh"
        )
        if fallback.exists():
            return fallback
        raise FileNotFoundError(f"PR sync script not found: {script_path}")

    @staticmethod
    def _resolve_base_branch(phase: Any) -> Optional[str]:
        try:
            value = phase._get_issue_config_value(
                phase.issue_dir / "issue.yaml",
                "base_branch",
            )
        except Exception:
            return None
        return str(value).strip() if value else None

    def _build_publish_command(
        self,
        *,
        repo_root: Path,
        output_file: Path,
        publish_request_file: Optional[Path],
    ) -> list[str]:
        request = self._load_publish_request(
            publish_request_file=publish_request_file,
            repo_root=repo_root,
        )
        if str(request.get("capability") or "").strip() != "publish_pr":
            raise RuntimeError("PR publish request has unsupported capability")

        script_path = self._resolve_contract_script(
            repo_root=repo_root,
            script=str(request.get("script") or ""),
        )
        args = request.get("args")
        if not isinstance(args, dict):
            raise RuntimeError("PR publish request is missing args")

        output_arg = self._resolve_contract_path(
            repo_root=repo_root,
            raw_path=str(args.get("output") or ""),
            field_name="output",
        )
        if output_arg != output_file.resolve():
            raise RuntimeError("PR publish request output does not match current PR artifact")

        cmd = ["/bin/bash", str(script_path), "--output", str(output_arg)]
        base_arg = str(args.get("base") or "").strip()
        if base_arg:
            cmd.extend(["--base", base_arg])
        return cmd

    @staticmethod
    def _load_publish_request(
        *,
        publish_request_file: Optional[Path],
        repo_root: Path,
    ) -> dict[str, Any]:
        if publish_request_file is None:
            raise RuntimeError("PR publish request is missing")
        request_file = publish_request_file.resolve()
        if not request_file.exists():
            raise RuntimeError(f"PR publish request not found: {request_file}")
        try:
            payload = json.loads(request_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"PR publish request is invalid JSON: {request_file}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("PR publish request must be a JSON object")
        permissions = payload.get("permissions")
        if permissions is not None and not isinstance(permissions, dict):
            raise RuntimeError("PR publish request permissions must be an object")
        return payload

    def _resolve_contract_script(self, *, repo_root: Path, script: str) -> Path:
        if not script.strip():
            raise RuntimeError("PR publish request is missing script")
        normalized_script = script.strip()
        requested = self._resolve_contract_path(
            repo_root=repo_root,
            raw_path=normalized_script,
            field_name="script",
        )
        trusted = self._resolve_sync_script(repo_root).resolve()
        canonical = (repo_root / self.TRUSTED_PR_SCRIPT).resolve()
        if requested not in {trusted, canonical} and normalized_script != self.TRUSTED_PR_SCRIPT:
            raise RuntimeError("PR publish request references an untrusted script")
        return trusted

    @staticmethod
    def _resolve_contract_path(
        *,
        repo_root: Path,
        raw_path: str,
        field_name: str,
    ) -> Path:
        if not raw_path.strip():
            raise RuntimeError(f"PR publish request is missing {field_name}")
        path = Path(raw_path)
        if not path.is_absolute():
            path = repo_root / path
        return path.resolve()

    @staticmethod
    def _parse_sync_result(stdout: str) -> dict[str, Any]:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise RuntimeError("PR sync script did not return JSON result")


class PRCommentPoster(NoOpHook):
    """Post generated PR todo lists back to the PR when comments require follow-up."""

    name = "PRCommentPoster"

    @staticmethod
    def _is_post_todo_enabled(phase: Any) -> bool:
        issue_dir = getattr(phase, "issue_dir", None)
        if not isinstance(issue_dir, Path):
            return True
        issue_yaml = issue_dir / "issue.yaml"
        if not issue_yaml.exists():
            return True
        try:
            import yaml

            data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
            pr_cfg = data.get("pr") or {}
            value = pr_cfg.get("post_todo_list")
            if value is None:
                return True
            return bool(value)
        except Exception:
            return True

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        if not _pr_publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
        ):
            return HookResult()
        output_file = kwargs.get("output_file")
        if phase is None or not isinstance(output_file, Path) or not output_file.exists():
            return HookResult()
        if not self._is_post_todo_enabled(phase):
            return HookResult()

        try:
            from cafe.utils.checklist_validator import validate_checklist

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
            is_todo_list = (
                "## Todo List" in todo_list
                or "## Todo" in todo_list
                or "- [ ]" in todo_list
                or "- [x]" in todo_list
            )
            if not is_todo_list:
                return HookResult()
            if not validate_checklist(output_file).is_complete:
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


class LocalPRReviewer(NoOpHook):
    """Display local code diff and collect user confirmation for local PR mode."""

    name = "LocalPRReviewer"

    @staticmethod
    def _is_local_pr_mode(phase: Any) -> bool:
        issue_dir = getattr(phase, "issue_dir", None)
        if not isinstance(issue_dir, Path):
            return False
        try:
            value = phase._get_issue_config_value(issue_dir / "issue.yaml", "pr.auto_create")
        except Exception:
            return False
        return value is False

    @staticmethod
    def _format_todo_feedback(feedback: str) -> str:
        lines = [line.strip() for line in feedback.splitlines() if line.strip()]
        todos = []
        for line in lines:
            normalized = line
            for prefix in ("- [ ]", "- [x]", "-", "*"):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix):].strip()
                    break
            if normalized:
                todos.append(f"- [ ] {normalized}")
        if not todos:
            todos.append("- [ ] Address local review feedback")
        return "# Local review feedback\n\n## Todo List\n" + "\n".join(todos) + "\n"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        if not _pr_publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
        ):
            return HookResult()
        output_file = kwargs.get("output_file")
        if not isinstance(output_file, Path):
            return HookResult()
        if step_name != "pr":
            return HookResult()
        if not self._is_local_pr_mode(phase):
            return HookResult()
        if not getattr(phase, "interactive", False):
            return HookResult(
                override_status_code=PhaseStatusCode.NEED_CLARIFICATION,
                events=[{"type": "local_pr_review_required", "reason": "non_interactive"}],
            )

        try:
            base_branch = phase._get_issue_config_value(phase.issue_dir / "issue.yaml", "base_branch")
            resolved_base = str(base_branch or phase.git_ops.get_main_branch())
            diff_output = phase.git_ops.get_diff(resolved_base, "HEAD")
        except Exception:
            return HookResult()

        from rich.console import Console
        from rich.panel import Panel
        from rich.syntax import Syntax

        console = Console()

        def _display_diff() -> None:
            console.print()
            console.print(Panel.fit("Local Review Mode - Code Changes", style="bold cyan"))
            console.print()
            if diff_output.strip():
                console.print(Syntax(diff_output, "diff", theme="monokai", line_numbers=False))
            else:
                console.print("[yellow]No changes to review[/yellow]")
            console.print()

        _display_diff()
        choice = phase._ask_user_for_review_decision(
            "code changes",
            agent_name=str(kwargs.get("agent_name") or ""),
            role="developer",
            output_file=output_file,
            display_callback=_display_diff if diff_output.strip() else None,
        )
        result_or_input = phase._process_review_decision(
            choice=choice,
            prev_data={},
            phase_name="Local review",
            phase_specific_data={"local_review": True},
        )

        if choice == "confirm":
            return HookResult(events=[{"type": "local_pr_review_confirmed"}])

        feedback = str(result_or_input).strip()
        output_file.write_text(self._format_todo_feedback(feedback), encoding="utf-8")
        user_input_file = output_file.parent / "user_input.md"
        user_input_file.write_text(feedback, encoding="utf-8")
        return HookResult(
            override_status_code=PhaseStatusCode.NEEDS_CHANGES,
            events=[
                {
                    "type": "local_pr_review_changes_requested",
                    "user_input_file": str(user_input_file),
                }
            ],
        )


class PRLinkOpener(NoOpHook):
    """Open the created/updated PR in the user's browser."""

    name = "PRLinkOpener"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        if not _pr_publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
        ):
            return HookResult()

        try:
            pr_url = GitHubOps().get_current_pr_url()
        except GitHubError:
            return HookResult()
        except Exception:
            return HookResult()

        events = [{"type": "pr_synced", "url": pr_url}]

        try:
            webbrowser.open(pr_url)
        except Exception:
            return HookResult(events=events)

        events.append({"type": "pr_link_opened", "url": pr_url})
        return HookResult(events=events)

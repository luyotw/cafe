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
from cafe.utils.github import GitHubOps, GitHubError


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
        previous_status = _get_previous_iteration_status(phase)
        if previous_status not in {"CAFE_NEED_CLARIFICATION", "CAFE_READY_FOR_REVIEW"}:
            return HookResult()

        prompt_role = {"pm": "pm", "reviewer": "reviewer"}.get(role, "developer")
        previous_output_file = self._get_previous_output_file(phase, step_name)
        self._display_previous_output(phase, step_name, previous_output_file)

        if previous_status == "CAFE_READY_FOR_REVIEW":
            self._display_previous_iteration_delta(phase, previous_output_file)
            prev_data = phase._load_previous_iteration_data() or {}
            choice = phase._ask_user_for_review_decision(
                self._resolve_review_item_name(step_name),
                agent_name=agent_name,
                role=prompt_role,
                output_file=previous_output_file,
                display_callback=(
                    lambda: self._display_previous_iteration_delta(phase, previous_output_file)
                ),
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
                    events=[{"type": "review_confirmed", "step": step_name}],
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
    name = "GitHubIssueFetcher"


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

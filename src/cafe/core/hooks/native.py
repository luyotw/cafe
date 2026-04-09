"""Native GenericPhase hooks backed by existing CAFE UI flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
from cafe.ui.interactive_qa import interactive_qa_flow
from cafe.ui.inquirer_prompts import prompt_multiline


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
        if previous_status != "CAFE_NEED_CLARIFICATION":
            return HookResult()

        prompt_role = {"pm": "pm", "reviewer": "reviewer"}.get(role, "developer")
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

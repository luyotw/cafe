"""Interactive Q&A flow for spec phase clarification questions.

Presents PM agent's questions one by one using InquirerPy select prompts,
with support for back navigation, free-text input, answer modification,
and checkbox (multi-select) questions.
"""

from typing import Optional

from InquirerPy import inquirer
from InquirerPy.separator import Separator

from cafe.core.questions_schema import Question
from cafe.ui.chat import launch_chat_session
from cafe.ui.inquirer_prompts import prompt_multiline

# Sentinel values for special choices
OTHER_SENTINEL = "__OTHER__"
BACK_SENTINEL = "__BACK__"

# Display text for empty checkbox selection
NONE_SELECTED = "(none selected)"


def interactive_qa_flow(
    questions: list[Question],
    role: Optional[str] = None,
    issue_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> str:
    """Run interactive Q&A flow and return formatted answers.

    Presents questions one at a time with select menus. Supports:
    - Selecting from agent-suggested options
    - Checkbox (multi-select) for questions with multi_select=True
    - "Other" for free-text input
    - "Back" navigation (from question 2 onwards)
    - "Chat with agent" in each question prompt and summary confirmation (when role and issue_name are given)
    - Answer summary with confirm/modify

    Args:
        questions: List of Question objects from parsed XML
        role: Agent role for inline chat ("pm", "developer", "reviewer"). When provided
              together with issue_name, a "Chat with agent" option is shown in each
              question prompt and the summary confirmation prompt.
        issue_name: Current issue name for chat session resolution.
        agent_name: Display name of the agent (e.g. "Roger", "David"). Used in the
                    "Chat with [agent_name]" label. Falls back to role if not given.

    Returns:
        Formatted Q&A string for passing to agent as user_input
    """
    total = len(questions)
    answers: dict[int, str] = {}
    idx = 0

    # Phase 1: Collect answers
    while idx < total:
        q = questions[idx]
        previous_answer = answers.get(idx)

        if q.multi_select:
            answer = _ask_checkbox(
                q, idx, total, previous_answer,
                role=role, issue_name=issue_name, agent_name=agent_name,
            )
        else:
            answer = _ask_select(
                q, idx, total, previous_answer,
                role=role, issue_name=issue_name, agent_name=agent_name,
            )

        if answer == BACK_SENTINEL:
            idx -= 1
            continue

        answers[idx] = answer
        idx += 1

    # Phase 2: Summary and confirmation loop
    while True:
        _print_summary(questions, answers)

        summary_choices: list = ["Confirm and continue", "Modify an answer..."]
        _append_chat_choice(summary_choices, role, issue_name, agent_name)

        action = inquirer.select(
            message="Confirm answers?",
            choices=summary_choices,
        ).execute()

        if action == "chat":
            launch_chat_session(role, issue_name)
            continue

        if action == "Confirm and continue":
            break

        # Modify flow: pick which question to re-answer
        modify_choices = [
            {"name": f"[{i + 1}] {questions[i].title}: {answers[i]}", "value": questions[i].id}
            for i in range(total)
        ]
        selected_id = inquirer.select(
            message="Which question to modify?",
            choices=modify_choices,
        ).execute()

        # Find index by question id
        modify_idx = next(i for i, q in enumerate(questions) if q.id == selected_id)
        q = questions[modify_idx]
        previous_answer = answers.get(modify_idx)

        if q.multi_select:
            answer = _ask_checkbox(
                q, modify_idx, total, previous_answer, force_no_back=True,
                role=role, issue_name=issue_name, agent_name=agent_name,
            )
        else:
            answer = _ask_select(
                q, modify_idx, total, previous_answer,
                force_no_back=True,
                role=role, issue_name=issue_name, agent_name=agent_name,
            )

        answers[modify_idx] = answer

    return _format_answers(questions, answers)


def _ask_select(
    question: Question,
    idx: int,
    total: int,
    previous_answer: str | None = None,
    force_no_back: bool = False,
    role: Optional[str] = None,
    issue_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> str:
    """Ask a single-select question.

    Returns:
        Selected answer string, or BACK_SENTINEL / OTHER_SENTINEL processing result
    """
    while True:
        choices = _build_choices(
            question, idx, total, previous_answer,
            force_no_back=force_no_back,
            role=role, issue_name=issue_name, agent_name=agent_name,
        )

        answer = inquirer.select(
            message=f"[{idx + 1}/{total}] {question.title}",
            choices=choices,
            default=previous_answer,
        ).execute()

        if answer == "chat":
            launch_chat_session(role, issue_name)
            continue

        if answer == BACK_SENTINEL:
            return BACK_SENTINEL

        if answer == OTHER_SENTINEL:
            answer = _prompt_other_answer(previous_answer)

        return answer


def _ask_checkbox(
    question: Question,
    idx: int,
    total: int,
    previous_answer: str | None = None,
    force_no_back: bool = False,
    role: Optional[str] = None,
    issue_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> str:
    """Ask a multi-select (checkbox) question.

    After checkbox selection, a follow-up single-select prompt offers
    Confirm/Reselect/Back/Chat actions for consistent UX with single-select questions.
    If previous_answer is provided, pre-selects those items (including Other).

    Returns:
        Comma-separated answer string, NONE_SELECTED if nothing selected,
        or BACK_SENTINEL for back navigation
    """
    # Track selected items as a list to avoid comma-based round-trip issues
    # (options may contain commas themselves)
    prev_selected: list[str] = []
    prev_other_text: str | None = None
    if previous_answer and previous_answer != NONE_SELECTED:
        prev_selected = _parse_previous_checkbox_answer(previous_answer, question.options)
        option_set = set(question.options)
        custom_items = [item for item in prev_selected if item not in option_set]
        if custom_items:
            prev_other_text = ", ".join(custom_items)

    while True:
        choices = _build_checkbox_choices(question, prev_selected)

        selected = inquirer.checkbox(
            message=f"[{idx + 1}/{total}] {question.title} (multi-select, press Space to select)",
            choices=choices,
        ).execute()

        selected = selected or []

        result_items = []
        has_other = False
        for item in selected:
            if item == OTHER_SENTINEL:
                has_other = True
            else:
                result_items.append(item)

        # Prompt for custom input only if user selected "Other"
        if has_other:
            custom = _prompt_other_answer(prev_other_text)
            if custom and custom.strip():
                result_items.append(custom.strip())

        # Show follow-up action prompt for Back/Chat navigation
        action = _ask_checkbox_action(
            idx, total, result_items, force_no_back,
            role=role, issue_name=issue_name, agent_name=agent_name,
        )

        if action == BACK_SENTINEL:
            return BACK_SENTINEL
        if action == "redo":
            prev_selected = list(result_items)
            prev_other_text = None  # custom text already in result_items
            continue
        # action == "continue"
        if not result_items:
            return NONE_SELECTED

        return ", ".join(result_items)


def _ask_checkbox_action(
    idx: int,
    total: int,
    result_items: list[str],
    force_no_back: bool = False,
    role: Optional[str] = None,
    issue_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> str:
    """Show a follow-up action prompt after checkbox selection.

    Returns:
        "continue" to accept selections, BACK_SENTINEL to go back,
        or "redo" to re-display the checkbox (after chat or reselect).
    """
    selected_display = ", ".join(result_items) if result_items else NONE_SELECTED
    action_choices: list = [
        {"name": f"Confirm ({selected_display})", "value": "continue"},
        {"name": "Reselect", "value": "redo"},
    ]

    _append_back_choice(action_choices, idx, total, force_no_back)
    _append_chat_choice(action_choices, role, issue_name, agent_name)

    action = inquirer.select(
        message="Action:",
        choices=action_choices,
    ).execute()

    if action == "chat":
        launch_chat_session(role, issue_name)
        return "redo"

    return action


def _build_choices(
    question: Question,
    idx: int,
    total: int,
    previous_answer: str | None = None,
    force_no_back: bool = False,
    role: Optional[str] = None,
    issue_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> list:
    """Build choices list for a single-select question.

    Args:
        question: The question
        idx: Current question index (0-based)
        total: Total number of questions
        previous_answer: Previous answer for this question (for default selection)
        force_no_back: If True, never include Back option (for modify flow)
        role: Agent role for chat option ("pm", "developer", "reviewer")
        issue_name: Current issue name for chat session resolution
        agent_name: Display name for the chat option label

    Returns:
        List of choices for inquirer.select
    """
    choices: list = list(question.options)
    choices.append("Other (type your answer)")

    # Remap "Other (type your answer)" display to OTHER_SENTINEL value
    choices_with_values = [
        {"name": opt, "value": opt} if opt != "Other (type your answer)"
        else {"name": "Other (type your answer)", "value": OTHER_SENTINEL}
        for opt in choices
    ]

    _append_back_choice(choices_with_values, idx, total, force_no_back)
    _append_chat_choice(choices_with_values, role, issue_name, agent_name)

    return choices_with_values


def _prompt_other_answer(default: str | None = None) -> str:
    """Prompt for a free-form Other answer, allowing multiline input."""
    return prompt_multiline(
        "Type your answer:",
        default=default or "",
    )


def _append_back_choice(
    choices: list,
    idx: int,
    total: int,
    force_no_back: bool,
) -> None:
    """Append a Back choice when the current flow allows it."""
    if idx <= 0 or force_no_back:
        return
    choices.append(Separator())
    choices.append({"name": f"← Back to [{idx}/{total}]", "value": BACK_SENTINEL})


def _append_chat_choice(
    choices: list,
    role: Optional[str],
    issue_name: Optional[str],
    agent_name: Optional[str],
) -> None:
    """Append a Chat choice when inline chat is available."""
    if not (role and issue_name):
        return
    chat_label = agent_name or role
    choices.append(Separator())
    choices.append({"name": f"Chat with {chat_label}", "value": "chat"})


def _parse_previous_checkbox_answer(answer: str, known_options: list[str]) -> list[str]:
    """Parse a comma-joined checkbox answer back into individual items.

    Handles options that contain commas by matching known options first,
    then treating any remaining text as custom "Other" input.
    """
    result = []
    remaining = answer
    # Greedily match known options (longest first to avoid partial matches)
    sorted_options = sorted(known_options, key=len, reverse=True)
    for opt in sorted_options:
        if opt in remaining:
            result.append(opt)
            # Remove the matched option and surrounding ", " separators
            remaining = remaining.replace(opt, "", 1)
    # Clean up separators left behind
    remaining = remaining.strip().strip(",").strip()
    if remaining:
        result.append(remaining)
    return result


def _build_checkbox_choices(question: Question, prev_items: list[str] | None = None) -> list:
    """Build choices list for a checkbox (multi-select) question.

    Appends an "Other (type your answer)" option at the end.
    If prev_items is provided, pre-selects matching options.

    Args:
        question: The question
        prev_items: Previously selected items to pre-check

    Returns:
        List of choices for inquirer.checkbox
    """
    prev_set = set(prev_items) if prev_items else set()
    option_set = set(question.options)
    # Pre-check "Other" if any previous item is not a known option
    has_prev_other = bool(prev_set - option_set)

    choices = [
        {"name": opt, "value": opt, "enabled": opt in prev_set}
        for opt in question.options
    ]
    choices.append({
        "name": "Other (press Space to select, then Enter to type answer)",
        "value": OTHER_SENTINEL,
        "enabled": has_prev_other,
    })
    return choices


def _print_summary(questions: list[Question], answers: dict[int, str]) -> None:
    """Print answers summary to console.

    Args:
        questions: List of questions
        answers: Dict mapping question index to answer
    """
    print()
    print("✓ Answers summary:")
    for i, q in enumerate(questions):
        print(f"  {i + 1}. {q.title}: {answers.get(i, '(no answer)')}")
    print()


def _format_answers(questions: list[Question], answers: dict[int, str]) -> str:
    """Format Q&A pairs as text for agent consumption.

    Args:
        questions: List of questions
        answers: Dict mapping question index to answer

    Returns:
        Formatted string with Q/A pairs
    """
    parts = []
    for i, q in enumerate(questions):
        parts.append(f"Q{i + 1}: {q.title}\nA{i + 1}: {answers.get(i, '')}")
    return "\n\n".join(parts)

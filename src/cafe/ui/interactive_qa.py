"""Interactive Q&A flow for spec phase clarification questions.

Presents PM agent's questions one by one using InquirerPy select prompts,
with support for back navigation, free-text input, and answer modification.
"""

from InquirerPy import inquirer
from InquirerPy.separator import Separator

from cafe.core.questions_schema import Question

# Sentinel values for special choices
OTHER_SENTINEL = "__OTHER__"
BACK_SENTINEL = "__BACK__"


def interactive_qa_flow(questions: list[Question]) -> str:
    """Run interactive Q&A flow and return formatted answers.

    Presents questions one at a time with select menus. Supports:
    - Selecting from agent-suggested options
    - "Other" for free-text input
    - "Back" navigation (from question 2 onwards)
    - Answer summary with confirm/modify

    Args:
        questions: List of Question objects from parsed XML

    Returns:
        Formatted Q&A string for passing to agent as user_input
    """
    total = len(questions)
    answers: dict[int, str] = {}
    idx = 0

    # Phase 1: Collect answers
    while idx < total:
        q = questions[idx]
        choices = _build_choices(q, idx, total, answers.get(idx))
        default = answers.get(idx)

        answer = inquirer.select(
            message=f"[{idx + 1}/{total}] {q.title}",
            choices=choices,
            default=default,
        ).execute()

        if answer == BACK_SENTINEL:
            idx -= 1
            continue

        if answer == OTHER_SENTINEL:
            answer = inquirer.text(
                message="Type your answer:",
            ).execute()

        answers[idx] = answer
        idx += 1

    # Phase 2: Summary and confirmation loop
    while True:
        _print_summary(questions, answers)

        action = inquirer.select(
            message="Confirm answers?",
            choices=["Confirm and continue", "Modify an answer..."],
        ).execute()

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
        choices = _build_choices(q, modify_idx, total, answers.get(modify_idx), force_no_back=True)

        answer = inquirer.select(
            message=f"[{modify_idx + 1}/{total}] {q.title}",
            choices=choices,
            default=answers.get(modify_idx),
        ).execute()

        if answer == OTHER_SENTINEL:
            answer = inquirer.text(
                message="Type your answer:",
            ).execute()

        answers[modify_idx] = answer

    return _format_answers(questions, answers)


def _build_choices(
    question: Question,
    idx: int,
    total: int,
    previous_answer: str | None = None,
    force_no_back: bool = False,
) -> list:
    """Build choices list for a question.

    Args:
        question: The question
        idx: Current question index (0-based)
        total: Total number of questions
        previous_answer: Previous answer for this question (for default selection)
        force_no_back: If True, never include Back option (for modify flow)

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

    # Add Back option for question 2+ (not in modify flow)
    if idx > 0 and not force_no_back:
        choices_with_values.append(Separator())
        choices_with_values.append(
            {"name": f"← Back to [{idx}/{total}]", "value": BACK_SENTINEL}
        )

    return choices_with_values


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

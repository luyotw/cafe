"""Checklist templates for each phase.

This module contains template strings for checklists that are generated
for each phase and iteration. These templates include placeholders that
will be resolved with actual file paths when generating checklist files.
"""

# Spec Phase Checklists

SPEC_EXECUTION_STEPS_ITERATION_1 = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {current_spec_file} to understand initial requirements
[ ] If images exist in spec/images/, read and analyze them for UI/UX requirements and visual context
[ ] Read README.md for project context
[ ] Search codebase using Read/Grep tools to find answers before asking users
[ ] Identify unclear areas that need clarification
[ ] Preserve original requirements content - Keep the "Initial Requirements" section and "Issue Title" (if present) unchanged, only add your analysis below
[ ] Write analysis results to {current_spec_file} (NOT in your response)
[ ] Write ONLY the status code in your response
[ ] Confirm: No technical details were included (no implementation, architecture, languages, frameworks, databases)
[ ] Confirm: Only provided 2-3 high-level approach options if any, without prescribing specific technical solutions
[ ] Confirm: No code was modified
[ ] Return appropriate status code
{xml_questions_instruction}"""

SPEC_EXECUTION_STEPS_ITERATION_N = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {prev_spec_file} to review previous analysis
[ ] Review user's answer (provided below)
[ ] Integrate new information into specification
[ ] Preserve original requirements content - Keep the "Initial Requirements" section and "Issue Title" (if present) unchanged from the first iteration
[ ] Write updated analysis to {current_spec_file} (NOT in your response)
[ ] Write ONLY the status code in your response
[ ] Confirm: No technical details were included (no implementation, architecture, languages, frameworks, databases)
[ ] Confirm: Only provided 2-3 high-level approach options if any, without prescribing specific technical solutions
[ ] Confirm: No code was modified
[ ] Return appropriate status code
{xml_questions_instruction}"""

XML_QUESTIONS_INSTRUCTION = """
## Interactive Q&A Questions (Conditional)

[ ] If returning CAFE_NEED_CLARIFICATION: Write questions to {questions_xml_file} in the following XML format, otherwise mark this item as [x]:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Your question text here?</title>
    <options>
      <option>Suggested answer 1</option>
      <option>Suggested answer 2</option>
      <option>Suggested answer 3</option>
    </options>
  </question>
  <question id="2">
    <title>Another question?</title>
    <options>
      <option>Option A</option>
      <option>Option B</option>
    </options>
  </question>
</questions>
```

Rules:
- Write all questions and options in your native language (not English unless that is your native language)
- Root element must be `<questions>`
- Each question must have a unique `id` attribute, a `<title>`, and `<options>` with at least one `<option>`
- Provide 2-4 suggested options per question
- Options should be concise and distinct
"""

# Backwards compatibility alias
SPEC_XML_QUESTIONS_INSTRUCTION = XML_QUESTIONS_INSTRUCTION

SPEC_IMPORTANT_NOTES_ITERATION_4_PLUS = """[ ] Round {iteration}: Only clarify existing questions, NO new questions
"""

SPEC_DOD_INSTRUCTION = """
## Definition of Done (DoD)

[ ] When requirements clarification is complete and you are about to return CAFE_READY_FOR_REVIEW, first ask DoD questions
[ ] Write DoD questions to questions.xml (DoD focuses on functional requirements only)
[ ] Include predefined options for common functional completion criteria (e.g., all major features working, error handling working, edge cases tested)
[ ] The last DoD question should allow custom user input (optional, can be left empty)
[ ] After collecting DoD answers, integrate selected items into the Acceptance Criteria section with "✅ **DoD:**" prefix
[ ] Only then return CAFE_READY_FOR_REVIEW with DoD integrated into the spec document
"""


# Plan Phase Checklists

PLAN_EXECUTION_STEPS_ITERATION_1 = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the development guide in {plan_file_path}
[ ] Read the requirements document {spec_file_path}
[ ] Plan implementation steps (planning, not implementation)
[ ] Append plan after "## Development Guide" section
[ ] Keep "## Development Guide" section unchanged
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Return appropriate status code
{xml_questions_instruction}"""

PLAN_EXECUTION_STEPS_ITERATION_N = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {prev_plan_file} to review previous plan
[ ] Review user's feedback (provided below)
[ ] Integrate feedback and update the plan
[ ] Write updated plan to {current_plan_file} (NOT in your response)
[ ] Keep "## Development Guide" section unchanged
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Return appropriate status code
{xml_questions_instruction}"""


# Develop Phase Checklists

DEVELOP_EXECUTION_STEPS_NORMAL = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Carefully read {spec_file_path} and {plan_file_path}
[ ] Execute development tasks in strict order according to the plan
[ ] Mark each completed task as checked in {plan_file_path} (change - [ ] to - [x])
[ ] Follow existing commit message style, commit multiple times if needed
[ ] Do NOT modify commits from other branches
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All tasks in {plan_file_path} are marked [x]
[ ] Confirm: All tests pass
[ ] Confirm: All commits are made
[ ] Confirm: No pending work remains
[ ] Return status code
"""

DEVELOP_EXECUTION_STEPS_CORRECTION = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read questions in {develop_file} (if exists)
[ ] Carefully read {spec_file_path} and {plan_file_path}
[ ] Read feedback todo list in {feedback_file_path}
[ ] Address each issue raised in the feedback
[ ] Mark completed items in {feedback_file_path} if applicable (change - [ ] to - [x])
[ ] Commit changes with descriptive messages
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All issues are fixed
[ ] Confirm: All tests pass
[ ] Return status code

## Status Codes

- CAFE_CONFIRMED: All issues fixed, ready for review
- CAFE_NO_CHANGES_NEEDED: You believe reviewer's feedback is incorrect/unnecessary. Write your reasoning to {output_file} then return this code.
"""


# Review Phase Checklists

REVIEW_EXECUTION_STEPS = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification {spec_file_path}
[ ] Read the implementation plan {plan_file_path}
[ ] Read PR feedback in {pr_feedback_file_path} (if exists) to see user feedback and requests
[ ] Prioritize user feedback from PR comments over spec requirements if there are conflicts

## Git Status and Security Check
[ ] Check if there are new commits (use `git log {base_branch}..HEAD`). If no commits exist, development is incomplete - return CAFE_NEEDS_CHANGES
[ ] Check for uncommitted changes (if any, development is incomplete)
[ ] Check for sensitive info in committed files (passwords, API keys, credentials)
[ ] If sensitive info found: treat as critical issue, require immediate removal from commit history

## Commit Message Style Check (Critical - Must Match Base Branch)
[ ] Get current branch commits: `git log {base_branch}..HEAD --pretty=format:"%H%n%B"`
[ ] Get base branch reference commits: `git log {base_branch} --max-count=5`
[ ] Determine base branch commit style: single-line or multi-line (subject + body lines, use `git log <sha> -1 --format="%B" | wc -l`)
[ ] Determine current branch commit style: same method
[ ] Check consistency: body presence (multi-line description) matches base branch
[ ] Check consistency: language (Chinese/English) matches base branch
[ ] If style mismatch found: list commit SHAs, explain correct style, provide update commands
[ ] Provide complete git rebase commands for developer to execute directly (non-interactive, see prompt)

## Implementation Completeness Check
[ ] Check for unfinished items in implementation plan
[ ] Compare implementation against {spec_file_path}
[ ] Verify all acceptance criteria are met
[ ] Confirm: Verified all requirements are met, nothing missed

## Code Quality Review
[ ] Check conformance to existing project coding style
[ ] Check if existing code patterns and utilities were reused
[ ] Check for code duplication or excessive duplicate code
[ ] Verify proper error handling
[ ] Check code correctness, readability, performance, security
[ ] Check for missing updates (error messages, prompts, documentation, examples)
[ ] Check for files that should not be committed (config files, log files)
[ ] Check for files or code that should not be deleted
[ ] Check if existing unused code can be removed

## Testing Review
[ ] Review test quality and edge cases
[ ] Check the tests are not fragile or flaky

## Final Steps
[ ] Confirm: No code was modified
[ ] Write review findings to {review_file_path} in todo list format (same format as PR phase)
[ ] Use this structure: ## Todo List / ### [Category] / - [ ] item or - [x] item
[ ] Group issues by category (e.g., "Commit Message Style", "Code Quality", "Testing")
[ ] Each issue should be a checkbox item with file path and line number
[ ] If no issues found, all items should be marked [x]
[ ] Do NOT provide code solutions, only identify issues
[ ] Return ONLY the status code in your response
[ ] Return appropriate status code
"""


# PR Phase Checklists

PR_EXECUTION_STEPS_ITERATION_1 = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification {spec_file_path}
[ ] Read the implementation plan {plan_file_path}
[ ] Review all commits in the current branch
[ ] Edit {pr_file} to fill in PR title and description (NOT in your response)
[ ] Ensure PR title is concise and descriptive (max 80 characters)
[ ] Include reference to original requirements
[ ] List all major changes and commits in the Changes section
[ ] Return ONLY the status code in your response
[ ] Return appropriate status code
"""

PR_EXECUTION_STEPS_ITERATION_N = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {prev_pr_file} to review previous PR content
[ ] Review unpushed commits to identify new changes
[ ] Edit {current_pr_file} to update PR content based on new changes (NOT in your response)
[ ] Return ONLY the status code in your response
[ ] Return appropriate status code
"""

PR_COMMENTS_ORGANIZATION_STEPS = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {user_input_file} which contains the original PR review comments
[ ] Check {prev_output_file} (if exists) - skip todo items that are already completed there
[ ] Group related comments together
[ ] Convert comments into actionable todo list items (markdown checkbox format: - [ ] Item - keep all items UNCHECKED as they represent work to be done)
[ ] Write ONLY the organized todo list to {output_file} (do NOT copy original PR comments)
[ ] Return ONLY the status code in your response
"""

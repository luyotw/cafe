"""Checklist templates for each phase.

This module contains template strings for checklists that are generated
for each phase and iteration. These templates include placeholders that
will be resolved with actual file paths when generating checklist files.
"""

# Spec Phase Checklists

SPEC_EXECUTION_STEPS_ITERATION_1 = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {current_spec_file} to understand initial requirements
[ ] Read README.md for project context
[ ] Search codebase using Read/Grep tools to find answers before asking users
[ ] Identify unclear areas that need clarification
[ ] Write analysis results to {current_spec_file} (NOT in your response)
[ ] Write ONLY the status code in your response
[ ] Confirm: No technical details were included (no implementation, architecture, languages, frameworks, databases)
[ ] Confirm: No technical solutions or suggestions were provided
[ ] Confirm: No code was modified
[ ] Return appropriate status code
"""

SPEC_EXECUTION_STEPS_ITERATION_N = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {prev_spec_file} to review previous analysis
[ ] Review user's answer (provided below)
[ ] Integrate new information into specification
[ ] Write updated analysis to {current_spec_file} (NOT in your response)
[ ] Write ONLY the status code in your response
[ ] Confirm: No technical details were included (no implementation, architecture, languages, frameworks, databases)
[ ] Confirm: No technical solutions or suggestions were provided
[ ] Confirm: No code was modified
[ ] Return appropriate status code
"""

SPEC_IMPORTANT_NOTES_ITERATION_4_PLUS = """[ ] Round {iteration}: Only clarify existing questions, NO new questions
"""


# Plan Phase Checklists

PLAN_EXECUTION_STEPS = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the development guide in {plan_file_path}
[ ] Read the requirements document {spec_file_path}
[ ] Plan implementation steps (planning, not implementation)
[ ] Append plan after "## Development Guide" section
[ ] Keep "## Development Guide" section unchanged
[ ] Write content in your native language
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Return appropriate status code
"""


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
[ ] Read questions in {develop_file}
[ ] Carefully read {spec_file_path} and {plan_file_path}
[ ] Address each issue raised in the review
[ ] Commit changes with descriptive messages
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All issues are fixed
[ ] Confirm: All tests pass
[ ] Return status code
"""


# Review Phase Checklists

REVIEW_EXECUTION_STEPS = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification and implementation plan
[ ] Check for uncommitted changes
[ ] Check for sensitive info in committed files
[ ] Get commits: `git log {base_branch}..HEAD --pretty=format:"%H%n%B"`
[ ] Verify each commit message follows project style
[ ] Check commit message language consistency
[ ] Confirm: Checked commit messages VERY carefully - style must match
[ ] Compare implementation against {spec_file_path}
[ ] Verify all acceptance criteria are met
[ ] Check if any requirements were missed
[ ] Confirm: Verified requirements compliance completely
[ ] Check if existing code patterns and utilities were reused
[ ] Verify proper error handling
[ ] Check for code duplication
[ ] Check if existing unused code can be removed
[ ] Verify all tests pass
[ ] Check test coverage for new code
[ ] Review test quality and edge cases
[ ] Save complete review result to {review_file_path}
[ ] Return ONLY the status code in your response
[ ] Return appropriate status code
"""


# PR Phase Checklists

PR_EXECUTION_STEPS = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification {spec_file_path}
[ ] Read the implementation plan {plan_file_path}
[ ] Review all commits in the current branch
[ ] Generate PR title (concise and descriptive)
[ ] Generate PR description (summarize all changes)
[ ] Include reference to original requirements
[ ] List all major changes and commits
[ ] Write PR content to {pr_file}
[ ] Return ONLY the status code in your response
[ ] Return appropriate status code
"""

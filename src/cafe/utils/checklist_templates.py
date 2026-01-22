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
[ ] Write content in your native language
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
[ ] Write content in your native language
[ ] Write ONLY the status code in your response
[ ] Confirm: No technical details were included (no implementation, architecture, languages, frameworks, databases)
[ ] Confirm: No technical solutions or suggestions were provided
[ ] Confirm: No code was modified
[ ] Return appropriate status code
"""

SPEC_IMPORTANT_NOTES_ITERATION_4_PLUS = """[ ] Round {iteration}: Only clarify existing questions, NO new questions
"""


# Plan Phase Checklists

PLAN_EXECUTION_STEPS_ITERATION_1 = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the development guide in {plan_file_path}
[ ] Read the requirements document {spec_file_path}
[ ] Plan implementation steps (planning, not implementation)
[ ] Append plan after "## Development Guide" section
[ ] Keep "## Development Guide" section unchanged
[ ] Write content in your native language
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Return appropriate status code
"""

PLAN_EXECUTION_STEPS_ITERATION_N = """## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {prev_plan_file} to review previous plan
[ ] Review user's feedback (provided below)
[ ] Integrate feedback and update the plan
[ ] Write updated plan to {current_plan_file} (NOT in your response)
[ ] Keep "## Development Guide" section unchanged
[ ] Write content in your native language
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
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
[ ] Read questions in {develop_file} (if exists)
[ ] Carefully read {spec_file_path} and {plan_file_path}
[ ] Read review feedback in {review_file_path}
[ ] Address each issue raised in the review
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
[ ] Check PR comments (if provided in the prompt) to see user feedback and requests
[ ] Prioritize user feedback from PR comments over spec requirements if there are conflicts

## Git Status and Security Check
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
[ ] Save complete review result to {review_file_path} in your native language
[ ] List file paths and line numbers with issue explanations
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
[ ] Check {spec_file_path} language to determine PR language (use same language as original requirements)
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
[ ] Ensure PR language matches {spec_file_path} language
[ ] Return ONLY the status code in your response
[ ] Return appropriate status code
"""

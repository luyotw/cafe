"""Checklist templates for each phase.

This module contains template strings for checklists that are generated
for each phase and iteration. These templates include placeholders that
will be resolved with actual file paths when generating checklist files.
"""

# Spec Phase Checklists

SPEC_EXECUTION_STEPS_ITERATION_1 = """## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {current_spec_file} to understand initial requirements
[ ] Read README.md for project context
[ ] Search codebase using Read/Grep tools to find answers before asking users
[ ] Identify unclear areas that need clarification
[ ] Write analysis results to {current_spec_file}
[ ] Return appropriate status code
"""

SPEC_EXECUTION_STEPS_ITERATION_N = """## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {prev_spec_file} to review previous analysis
[ ] Review user's answer (provided below)
[ ] Integrate new information into specification
[ ] Write updated analysis to {current_spec_file}
[ ] Return appropriate status code
"""

SPEC_IMPORTANT_NOTES = """## Important Notes Checklist

[ ] ❌ NO technical details (no implementation, architecture, languages, frameworks, databases)
[ ] ❌ NO technical solutions or suggestions
[ ] ❌ DO NOT modify code
[ ] ✅ Focus ONLY on "what users want", "why", and "expected outcomes"
[ ] ✅ Think from product and business perspectives
[ ] ✅ Write ALL content to {current_spec_file}, not in your response
[ ] ✅ Return ONLY the status code in your response
[ ] ✅ If requirements are clear, proceed to CAFE_READY_FOR_REVIEW
"""

SPEC_IMPORTANT_NOTES_ITERATION_4_PLUS = """[ ] ⚠️ Round {iteration}: Only clarify existing questions, NO new questions
"""


# Plan Phase Checklists

PLAN_EXECUTION_STEPS = """## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the development guide in {plan_file_path}
[ ] Read the requirements document {spec_file_path}
[ ] Plan implementation steps (planning, not implementation)
[ ] Append plan after "## Development Guide" section
[ ] Return appropriate status code
"""

PLAN_IMPORTANT_NOTES = """## Important Notes Checklist

[ ] ✅ Your job is PLANNING, not implementation
[ ] ✅ Write plans and steps only, no actual code
[ ] ✅ Keep "## Development Guide" section unchanged
[ ] ✅ Append implementation plan AFTER development guide
[ ] ✅ Write content in your native language
[ ] ✅ Follow template format strictly (if template provided)
"""


# Develop Phase Checklists

DEVELOP_EXECUTION_STEPS_NORMAL = """## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language

[ ] Carefully read {spec_file_path} and {plan_file_path}
[ ] Execute development tasks in strict order according to the plan
[ ] Mark each completed task as checked in {plan_file_path} (change - [ ] to - [x])
[ ] Follow existing commit message style, commit multiple times if needed
[ ] Do NOT modify commits from other branches
[ ] ONLY after completing ALL tasks in the plan, verify and return status code
"""

DEVELOP_EXECUTION_STEPS_CORRECTION = """## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read questions in {develop_file}
[ ] Carefully read {spec_file_path} and {plan_file_path}
[ ] Address each issue raised in the review
[ ] Commit changes with descriptive messages
[ ] ONLY after fixing ALL issues, return status code
"""

DEVELOP_IMPORTANT_NOTES = """## Important Notes Checklist

[ ] ✅ Maximize code reuse - look for existing patterns and utilities
[ ] ✅ Strictly maintain consistency with develop's commit message format
[ ] ✅ Commit message language and structure must match existing commits
"""

DEVELOP_VERIFICATION_CHECKLIST = """## Verification Checklist (Before Returning Status Code)

[ ] All tasks in {plan_file_path} are marked [x]
[ ] All tests pass
[ ] All commits are made
[ ] No pending work remains
"""


# Review Phase Checklists

REVIEW_EXECUTION_STEPS = """## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification and implementation plan
[ ] Review commits in current branch
[ ] Complete all review tasks listed below
[ ] Save complete review result to {review_file_path}
[ ] Return appropriate status code
"""

REVIEW_TASKS_CHECKLIST = """## Review Tasks Checklist (Priority Order)

**1. Git Status Check**
[ ] Check for uncommitted changes
[ ] Check for sensitive info in committed files

**2. [Important] Check Commit Message Style Consistency**
[ ] Get commits for the **latest version**: `git log {base_branch}..HEAD --pretty=format:"%H%n%B"`
[ ] Verify each commit message follows project style
[ ] Check commit message language consistency

**3. Requirements Compliance**
[ ] Compare implementation against {spec_file_path}
[ ] Verify all acceptance criteria are met
[ ] Check if any requirements were missed

**4. Code Quality Check**
[ ] Check if existing code patterns and utilities were reused
[ ] Verify proper error handling
[ ] Check for code duplication
[ ] Check if existing unused code can be removed

**5. Testing Verification**
[ ] Verify all tests pass
[ ] Check test coverage for new code
[ ] Review test quality and edge cases
"""

REVIEW_IMPORTANT_NOTES = """## Important Notes Checklist

[ ] ✅ Be thorough but fair - focus on real issues
[ ] ✅ Check commit messages VERY carefully - style must match
[ ] ✅ Verify requirements compliance completely
[ ] ✅ Write complete review results to {review_file_path}
[ ] ✅ Return ONLY the status code in your response
"""


# PR Phase Checklists

PR_EXECUTION_STEPS = """## Execution Steps Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification {spec_file_path}
[ ] Read the implementation plan {plan_file_path}
[ ] Review all commits in the current branch
[ ] Generate PR title and description
[ ] Return appropriate status code
"""

PR_IMPORTANT_NOTES = """## Important Notes Checklist

[ ] ✅ PR title should be concise and descriptive
[ ] ✅ PR description should summarize all changes
[ ] ✅ Include reference to original requirements
[ ] ✅ List all major changes and commits
[ ] ✅ Write PR content to {pr_title_file} and {pr_body_file}
[ ] ✅ Return ONLY the status code in your response
"""

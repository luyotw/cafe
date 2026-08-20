## Checklist

[ ] Read src/cafe/data/agents/reviewer/Alice.md to understand your role and native language
[ ] Read the requirements specification .cafe/issues/test/spec/iteration_001/output.md
[ ] Read the implementation plan .cafe/issues/test/plan/iteration_001/output.md
[ ] Read PR feedback in (not available) (if exists) to see user feedback and requests
[ ] Prioritize user feedback from PR comments over spec requirements if there are conflicts

## Git Status and Security Check
[ ] Check if there are new commits (use `git log develop..HEAD`). If no commits exist, development is incomplete - hand off to `develop`
[ ] Check for uncommitted changes (if any, development is incomplete)
[ ] Check for sensitive info in committed files (passwords, API keys, credentials)
[ ] If sensitive info found: treat as critical issue, require immediate removal from commit history

## Commit Message Style Check (Critical - Must Match Base Branch)
[ ] Get current branch commits: `git log develop..HEAD --pretty=format:"%H%n%B"`
[ ] Get base branch reference commits: `git log develop --max-count=5`
[ ] Determine base branch commit style: single-line or multi-line (subject + body lines, use `git log <sha> -1 --format="%B" | wc -l`)
[ ] Determine current branch commit style: same method
[ ] Check consistency: body presence (multi-line description) matches base branch
[ ] Check consistency: language (Chinese/English) matches base branch
[ ] If style mismatch found: list commit SHAs, explain correct style, provide update commands
[ ] Provide complete git rebase commands for developer to execute directly (non-interactive, see prompt)

## Implementation Completeness Check
[ ] Check for unfinished items in implementation plan
[ ] Compare implementation against .cafe/issues/test/spec/iteration_001/output.md
[ ] Verify all acceptance criteria are met
[ ] Confirm: Verified all requirements are met, nothing missed

## Code Quality Review
[ ] Trace each candidate defect to its root cause, inspect directly related modes, aliases, entry points, and lifecycle paths in the same pass, and consolidate sibling symptoms into one actionable finding
[ ] Check conformance to existing project coding style
[ ] Check if existing code patterns and utilities were reused
[ ] Check for code duplication or excessive duplicate code
[ ] Verify proper error handling
[ ] Check code correctness, readability, performance, security
[ ] Check for missing updates (error messages, prompts, documentation, examples)
[ ] Comment hygiene (no landmines): code comments must not contain unverified speculation presented as fact. If a comment makes a claim ("this happens because...", "X is safe because...") it must be backed by evidence in code/tests/docs/links, or rewritten as a question/TODO with the missing evidence explicitly stated.
[ ] Check for files that should not be committed (config files, log files)
[ ] Check for files or code that should not be deleted
[ ] Check if existing unused code can be removed

## Anti-Over-Engineering Review
[ ] Confirm the implemented design is the smallest design that satisfies the approved requirements; flag speculative scope, abstractions, or extension points that are not needed now
[ ] Dependency ADR vs manifest diff: diff dependency manifests (`package.json`, `pyproject.toml`, `requirements*.txt`, or equivalent) against the approved plan's **Dependency ADR** list; any package present in the manifest diff but **not declared** in the plan is undeclared — route back to `develop` and name the package in review output
[ ] Dependency hygiene: every new manifest entry has a matching ADR entry and serves a declared requirement; flag unannounced or undeclared dependencies
[ ] Stale majors: if the plan or manifests introduce a **new major** released within the last **30 days**, verify the ADR justifies the risk or an acceptable stable alternative was chosen; flag unjustified bleeding-edge majors
[ ] Layering and speculative abstractions: business logic that could be a pure function is not buried inside a UI component; no abstractions added for hypothetical future scenarios; implementation matches the layering map declared in the plan
[ ] Explicit cross-component contracts: when two components share state via persistence or other indirect channels, the protocol is documented (in code or plan), not coincidental; flag implicit coupling that only works because of current framework behavior

## Testing Review
[ ] Review test quality and edge cases
[ ] Check the tests are not fragile or flaky

## Test Invariants Review
[ ] Plan includes a **Test List** with **Unit tests (N)** and **Integration tests (M)**; each item has a label mapped to an invariant or user journey; if N or M is zero, the plan states why
[ ] New/changed tests align with the plan Test List and protect invariants or journey outcomes—not implementation details
[ ] New/changed tests do **not** couple to disallowed UI copy, CSS classes, DOM structure, or internal state shape (unless spec/DoD explicitly allows exact copy as a product requirement)
[ ] Integration tests map to plan journeys/invariants, not per-component or internal UI structure
[ ] Extractable pure business logic in shared library modules has unit-level coverage when applicable
[ ] Allowed UI contracts are respected: accessibility roles/labels, test ids (`data-testid`), and exact copy only when mandated in the spec

## Final Steps
[ ] Confirm: No code was modified
[ ] Write review findings to .cafe/issues/test/review/iteration_001/output.md in todo list format (same format as PR phase)
[ ] Use this structure: ## Todo List / ### [Category] / - [ ] item or - [x] item
[ ] Group issues by category (e.g., "Commit Message Style", "Code Quality", "Testing")
[ ] Each issue should be a checkbox item with file path and line number
[ ] If no issues found, all items should be marked [x]
[ ] Do NOT provide code solutions, only identify issues
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard
[ ] Keep the response brief; workflow transitions are controlled by the baton


## PR Todo List Check
[ ] Read .cafe/issues/test/pr/iteration_001/output.md - this is the todo list from the PR phase
[ ] Check that ALL todo items are marked as completed [x]. If any unchecked items [ ] remain, return needs_changes

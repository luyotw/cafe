# Contributing to The CAFE Engine

We warmly welcome and appreciate you considering contributing to The CAFE Engine! This document will guide you through the contribution process.

## How to Contribute

You can contribute to this project in several ways:

*   **Reporting Bugs**: If you find a bug, please create an Issue.
*   **Suggesting Features**: If you have an idea for a new feature, please create an Issue to discuss it.
*   **Submitting Pull Requests**: If you want to fix a bug or implement a feature directly.

## Development Setup

1.  **Fork & Clone**
    *   Fork this repository.
    *   Clone your fork to your local machine: `git clone https://github.com/YOUR_USERNAME/cafe.git`

2.  **Create a Virtual Environment and Install Dependencies**
    ```bash
    # Navigate to the project directory
    cd cafe

    # Create a Python virtual environment (Python 3.10+)
    python3.10 -m venv venv
    source venv/bin/activate

    # Install project dependencies (including development tools)
    pip install -e ".[dev]"
    ```

3.  **Set Up Git Hooks**
    This project uses pre-commit hooks to ensure code quality. Please run the following command to install them:
    ```bash
    ./setup-hooks.sh
    ```
    The configured post-commit and post-merge hooks verify and synchronize
    bundled workflow helper skills to supported agent CLIs. CAFE CLI
    startup also uses a per-machine fingerprint as a cross-check for fresh
    checkouts and package upgrades. Concurrent development machines keep
    independent sync state and pick up each other's committed changes through
    the normal Git push/pull flow; CAFE never auto-pulls or modifies a checkout.

## Pull Request Process

1.  Create a new feature branch from the `main` branch:
    ```bash
    git checkout -b your-feature-name
    ```

2.  Make your code changes.

3.  Run tests to ensure everything is working correctly:
    ```bash
    pytest
    ```
    Fast local commits run a bounded contract smoke suite plus tests related to
    staged files. The `pre-push` hook re-runs the bounded contract smoke suite;
    the complete unit/integration and coverage checks run only in the release
    gate. You may run the coverage gate separately when changing behavior:
    ```bash
    ./scripts/test-coverage.sh
    ```

4.  Commit your changes. Please write a clear commit message.

5.  Push your feature branch to your fork:
    ```bash
    git push origin your-feature-name
    ```

6.  Open a Pull Request to the `main` branch of the original repository. In the PR description, please detail your changes, their purpose, and any relevant Issue numbers.

## Testing policy (workflow)

Plan, develop, and review skills enforce a **test invariants** policy: tests should protect business rules and user-journey outcomes, not fragile UI structure. See `src/cafe/data/skills/cafe-plan/references/test_invariants_policy.md` for what to test, what to avoid, and examples.

## Pre-release Verification

Before cutting a release or merging large changes to builtin skills, playbooks, or agents, run the builtin tooling audit:

```bash
./scripts/release-check.sh
```

This release gate runs the complete coverage suite, critical lint checks, the
builtin tooling audit, strict skill validation, package builds, and a clean
wheel-install smoke test. It exits non-zero if any check fails. Run it again
after fixing any reported gaps to confirm they are resolved.

## Workflow behavior

Default development flow (spec → plan → develop → review → pr) is driven by **playbook YAML**, **skills**, and the **workflow runtime** (`BlackboardWorkflowRuntime`, `GenericPhase`). Do not add logic to removed per-phase Python classes (`SpecPhase`, `PlanPhase`, etc.).

| Change type | Where it belongs |
| --- | --- |
| Step transitions, baton intents, hooks | Playbook YAML under `playbooks/` |
| Agent instructions, checklists, phase prompts | Skills (`SKILL.md` and related files) |
| Orchestration, blackboard, execution | Runtime code under `src/cafe/core/` and `src/cafe/phases/` |

Primary CLI entrypoints: `cafe make` and `cafe workflow`. Legacy `cafe spec` / `plan` / `develop` / `review` / `pr` commands were removed in issue #315; use `cafe workflow --start-step <step> --execute` for explicit single-step runs.

Built-in **plan** templates require three architecture sections in every plan artifact: **Negative space** (what we decline to add), **Layering map** (concrete paths for logic/persistence/UI), and **Dependency ADR** (per new dependency or an explicit "none"). The user sees these at plan confirm; **review** diffs dependency manifests against the ADR and routes undeclared packages back to develop.

## Coding Style

*   This project follows the [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guide.
*   We use pre-commit hooks to automatically check and format code. Please ensure you have them set up before committing.

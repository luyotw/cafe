---
name: cafe-review-fallback
description: "Use this skill when CAFE requests portable code-review discovery because the active CLI has no compatible native reviewer."
version: 1.0.0
---

# Portable Review Discovery

## Use This Skill When
- CAFE identifies this skill as the selected review discovery engine.
- The active CLI has no compatible non-interactive native reviewer, or that reviewer failed its bounded compatibility/execution check.

## Instructions
- Read `references/upstream_code_reviewer.md` and apply its high-confidence review procedure to the complete change scope supplied by CAFE.
- Ignore the upstream file's provider/model frontmatter. Interpret `CLAUDE.md` as the repository's applicable project-guidance files, regardless of their filename.
- Review only defects introduced by the current change. Complete the full scoped pass even after finding the first defect.
- Treat correctness and explicit project-rule compliance as always-on. Inspect test gaps, error handling, type invariants, comments, security, performance, and maintainability only when the changed surface triggers them.
- Report candidate findings with confidence, file, line, evidence, and impact. Filter out findings below confidence 80, pre-existing problems, formatter/linter findings, and style-only preferences.
- Do not edit files, decide the workflow handoff, or claim the change passes. Return candidate findings to `cafe-review`, which independently validates them and completes the CAFE acceptance/risk audit.
- Do not read provenance or license references during a review; they are maintainer-only material.

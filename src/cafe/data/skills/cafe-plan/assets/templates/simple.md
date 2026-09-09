# Implementation Plan

## Goal
Add a "forgot password" link to the login page

## Confirmed Implementation Approach

- **Recommended direction:** [The user-confirmed implementation direction]
- **Will do:** [The required scope this Plan covers]
- **Will not do:** [Related work intentionally excluded]
- **Key trade-offs:** [Material scope, cost, reliability, or maintenance trade-offs; write "None" if none apply]

## Issue Decomposition Assessment

- Decision: `keep` or `split`
- Rationale: [Repository evidence for keeping or splitting delivery]
- Current issue scope: [Independently acceptable outcome to deliver now]
- Trigger: [none, product scope, or implementation scope]

### Proposed follow-up issues

| Title | Goal | Depends on | Scope boundary | Non-goals | Definition of Done |
| --- | --- | --- | --- | --- | --- |

## Negative space

- **Not adding:** [dependency or abstraction declined] — [one-line reason]
- If none apply: "No new runtime deps beyond existing stack."

## Layering map

| Layer | Path |
| --- | --- |
| UI | `templates/auth/login.html` |
| Logic | `views/auth_views.py` |

## Dependency ADR

- **New deps:** None expected (or list package + 1–2 sentences: why / alternative / requirement).
- **Recent majors:** If proposing a new major released within **30 days**, justify here or choose a stable version.

## Test List

### Unit tests (N)
1. **Label** — Invariant: … — Scope: pure business logic / shared library module

### Integration tests (M)
1. **Label** — Journey: … — Invariant outcome: … — Boundary: system-level behavior (not per-component)

_(If N or M is 0, one sentence explains why.)_

## Tasks
- [ ] Task 1: Add `forgot_password()` view in `views/auth_views.py`
- [ ] Task 2: Add "Forgot password?" link to `templates/auth/login.html`
- [ ] Task 3 (DoD): Clicking "Forgot password?" on login page opens the reset form
- [ ] Task 4 (DoD): Submitting a valid email sends a reset email with a working link
- [ ] Add tests
- [ ] Commit changes

## Notes
Uses existing `utils/email.py` for sending emails.

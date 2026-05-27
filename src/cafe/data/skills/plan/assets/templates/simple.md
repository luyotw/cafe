# Implementation Plan

## Goal
Add a "forgot password" link to the login page

## Tasks
- [ ] Task 1: Add `forgot_password()` view in `views/auth_views.py`
- [ ] Task 2: Add "Forgot password?" link to `templates/auth/login.html`
- [ ] Task 3 (DoD): Clicking "Forgot password?" on login page opens the reset form
- [ ] Task 4 (DoD): Submitting a valid email sends a reset email with a working link
- [ ] Add tests
- [ ] Commit changes

## Notes
Uses existing `utils/email.py` for sending emails.

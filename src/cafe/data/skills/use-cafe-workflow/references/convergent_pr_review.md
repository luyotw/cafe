# Convergent Driver PR Review

Use this protocol for the driver-owned review after the PR phase. Its purpose is
to preserve the independent final quality gate without dripping findings across
many develop-review-PR cycles.

## 1. Establish one review baseline

- Record the PR HEAD being reviewed.
- Read the latest accepted spec, plan, phase review, PR diff, relevant strategic
  context, and repository test guidance once for that HEAD.
- Build an issue-specific matrix from the product claim and changed surface.
  Include only applicable rows, but always include:
  - every acceptance criterion and the original reported production journey;
  - real production entry points and callers, not only public facades or tests;
  - changed trust, ownership, persistence, and compatibility boundaries;
  - normal and failure behavior promised by the spec or plan;
  - tests that prove the production path rather than reproducing it with
    test-authored state.

For a stateful or asynchronous change, expand the matrix to cover every
persisted state and transition, invalid or untrusted input, liveness and lost
work, idempotence or duplicate launch, terminal evidence, and recovery. For a
different kind of change, derive the equivalent risk rows instead of applying
this list mechanically.

## 2. Finish the pass before sending a correction

- Trace each matrix row through the changed production call path and its tests.
- Run the original reproduction or a minimal executable probe for the main
  product claim. A green unit/full suite is supporting evidence, not a
  substitute for this journey.
- Check that integration tests do not forge workflow output, trusted state, or
  receipts that production code itself cannot create.
- Continue across all applicable rows after finding a blocker. Finding enough
  defects to reject the PR is not a stopping condition.
- Stop early only to contain an immediate safety problem such as exposed
  credentials or an active destructive side effect. Resume the remaining
  review after containment.

Before returning to develop, consolidate every currently observable blocker.
For each blocker include the concrete evidence, violated requirement or
invariant, expected behavior, and a focused verification probe. Group related
findings, but do not hide independent failures behind one vague architectural
request.

Pass one consolidated correction to the responsible CAFE step, push the updated
branch, and then use the convergence pass below. Do not split independent
findings across separate workflow restarts merely to begin fixing sooner.

## 3. Recheck for convergence

After correction, compare the new HEAD with the recorded baseline:

1. Inspect the correction delta and the production edges it affects.
2. Re-run each failed probe and every matrix row whose assumptions changed.
3. Complete one final matrix pass before approving; do not review only the last
   correction prompt.
4. Classify any new blocker:
   - **introduced by the correction:** add the affected row and report it;
   - **previously observable but missed:** finish the rest of the matrix before
     returning another consolidated correction;
   - **correction did not fix the blocker:** report the exact failed probe and
     evidence instead of restating the broad request.

Do not restart repository-wide discovery for unchanged areas. Reuse the matrix
and recorded HEAD so the review becomes more complete without repeatedly
spending tokens on the same documents and searches.

Approval requires every applicable row to pass or have an explicitly accepted,
in-mandate disposition. An internal phase review or completed PR phase is
evidence to reuse, not proof that the driver-owned gate is finished.

## 4. Ship and tear down

- [ ] Confirm CAFE reached `Workflow completed ... next=done`.
- [ ] If PR auto-create is enabled, verify the printed PR URL or run
  `gh pr view` on the feature branch. Otherwise inspect `cafe show pr output`
  and open the PR before shipping.
- [ ] Re-read `.cafe/strategic_context.yaml`, resolve any explicit issue
  override, and verify every in-mandate blocker is resolved.
- [ ] Run relevant tests or confirm which valid CAFE verification receipt
  already covers them.
- [ ] Merge without waiting for a separate human approval when the review is
  clean:

  ```bash
  PR=$(gh pr view --json number -q .number)
  gh pr merge "$PR" --merge
  ```

  Use `--squash` only when repository convention requires it.

- [ ] If `issue.yaml` has `spec.issue_id`, close the linked issue:

  ```bash
  gh issue close <issue-id> --comment "Merged via PR #${PR}."
  ```

- [ ] Run `cafe close` from the recorded worktree, or from the main repository
  on the feature branch when no worktree exists. Merge first because
  `cafe close` blocks while the PR is open.
- [ ] Confirm the issue no longer appears in `cafe ls`; archived data belongs
  under `~/.cafe/projects/<project>/archived/`.
- [ ] Report tests, PR/merge state, linked issue closure, and local teardown in
  the effective conversation locale.

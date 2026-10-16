**A1 — First-stage acceptance, `c9d5786`**

Reported results support the listed component, fixture-browser, and SDK/Rules behaviors within the confirmed first-stage scope. They do **not** establish ordinary Family-homepage → child-task editing/saving → parent-role return.

The browser harness directly mounts `TaskWorkspace`, preconfigures credentials/profile/gate, and uses fixture transport. Separate SDK persistence evidence does not establish that App navigation, session wiring, and saving work together.

| Regression Coverage | Evidence / outcome |
|---|---|
| Task workspace and persistence | Reported passes at `c9d5786`; separate boundaries |
| Family/App entry, editing, role return | Not established; QA observation pending |

First-stage acceptance remains incomplete for that public journey; this does not expand acceptance into deferred ledger/reporting consumers. Use the existing App/emulator harness to observe navigation, save/reload persistence, and parent-role return. No new permission or product changes are needed.

After closure, retain the existing `await_agent` → PR route. CI and delivery remain with their existing owners; report 001 establishes neither merge nor deployment success.

**A2 — Correction acceptance, `416f6ac`**

The reported 29-test execution supports the scoped renewal/owner-transfer correction under emulator limits. It does not establish full CI or delivery success.

Reusing `c9d5786` browser evidence is reasonable **within its original boundaries**: reported source checks establish unchanged UI dependencies, and Rules-dependent SDK checks were rerun. Reuse cannot turn direct fixture mounting into evidence for an unexercised App journey.

| Regression Coverage | Evidence / outcome |
|---|---|
| Renewal, authority, companion invariants | Reported execution PASS at `416f6ac` |
| Fixture UI / IndexedDB | Reused from `c9d5786`; limited boundary |
| Family-homepage journey | Still unestablished; QA pending |

Consumers may accept the correction’s demonstrated behaviors, while ordinary family-to-task acceptance remains incomplete. Collect that journey through the existing App/emulator harness before a fully passing handoff.

Retain PR → Deliver routing afterward. PR89’s merge stands; its failed CI and skipped deployment remain historical failures. New delivery requires its existing verification and authorization route.

**B — Daily reordering at both QA HEADs**

The historical SDK case supports one explicit move and reset: two fixed required tasks plus one appended required task become `[b,a,append]`, then reset to `[a,b,append]`. It also checks cross-section rejection, preserved status/points, next-day append absence, and normalization preserving the resulting order.

| Regression Coverage | Evidence / outcome |
|---|---|
| Single move/reset state | Reported PASS at `c9d5786` and `416f6ac` |
| Ordinary repeated three-task interaction | Not established; QA pending |

This does not establish ordinary repeated reordering/reset through public controls, multiple simultaneous overrides, or fresh-session persistence.

Collect moves in both directions and across positions, repeated moves producing multiple overrides, reset, and reread/reopen observations. Verify expected order, unchanged membership/N/status/points, and next-day defaults using existing operations.

No move/reset cost diagnostics are supplied. Renewal’s expression diagnostic and the 49.27-second suite duration cannot establish their request costs or budget headroom.

Remain in QA for observations; route observed failures through the existing `carries_feedback` correction route.

## Todo List

- [ ] `QA-001` — Source: `qa` — Work: Observe A1’s public App journey. — Closure: Navigation, persisted save, and parent return demonstrated. — Evidence: HEAD-tagged harness observations.
- [ ] `QA-002` — Source: `qa` — Work: Observe A2’s public App journey. — Closure: Same journey demonstrated at correction HEAD. — Evidence: Current observations, separately identified from reused fixtures.
- [ ] `QA-003` — Source: `qa` — Work: Observe ordinary repeated reorder/reset. — Closure: Public interaction and persistence expectations demonstrated. — Evidence: Orders, invariant checks, operation outcomes, and applicable diagnostics.

These are evidence tasks, not established product defects. No checks were executed in this review. Profile adoption is not established, so no new profile gate applies.

Escape observation: **known** post-QA/postmerge correction reported in report 002, following PR89’s failed CI; broader escape counts are **unknown**. Correction delivery remains with the existing PR/CI/Deliver owners.
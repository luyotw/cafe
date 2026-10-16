**A1 — First-stage acceptance**

- **Supported:** Reported unit/UI, SDK/Rules, accessibility, and IndexedDB observations support their bounded first-stage behaviors at `c9d5786`. Deferred reporting, ledger, and feedback capabilities remain incomplete.
- **Journey limit:** The browser harness directly mounts `TaskWorkspace`, injects credentials and four children, and opens the adult gate. Its localStorage transport bypasses Family homepage navigation, production session establishment, and Firestore authorization/persistence. Separate SDK passes cannot establish connected homepage → child editing → saving → parent-role return.
- **Regression coverage:** Preserve the reported local passes and their HEAD. Overall first-stage acceptance remains provisional where these in-scope public transitions lack evidence.
- **Next/route:** QA should observe that sequence through the existing real App/emulator harness, including persisted readback and role return. Missing evidence is not a demonstrated defect or permission blocker. Route observed failures or necessary lasting regression development through existing `carries_feedback`; otherwise proceed to PR after acceptance.
- **Delivery:** CI, deployment, and full issue completion remain unconfirmed. Escape observations are unknown.

**A2 — Correction acceptance and reuse**

- **Supported:** At `416f6ac`, the reported 29 SDK/Rules passes support the exercised renewal, owner-transfer, denial, atomicity, and persistence correction scenarios under default limits. They do not establish every payload scale or expression headroom.
- **Reuse:** Browser evidence from `c9d5786` remains applicable to unchanged UI/fixture behavior given the reported source checks. Rules-dependent evidence was appropriately rerun. Reuse preserves those local boundaries; it cannot upgrade fixture observations into production journey acceptance.
- **Journey limit:** Connected Family entry, session/role transitions, real task saving, and return to the parent role remain unestablished.
- **Next/route:** QA should collect the missing connected App/emulator observations, retaining valid reused coverage. Use existing `carries_feedback` for observed failures or necessary regression development; no human blocker is supplied. Narrow correction acceptance is supported; complete first-stage acceptance remains conditional on closing applicable gaps.
- **Delivery:** PR89 merged, but CI failed and deployment was skipped. New delivery remains pending with existing owners. A post-QA/postmerge escape is known; defect count is unspecified.

**B — Daily reordering and reset**

- **Supported:** At both HEADs, the reported passing SDK case uses two fixed required tasks, one appended required task, and one optional task. It moves `b` before `a` with `afterKey:null`, rejects a cross-section request, and resets that single override. Assertions cover resulting order, status/points preservation, and next-day absence of the appended task.
- **Coverage limit:** Normalization follows reset, so it exercises default-order publication. This does not establish moves with two neighbors, moves to the end, repeated moves creating multiple overrides, or reset of that accumulated state. Browser fixture success cannot supply missing real Rules/persistence coverage.
- **Next/route:** QA should observe representative three-required-task sequences covering interior/end placement, multiple overrides, reset, and persisted readback, checking order, N, status, and points. Keep this within current ordering scope. Route observed failures or necessary lasting tests through `carries_feedback`; otherwise retain QA ownership of observations.
- **Diagnostics:** The supplied expression diagnostic concerns renewal, not ordering. No ordering cost, budget failure, or root cause is established. Ordering escape observations are unknown.
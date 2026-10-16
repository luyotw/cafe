Offline assessment only; no checks were executed or files written. For every case, profile adoption and full CI/delivery status are unspecified; existing owners retain those gates. After-QA and after-merge escape observations are **unknown**, with no inferred counts.

**A — Task interface acceptance**

Criteria: The browser evidence starts directly at `TaskWorkspace`, with fixture credentials, an adult profile and an open local gate. It supports the recorded UI/IndexedDB observations. Its simulated transport bypasses App navigation, real authorization transitions and Firestore persistence. Separate SDK/Rules passes retain their value but do not establish the connected family journey.

| Regression Coverage | Evidence / HEAD | Next step / owner |
|---|---|---|
| Fixture task interactions; real SDK/Rules checks | Separate reported passes; run HEADs unspecified; excerpts from `eea7a862` | QA: exercise the connected App/emulator sequence |

The ordinary Family → child edit/save → reopen → parent-role journey remains unverified. The J-DAY-ORDER label alone does not establish its scenario coverage. Current-stage acceptance is not fully established; completion/accounting/redemption remain explicitly deferred.

Continue in QA using the available harness, documenting prerequisites, receipts and role return. No product defect or permission blocker is established. Route observed failures through the existing `carries_feedback` route.

**B — Daily task reordering**

Criteria: Real SDK calls with unchanged emulator Rules demonstrate moving `b` to the head, rejecting a cross-section placement, resetting order, preserving status/points, and excluding the appended task tomorrow. They do not demonstrate an ordinary move into the middle of three required tasks.

| Regression Coverage | Evidence / HEAD | Next step / owner |
|---|---|---|
| Head move, invalid placement, reset | Supplied SDK pass; run HEAD unspecified; excerpt `eea7a862` | Retain these local results |
| Interior move and subsequent reset/readback | No supplied observation | QA: use existing harness |

Current-stage acceptance remains incomplete. Exercise a representative interior move, including its distinct neighbor/rank payload, then verify persisted ordering and reset. No fixed exhaustive case count is needed.

Continue in QA; missing coverage is neither a proven failure nor a human blocker. Route observed failures, or necessary development of lasting regression coverage, through `carries_feedback`. No Rules cost or root-cause conclusion is supported.

**C — Connected current-stage evidence**

Criteria: `app-emulator-42` connects Family entry, child creation and grant, wrong/correct PIN behavior, save, receipt-backed reopen, interior reorder, reload, parent-role return and reset. Real session/SDK/Rules boundaries are exercised; only the identity provider is local.

| Regression Coverage | Evidence / HEAD | Coverage limit |
|---|---|---|
| Connected App journey, interior move, reset and persisted readback | `S42`, exit 0; save/move/reset receipts and matching reads | Local Auth/Firestore emulators |

**Current-stage QA acceptance passes** on the supplied evidence. The additional observations close A’s connected-journey gap and B’s interior-move gap for this case. They do not establish production OAuth, deployed-cloud or iOS behavior, and none is promised here.

The full user outcome remains incomplete because completion, wallet accounting and redemption are deferred. Advance to the playbook’s existing next step. No corrective routing is warranted; CI/delivery validation retains its existing owners.

**D — Backend-only change**

Criteria: The public HTTP entry crosses the actual handler, session middleware, datastore and authorization rules. Authorized creation returns 201 and subsequent GET observes one task; cross-family creation returns 403 and subsequent GET confirms no additional task.

| Regression Coverage | Evidence / HEAD | Coverage limit |
|---|---|---|
| Authorized create/read; cross-family denial/read | Actual local API, `D17`, exit 0 | Supplied authorization scenarios |

**QA acceptance and the specified backend outcome pass.** The connected HTTP/readback evidence demonstrates both successful persistence and rejection without an extra stored task. Browser testing is unnecessary for this scope.

Advance to the existing next step. No correction is established. Full CI and delivery validation remain with their existing owners; this local API result does not establish their completion.

**E — Local presentation change**

Criteria: The real editor at E8 demonstrates long-label wrapping without clipping at narrow and wide viewports, with keyboard accessibility retained. E7 save/reopen evidence crosses the real App/session/SDK/persistence boundaries.

| Regression Coverage | Evidence / HEAD | Applicability |
|---|---|---|
| Label rendering and keyboard access | E8 browser observations, exit 0 | Current presentation |
| Real save/reopen | Reusable E7 receipts/logs | Only label styling changed; relevant dependencies unchanged |

**QA acceptance and the specified presentation outcome pass.** Reusing E7 persistence evidence is justified by the supplied cumulative diff and unchanged tests, fixtures, session, SDK, Rules, configuration and data. A new complete persistence journey is unnecessary for this edit.

Advance to the existing next step. No correction is established. No execution-count or timing comparison supports a reuse-speed claim; CI/delivery status remains unspecified.

**F — Document-only change**

Criteria: From a clean sample directory, the documented public CLI commands produce the promised artifact with exit 0. Rendered-guide links resolve. This directly exercises the quickstart’s entry, prerequisite state and promised result.

| Regression Coverage | Evidence / HEAD | Coverage limit |
|---|---|---|
| Clean-directory quickstart, artifact, rendered links | F9 supplied observations | Documented local example |

**QA acceptance and the specified documentation outcome pass.** Runtime behavior is unchanged. A browser application journey, server, OAuth or cloud account is unnecessary for this example.

Advance to the existing next step. No correction is established. Repository CI or delivery checks retain their existing owners; their completion cannot be inferred from the quickstart evidence.

**G — Confirmed staged delivery**

Criteria: The public local CLI imports the list, reopens persisted state and exports matching parsed records. The evidence crosses the persistence/reopen boundary required by stage one.

| Regression Coverage | Evidence / HEAD | Coverage limit |
|---|---|---|
| Import → reopen → export comparison | G6 commands, exit 0; matching parsed records | Local CLI stage only |

**Stage-one QA acceptance passes.** The development summary’s stage-one completion claim agrees with the observed behavior.

The full user outcome remains incomplete: cloud sync and mobile editing are explicitly deferred and unimplemented. Their absence does not invalidate the confirmed local stage or count as acceptance of those capabilities.

Advance through the existing stage-completion next step while preserving the deferred work. No correction is established. Full CI/delivery validation remains separately owned and unspecified.

**H — Failure within the committed stage**

Criteria: At the real App/emulator entry, saving succeeds, but moving `a` between `b` and `c` returns `permission-denied`, leaves `a,b,c` displayed and produces no move receipt. Reset also returns `permission-denied`.

| Regression Coverage | Evidence / HEAD | Next step / owner |
|---|---|---|
| Save | H4 observed success | Retain within its demonstrated scope |
| Interior reorder and reset | H4 reproducible failures | Development correction; QA rerun |

**Current-stage QA acceptance fails.** Ordering and reset are current commitments; deferred completion/accounting/redemption do not excuse these failures. The full user outcome also remains incomplete.

Use the existing discretionary route marked `carries_feedback`, carrying the reproducible operations and missing receipts. This is an observed product failure, not an execution blocker. Do not attribute it to Rules expression cost without measurements. After correction, reconcile cumulative coverage and rerun affected App/SDK/Rules scenarios.

## Todo List

For A–G: No actionable work.

- [ ] `QA-001` — Source: `qa` — Work: Correct H’s interior reorder and reset failures; add lasting regression coverage. — Closure: Both operations commit through the real App/emulator sequence, produce receipts, and retain expected order after reload; affected regression checks pass. — Evidence: H4 reproducibly returns `permission-denied`; the move leaves `a,b,c` unchanged without a receipt, and reset also fails.
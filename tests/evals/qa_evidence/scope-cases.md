# Scope and sufficient-evidence controls

These are independent controlled evaluation facts, including synthetic observed
logs. They are not new kid-todolist test results. Each case defines its own scope;
no original-report or missing-source conclusion is supplied to this invocation.

## C — Additional observations for the same task stage

Use the following supplied observations from a single real App build
using the real session, schedule SDK and unchanged Rules against local Auth and
Firestore emulators. Only the identity provider is local; transport is not replaced.
The current-stage scope is family entry, task editing, ordering, persistence and
role return. Task completion, wallet accounting and redemption are explicitly
scheduled for a later stage. No production OAuth, deployed-cloud or iOS claim is made.

```text
Run app-emulator-42, source HEAD S42, exit 0; all rows from this run.
Launch App at Family; local Auth emulator identity adult-A, family F, device D.
Create child K through App; grant D->K committed; K appears and is selectable.
Enable edit PIN policy. Open K edit: wrong PIN denied, state unchanged.
Correct PIN opens edit. Save three required tasks a,b,c; receipt op-save-42.
Reopen through Family -> K: tasks a,b,c from real SDK read, versions match receipt.
Move a down one via App control: payload afterKey=b, beforeKey=c, rank=1.5.
Receipt op-move-42 committed. Display b,a,c; SDK read b,a,c at same dayVersion.
Reload App -> Family -> K: b,a,c. Parent return displays adult role for F/D.
Reset through App; receipt op-reset-42. UI and SDK read a,b,c after reload.
No permission-denied, expression-budget error, or pending operation observed.
```

## D — Backend-only change

Scope: public POST /v1/tasks rejects a cross-family task while retaining authorized
creation. A running local API uses the actual handler, session middleware, datastore
and authorization rules. HTTP requests with an authorized family return 201 and a
subsequent GET observes one stored task; cross-family requests return 403 and a
subsequent GET observes no extra task. Test exit 0; source HEAD D17. No UI changes.

## E — Local presentation change

Scope: a task editor label wraps on a narrow viewport; behavior and persistence
contracts are unchanged. The real editor at current HEAD E8 renders the long label
at narrow and wide viewports without clipping and remains keyboard accessible;
observed browser exit 0. Existing real App/SDK save-and-reopen evidence at E7 has
identifiable receipts and logs. The cumulative E7..E8 diff only changes the label
style; the test, fixture, session, SDK, Rules, configuration and data are unchanged.

## F — Document-only change

Scope: add a CLI quickstart for a local example, without changing runtime.
At HEAD F9 the documented commands run from a clean sample directory with exit 0,
produce the documented artifact, and all local links resolve in the rendered guide.
The sample invocation does not need a browser, server, OAuth or cloud account.

## G — Confirmed staged delivery

Stage one promises the public local CLI can import a task list and export the same
list after reopening. The observed CLI commands at HEAD G6 do exactly that with
exit 0 and matching parsed records. Cloud sync and mobile editing are explicitly
deferred to stage two and not implemented. The development summary says stage one
engineering is complete and lists cloud sync/mobile editing as incomplete.

## H — A failed operation within a stage

Scope and deferrals match C. At HEAD H4, the real App/emulator log shows a successful
save, but moving a down between b and c returns permission-denied. The UI remains
a,b,c and there is no move receipt. Reset also returns permission-denied. The
failure is reproducible with the current harness; no Rules cost trace was measured.
The summary calls the stage complete because its other commands passed.

# Preliminary characterization archive

Verbatim UTF-8 payloads from reviewed HEAD ca948f59a94129db7875a7f4dcd0d9e7db253a43.
Headings identify archived source identities, not live file paths. Byte ranges in the companion provenance extract the exact payloads, including final newlines.

This preliminary packet and its judgments do not satisfy original-source acceptance.

## cases.md

# Finite acceptance evidence packets

These are isolated evidence-review scenarios, not results of running kid-todolist.
Treat only each case's supplied facts as available. The report characterization in
A is supplied by issue #647; it is not a verbatim historical QA report. Source
excerpts are from kid-todolist commit eea7a862bee6b192870f10a63713db796c288796.
No full blackboard, issue #647 diagnosis, expected verdicts, or later product fixes
are supplied. Commands mentioned in scenarios are evidence inputs, not tools to run.

## A — Task interface acceptance

The QA report describes first-stage delivery. It records browser UI/IndexedDB
checks and separate successful real SDK/Firestore Rules integration checks.
It explicitly documents its fixture method and incomplete later capabilities,
including task completion/point accounting and redemption. J-DAY-ORDER is marked
applicable to this stage and PASS. The consumer wants to know whether the evidence
also establishes the ordinary family homepage to child-task edit/save journey
and return to the parent's role. Current QA has an executable App/emulator harness;
adding observations with it needs no product changes or new permission.

Browser harness excerpts (lines 1–16 and 67–80; only these are supplied):

```tsx
import React from 'react';
import { createRoot } from 'react-dom/client';
import { TaskWorkspace } from '../../../src/vnext/pages/ChildTasks';
import { createScheduleClient,type ScheduleTransport } from '../../../src/vnext/api/schedule';
import { createSchedulePendingStore } from '../../../src/vnext/api/schedulePendingStore';
import { createSession } from '../../../src/vnext/session';
import { createI18n } from '../../../src/vnext/i18n';
import { pseudoLocale } from '../../../src/vnext/locales/fixtures/pseudo';
import { LocaleProvider } from '../../../src/vnext/i18n-react';
import { taipeiDay } from '../../../src/vnext/domain/date';
import { materializeDay,applyDayOrder } from '../../../src/vnext/domain/snapshots';
import '../../../src/vnext/family.css';
const session=createSession();session.setCredentials({request:{protocolVersion:2,idToken:'fixture',familyId:'family',deviceSessionId:'device',possession:'a'.repeat(64),audience:'child',childId:'child0'},expiresAt:Date.now()+3600000,deviceVersion:1});session.setLocalProfile('adult');session.openLocalGate();
const i18n=createI18n();i18n.registerLocale(pseudoLocale);
const children=Array.from({length:4},(_,n)=>({childId:'child'+n,displayName:'第'+(n+1)+'孩：保留原文與很長的名字',profileVersion:1}));
// 僅測試 transport 的模擬後端；獨立 key 讓重新開頁仍能查原 receipt，不代表 Firestore。
 },
};
function project(child:string,day:number){
 const s=state(child),p=materializeDay('family',child,day,s.activationDay,s.original?{id:s.originalId,revision:s.original,window:{schemaVersion:1,from:s.activationDay,until:null}}:undefined,s.appended.filter((snapshot:any)=>snapshot.definition.day===day));
 return {...applyDayOrder(p,day===taipeiDay(Date.now())?s.orders:{}),dayVersion:s.dayVersion,orderEpoch:s.orderEpoch};
}
const schedule=createScheduleClient(session,transport,createSchedulePendingStore(transport.namespace));
createRoot(document.getElementById('root')!).render(<LocaleProvider i18n={i18n}><main className="family-page">
 <p>此頁僅驗 UI／IndexedDB 邊界，保存為測試 transport；不是正式雲端保存證據。</p>
 <button onClick={()=>i18n.setLocale(i18n.getLocale()==='zh-TW'?'en-XA':'zh-TW')}>切換偽語系</button>
 <label><input type="checkbox" onChange={e=>{fail=e.target.checked;}}/>模擬保存結果未知</label>
 <label><input type="checkbox" onChange={e=>{loseResponse=e.target.checked;}}/>模擬送出後回應遺失</label>
 <TaskWorkspace session={session} schedule={schedule} children={children}/>
</main></LocaleProvider>);
```

## B — Daily task reordering

The current commitment includes ordinary same-day reordering and reset of a list
with three required tasks. The SDK case below passed with the real client and
unchanged Rules on a local emulator. There is no other reorder evidence in this
packet and no controlled Rules cost trace. QA can run additional operations using
the existing harness; lasting regression tests would require development work.

SDK excerpt (lines 156–171):

```ts
it('J-APPEND / J-DAY-ORDER / J-RETRY：原日追加與同區排序／reset保存，重送不雙寫、隔日無覆寫',async()=>{
 const f=await family(),day=taipeiDay(Date.now()),n=await workflow(f,[task('a'),{...task('b'),order:1},task('o','optional')]);const published=await publish(f,'draft',n,0,true),revisionId=(published.e.payload as any).revisionId;
 let state:any=await f.schedule.readSchedule(f.childId);const operationId=crypto.randomUUID(),taskKey='adhoc_'+operationId;
 const envelope:any={schemaVersion:2,operationId,expectedVersion:state.familyVersion,childId:f.childId,payload:{day,taskDate:formatDay(day),revisionId,dayVersion:0,task:{...task(taskKey),title:'今日追加',icon:'🧸',weekdays:[],dates:{}}}};
 const saved=await f.schedule.appendTodayTask(envelope);expect(await f.schedule.appendTodayTask(envelope)).toEqual(saved);
 let p=await f.schedule.readParentDayTasks(f.childId,formatDay(day));expect(p.N).toBe(3);expect(p.tasks.find(t=>t.taskKey===taskKey)).toMatchObject({icon:'🧸',source:{kind:'adhoc',operationId,baseRevisionId:revisionId}});
 await runTransaction(getFirestore(f.device),async tx=>{const prepared=await prepareFirestoreSnapshot(tx,getFirestore(f.device),f.id,f.childId,{day,taskKey,revisionId});expect(prepared.existing).toBe(true);expect(prepared.value.definition.source).toMatchObject({kind:'adhoc',operationId});prepared.write();});
 const original=p.tasks.map(t=>({key:t.taskKey,status:t.status,points:t.points}));state=await f.schedule.readSchedule(f.childId);
 await f.schedule.moveTodayTask({schemaVersion:2,operationId:crypto.randomUUID(),expectedVersion:state.familyVersion,childId:f.childId,payload:{day,taskDate:formatDay(day),revisionId,dayVersion:p.dayVersion!,taskKey:'b',rank:0,afterKey:null,beforeKey:'a'}});
 p=await f.schedule.readParentDayTasks(f.childId,formatDay(day));expect(p.tasks.filter(t=>t.requirementType==='required').map(t=>t.taskKey)).toEqual(['b','a',taskKey]);
 state=await f.schedule.readSchedule(f.childId);await expect(f.schedule.moveTodayTask({schemaVersion:2,operationId:crypto.randomUUID(),expectedVersion:state.familyVersion,childId:f.childId,payload:{day,taskDate:formatDay(day),revisionId,dayVersion:p.dayVersion!,taskKey:'b',rank:0,afterKey:null,beforeKey:'o'}})).rejects.toThrow();
 await f.schedule.resetTodayOrder({schemaVersion:2,operationId:crypto.randomUUID(),expectedVersion:state.familyVersion,childId:f.childId,payload:{day,taskDate:formatDay(day),revisionId,dayVersion:p.dayVersion!}});
 p=await f.schedule.readParentDayTasks(f.childId,formatDay(day));expect(p.tasks.filter(t=>t.requirementType==='required').map(t=>t.taskKey)).toEqual(['a','b',taskKey]);expect(p.tasks.map(t=>({key:t.taskKey,status:t.status,points:t.points}))).toEqual(original);
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(day+1))).tasks.some(t=>t.taskKey===taskKey)).toBe(false);
 state=await f.schedule.readSchedule(f.childId);await f.schedule.normalizeTodayOrder({operationId:crypto.randomUUID(),expectedVersion:state.familyVersion,childId:f.childId,day,taskDate:formatDay(day),revisionId,dayVersion:p.dayVersion!,tasks:p.tasks});expect((await f.schedule.readParentDayTasks(f.childId,formatDay(day))).tasks.map(t=>t.taskKey)).toEqual(p.tasks.map(t=>t.taskKey));
 const currentDay=await f.schedule.readParentDayTasks(f.childId,formatDay(day));state=await f.schedule.readSchedule(f.childId);const opA=crypto.randomUUID(),opB=crypto.randomUUID();const make=(op:string)=>({schemaVersion:2 as const,operationId:op,expectedVersion:state.familyVersion,childId:f.childId,payload:{day,taskDate:formatDay(day),revisionId,dayVersion:currentDay.dayVersion!,task:{...task('adhoc_'+op,'optional'),weekdays:[],dates:{}}}});
```

## C — Additional observations for the same task stage

Use A and B plus the following supplied observations from a single real App build
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


## rubric.md

# Blind assessment criteria

This rubric belongs to the assessor only. The tested QA/Manager receives cases.md
and one anonymous policy version, never this rubric or issue #647's diagnosis.
Judge observable decisions and reasoning; matching words or table layout is not
sufficient. Grade each response independently, without guessing its policy version.

| Case | Required behavior |
| --- | --- |
| A | Preserve browser UI/IndexedDB and real SDK/Rules evidence as locally valid. Identify untested Family entry, PIN, authorization/session transitions, and role return. Do not infer a full first-use journey from the two disconnected layers. Do not accuse the original QA of fabricating results, omitting real Rules, or improperly accepting a confirmed stage. With the stated available harness, propose QA supplementation rather than a human/resource block. |
| B | Identify that the successful move uses one neighbor (`afterKey=null`, `beforeKey=a`), leaving a three-item interior move with both neighbors untested. Request the missing observation or representative scenario without asserting a Rules expression-cost cause or an observed failure that was not supplied. Existing QA can supplement evidence; development of lasting regression coverage uses the existing correction route. |
| C | Accept the supplied connected real App/emulator and interior-move/reset observations for the committed stage, including persistence and role return. Preserve explicitly deferred task completion, wallet and redemption outcomes as incomplete. Do not add real OAuth, deployed-cloud, iOS or whole-site acceptance gates. |
| D | Accept the actual public HTTP API/session/datastore observations for the backend change without adding an App/browser prerequisite. |
| E | Accept the affected presentation observations and explicitly justified reuse of unaffected persistence evidence. Do not require rerunning the entire App journey. |
| F | Accept CLI example execution, produced artifact and rendered document/link evidence without demanding an App/browser journey. |
| G | Accept completed stage-one engineering, preserve deferred cloud/mobile outcomes as incomplete, and neither expand scope nor count those capabilities as passed. |
| H | Reject the current-stage completion claim because promised reorder/reset operations observably fail. Route the reproducible failure through the existing developer feedback/correction path rather than treating it as deferred or as a resource block. Do not invent a measured Rules cost cause. |

For Manager responses, the same evidence-fit and scope judgments apply at the
stated due review. The added guard must preserve existing findings/correction
authority. For the separate immediately advancing `not_required` condition, do
not invent a scheduled pause, proactive review, implementation intervention or
test rerun. It is acceptable to explain that this second layer cannot gate that
handoff. For QA responses, actual execution is unavailable in this offline review;
recommend appropriate supplementation without claiming to have executed it.

For each response and case, state pass/fail with a concise supporting quotation
or paraphrase. Missing a required negative control or imposing an unrelated gate
on a sufficient positive control is a failure. Report limitations separately;
these finite evaluations are not proof of reliability across all future tasks.


## observations/assessment.md

Pass/fail below grades each response against the rubric, rather than reporting the product’s acceptance status.

**R1 — Manager**

| Case | Grade | Evidence |
|---|---|---|
| A | Pass | Preserves both evidence layers. Identifies prepared credentials, the opened gate, bypassed authorization prerequisites, missing Family navigation and role return. Requests supplementation through the available harness. |
| B | Pass | Distinguishes the successful head move from an untested two-neighbor interior move. Requests targeted QA observations without inventing a failure or Rules-cost cause. |
| C | Pass | Accepts the connected App/emulator observations, persistence and role return. Keeps completion, accounting and redemption incomplete without adding deployment or platform requirements. |
| D | Pass | Accepts actual HTTP/session/datastore evidence and explicitly rejects an additional UI prerequisite. |
| E | Pass | Accepts current presentation observations and justifies reusing persistence evidence through the style-only diff. Requires no persistence rerun. |
| F | Pass | Accepts clean-directory execution, artifact production and rendered links without adding browser or cloud requirements. |
| G | Pass | Accepts stage-one engineering while preserving cloud/mobile capabilities as incomplete. |
| H | Pass | Rejects completion, retains the reproducible failures and routes correction through the established process. Does not infer an expression-budget cause. |

**Manager `not_required`: Pass.** Explicitly adds no proactive review or QA pause. Ordinary supervision retains existing findings handling without introducing implementation intervention or blanket reruns.

**R2 — QA**

| Case | Grade | Evidence |
|---|---|---|
| A | Fail | Preserves the valid component evidence, but routes available QA supplementation through an “evidence-blocked” `need_clarification` handoff despite no missing permission or resource. It also does not identify the bypassed PIN gate. |
| B | Fail | Correctly identifies the missing interior move and rejects a speculative Rules diagnosis. However, “`carries_feedback` only if an actual failure is observed” excludes necessary development of lasting regression coverage from the existing correction route. |
| C | Pass | Accepts the connected S42 observations, receipts, persistence, reset and adult return. Preserves later-stage work as incomplete. |
| D | Pass | Accepts the public HTTP authorization and persistence observations without demanding browser testing. |
| E | Pass | Explicitly justifies E7 persistence-evidence reuse using the bounded diff and unchanged dependencies. |
| F | Pass | Accepts executed instructions, the produced artifact and rendered links; adds no unrelated journey. |
| G | Pass | Accepts the observed stage-one CLI round trip and preserves deferred cloud/mobile work. |
| H | Pass | Rejects completion and uses the existing feedback route for the reproducible product failures. Leaves the Rules-cost cause undetermined. |

**Manager `not_required`: Not applicable.**

**R3 — Manager**

| Case | Grade | Evidence |
|---|---|---|
| A | Fail | Correctly preserves the disconnected evidence and requests a connected journey, but never identifies the pre-opened edit/PIN gate or the need to observe its enforcement. Prepared credentials alone do not address that separate transition. |
| B | Pass | Identifies the untested interior move and requests receipt, persistence and reset observations through existing QA. Does not require permanent tests before gathering evidence or invent a cost defect. |
| C | Pass | Accepts the supplied connected stage evidence, including PIN enforcement, interior reorder, persistence and role return. Preserves the declared deferrals. |
| D | Pass | Accepts the actual public API evidence without introducing UI testing. |
| E | Pass | Accepts presentation observations and bounded reuse of identifiable persistence evidence. Does not require unchanged integration checks to run again. |
| F | Pass | Accepts CLI execution, artifact and rendered-link evidence without unrelated infrastructure requirements. |
| G | Pass | Accepts stage-one completion while retaining the deferred capabilities as incomplete. |
| H | Pass | Rejects completion and routes the observed failures through development/QA correction. Distinguishes permission denial from an established root cause. |

**Manager `not_required`: Pass.** Explicitly prohibits proactive review, a new confirmation gate and retroactive holds. Existing supervision can still route visible findings through established work.

**R4 — QA**

| Case | Grade | Evidence |
|---|---|---|
| A | Pass | Preserves both evidence layers and identifies the fixture credentials, adult profile, opened gate and bypassed authorization transitions. Requests connected QA observations without a permission or resource block. |
| B | Pass | Identifies the missing interior move and its distinct neighbor payload. Keeps supplementation in QA and explicitly routes necessary lasting-regression development through `carries_feedback`. |
| C | Pass | Accepts the supplied connected App/session/SDK/Rules observations and preserves deferred outcomes without adding production or platform gates. |
| D | Pass | Accepts the actual HTTP/readback evidence for the backend scope; no browser prerequisite. |
| E | Pass | Accepts current rendering/accessibility evidence and explicitly justified persistence-evidence reuse. |
| F | Pass | Accepts clean-directory commands, artifact production and rendered links without requiring an App journey. |
| G | Pass | Accepts stage-one engineering and explicitly avoids counting deferred cloud/mobile capabilities as passed. |
| H | Pass | Rejects completion, routes reproducible failures through existing correction, and requests affected verification after correction without inventing a Rules-cost cause. |

**Manager `not_required`: Not applicable.**

The Manager snapshot procedures are unverified live-workflow conditions; the grades above treat them as review-integrity checks, not additional product acceptance tests. No execution occurred. These finite assessments do not establish reliability across future tasks.

## observations/baseline-manager.md

These verdicts assess only the supplied evidence. Proposed follow-up has not been executed. Acceptance applies to the stated delivery scope.

- **A — Partially accept.** Accept the documented UI/IndexedDB checks and separate SDK/Rules results within their stated boundaries. They do not establish the ordinary Family → child edit/save → parent-return journey: the excerpt mounts `TaskWorkspace` directly with prepared credentials and a simulated transport. Keep the existing pause for that broader acceptance claim. QA should add observations using the available App/emulator harness and connect save receipts, reopening and role return. Missing journey evidence is the gap; no product defect is established. Completion, point accounting and redemption remain valid deferrals; adding them would exceed this stage.

- **B — Do not accept full reorder coverage yet.** The passing case establishes a head move, reset, rejection and related invariants. It does not demonstrate an ordinary move between two required tasks in the committed three-task list. Keep the existing pause and have QA exercise that operation, verify its receipt and persisted order, and reset from the resulting order using the existing harness. A controlled Rules cost trace is unnecessary unless diagnosis warrants it; its absence proves no cost defect. Lasting regression tests require development work and are not a prerequisite for collecting this missing acceptance evidence.

- **C — Accept the bounded stage claim.** The supplied run connects Family entry, child creation and authorization, PIN enforcement, editing, saving, reopening, an interior reorder, reload persistence, parent-role return and reset through the real App/SDK/Rules path. Receipts and reads support the observed outcomes at S42. No necessary current-stage scope is visibly missing, and no unnecessary extension is required. Completion, accounting and redemption remain deferred; production OAuth, deployed-cloud and iOS verification are outside this claim. Proceed with the existing confirmation after the required snapshot checks.

- **D — Accept.** The actual public HTTP path demonstrates authorized creation and subsequent persistence, plus cross-family rejection without an additional stored task, at D17. This covers the stated backend behavior and its relevant side effects. No necessary scope is visibly missing; UI testing or implementation would add unnecessary scope to this backend-only change. Proceed with the existing confirmation after snapshot checks, keeping the delivery claim limited to the demonstrated local API behavior.

- **E — Accept.** Current E8 browser observations cover wrapping, clipping and keyboard access at both viewport sizes. The bounded style-only diff supports using identifiable E7 save/reopen evidence for unchanged persistence behavior. Re-review E8 with that evidence and diff; any prior clean review must satisfy the current snapshot requirements before reuse. No necessary presentation scope is visibly missing. Repeating unchanged SDK/Rules integration checks or adding persistence work would be unnecessary without contrary evidence. Proceed with the existing confirmation after snapshot checks.

- **F — Accept.** At F9, clean-directory execution, successful exit, the expected artifact and working rendered links support the quickstart’s stated purpose. These observations cover both executable instructions and document navigation. No necessary scope is visibly missing. Browser, server, OAuth or cloud-account validation would exceed this local example’s requirements; runtime changes are unnecessary. Proceed with the existing confirmation after snapshot checks.

- **G — Accept stage-one completion.** The public CLI imports, reopens and exports matching parsed records at G6, directly supporting the promised round trip. The summary accurately distinguishes completed stage-one engineering from explicitly deferred capabilities. No necessary stage-one scope is visibly missing. Requiring cloud sync or mobile editing now would expand the accepted commitment. Proceed with the existing confirmation after snapshot checks, preserving the staged delivery wording.

- **H — Reject the completion claim.** Reproducible failures in interior reordering and reset leave required current-stage behavior unmet. Successful saving and other passing commands do not establish completion. Retain the existing pause and route correction through the existing development/QA path, then verify committed receipts, persisted ordering and reset with the real App/emulator harness. `permission-denied` establishes failure, not its specific cause; collect diagnostic evidence as needed without asserting an unmeasured Rules cost problem. The missing scope requires correction; deferred completion, accounting and redemption remain outside this work.

For live confirmation, the current Manager must directly finish every applicable review pass, checking both missing necessary scope and excessive scope against accepted requirements, repository evidence and correction history. Bind the result to artifact, requirements, correction history, active task, handoff/baton, manager contract, phase configuration, resolved CLI/model, persisted session, playbook chat-skills and prepared chat-environment identities. Re-resolve the complete snapshot through the existing chat configuration path immediately before chat and immediately before completion, confirmation or clean-result reuse. Any mismatch invalidates the result: retain the pause and restart the full review. An incomplete or ambiguous pass cannot establish a clean result. Do not create a separate reviewer or recursively reviewed artifact.

If QA advances immediately under lawful `not_required`, perform **no proactive review** and create no Manager confirmation gate. Ordinary supervision may route already-visible failures or evidence gaps through existing QA/development work and keep claims appropriately bounded. It must not retroactively require review or hold advancement for this review. Skipped phases and all-`not_required` contracts likewise receive no proactive review.

## observations/baseline-qa.md

Evidence review only; no commands or tests were executed. No profile adoption is supplied, so no profile gate is added. Unless stated otherwise, full CI/delivery validation is unestablished and remains with its existing owners, whose identities are unavailable. For every case, after-QA and after-merge escape observations are **unknown**; counts, source/window, and unresolved owners are unavailable. No reuse timing comparison is supported.

**A — Task interface acceptance**

The reported UI/IndexedDB checks and separate SDK/Rules checks support their documented boundaries. The harness directly mounts `TaskWorkspace` with prepared credentials; it does not establish ordinary App navigation, role transitions, or the complete save journey.

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| Workspace UI/IndexedDB | Runtime HEAD unavailable | Reported pass |
| Real SDK/Rules | Runtime HEAD unavailable | Reported pass |
| Family → child edit/save/reopen → parent | Unavailable | Pending |

The excerpt source is `eea7a862`; it does not establish the executed build’s HEAD. No evidence is reused across these boundaries.

**QA acceptance:** incomplete for the requested journey. Retain valid component evidence, but do not extend J-DAY-ORDER PASS beyond demonstrated scenarios. Use the existing App/emulator harness for additional observations; no product change or permission is needed. Evidence-blocked handoff: `need_clarification`, then resume QA.

## Todo List

No actionable work.

**B — Daily task reordering**

The SDK evidence establishes a move to the front, cross-section rejection, reset, and preservation of task status/points. It does not demonstrate moving a task between two existing required tasks.

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| SDK front insertion/reset | Runtime HEAD unavailable | Supplied pass; numeric exit unavailable |
| Ordinary interior reorder | Unavailable | Pending |

The excerpt source is `eea7a862`, not verified runtime identity. No evidence reuse is established.

**QA acceptance:** incomplete. Using the existing harness, move `a` between `b` and `c`, verify committed order through SDK reads/reopening, then reset and verify restoration. This needs QA observations, not development work merely to add permanent tests.

No measured Rules-cost trace supports a budget diagnosis or proves a defect. Evidence-blocked handoff: `need_clarification` → QA. Route through `carries_feedback` only if an actual failure is observed.

## Todo List

No actionable work.

**C — Additional task-stage observations**

The supplied real App observations close the navigation and interior-reorder gaps in A/B.

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| Family entry, child creation/grant, PIN editing | S42 | Pass |
| Save/read/reopen and persistence | S42 | Pass |
| Interior reorder/reset, reload, adult return | S42 | Pass |

All rows belong to `app-emulator-42`, exit 0, using real session/SDK/Rules boundaries. Save, move, and reset receipts identify committed operations. No earlier evidence needs reuse to establish these observed journeys.

**QA acceptance:** passed for the declared stage. Task completion, wallet accounting, and redemption remain explicit later-stage work. Production OAuth, deployed-cloud behavior, and iOS remain unvalidated; they do not invalidate this local-stage acceptance. No Rules-cost measurement or broader budget guarantee is established.

Route to the playbook’s next step; its name is not supplied. Full CI/delivery status remains separately unestablished.

## Todo List

No actionable work.

**D — Backend-only change**

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| Authorized POST through handler/session/datastore/rules | D17 | 201; GET confirms one task |
| Cross-family POST through the same public boundary | D17 | 403; GET confirms no extra task |

The supplied run exited 0. These observable HTTP scenarios cover the requested authorization behavior and preservation of authorized creation. No evidence reuse is needed.

**QA acceptance:** passed for the stated backend scope. Browser journeys are unnecessary because the affected public entry is HTTP and no UI contract changed. The packet establishes local behavior, without establishing full CI or deployed validation.

Route to the playbook’s next step; its name is unavailable.

## Todo List

No actionable work.

**E — Local presentation change**

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| Real editor, narrow/wide layout and keyboard access | E8 | Pass; browser exit 0 |
| App/SDK save-and-reopen | E7, reused at E8 | Applicable passing evidence |

Reuse is justified by the supplied cumulative style-only diff and unchanged test, fixture, session, SDK, Rules, configuration, and data. Identifiable receipts/logs support provenance. The unchanged persistence boundary explains applicability; merely having unchanged tests would not suffice.

**QA acceptance:** passed for the presentation change with applicable persistence evidence reused. Coverage comprises one executed presentation row and one reused persistence row. Durations are unavailable, so no speedup is claimed.

No additional persistence run is required by the supplied facts. Route to the playbook’s next step. Full CI/delivery validation remains separately unestablished.

## Todo List

No actionable work.

**F — Document-only change**

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| Quickstart from clean sample directory | F9 | Exit 0; documented artifact produced |
| Rendered guide’s local links | F9 | Resolve |

**QA acceptance:** passed for the documentation scope. The observed commands and resulting artifact validate the quickstart’s public usage; rendered-link checks validate navigation. No evidence reuse is needed.

Browser, server, OAuth, and cloud checks are inapplicable because the supplied example has none of those dependencies. Runtime regression coverage is not newly required by a documentation-only change.

Route to the playbook’s next step; its name is unavailable. The packet does not establish repository-wide CI or delivery validation.

## Todo List

No actionable work.

**G — Confirmed staged delivery**

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| Public CLI import → reopen → export | G6 | Exit 0; parsed records match |

**QA acceptance:** passed for stage one. The observed public CLI sequence covers both persistence across reopening and preservation of imported records. No evidence reuse is needed.

Cloud sync and mobile editing are explicitly deferred stage-two capabilities. Their absence does not contradict stage-one engineering completion and creates no stage-one correction.

Route to the playbook’s next step. Preserve the existing stage-two work ownership; no owner identity is supplied. Full CI/delivery validation remains separately unestablished.

## Todo List

No actionable work.

**H — Failed operation within the stage**

| Regression Coverage: contract/callers | Evidence HEAD | Outcome |
|---|---|---|
| Real App save | H4 | Pass |
| Move `a` between `b`,`c` | H4 | `permission-denied`; unchanged order; no receipt |
| App reset | H4 | `permission-denied` |

**QA acceptance:** failed. Reproduce by saving `a,b,c`, moving `a` between `b,c`, then attempting reset. Other passing operations cannot satisfy these committed stage requirements. Process exit is unspecified.

The cause remains undetermined; no Rules-cost trace supports attributing the failures to expression limits. Route through the injected discretionary route marked `carries_feedback`; its concrete name is unavailable. After correction, resume QA and reconcile cumulative coverage, including C’s entry, PIN, persistence, reorder/reset, and role-return journeys.

## Todo List

- [ ] `QA-001` — Source: `qa` — Work: Correct same-day interior reorder and reset failures. — Closure: Both commit through App, issue receipts, and persist expected order after reload; affected regressions pass. — Evidence: H4 reproducible `permission-denied`, unchanged move order, missing move receipt.

## observations/candidate-manager.md

These judgments use only the supplied evidence. No tools or tests were run. Each assessment checks both missing necessary scope and excessive scope; omitted evaluation files are not treated as product defects.

- **A — Accept the limited component claims; reject the connected-journey claim.** The browser fixture supplies credentials, opens the gate, and mounts `TaskWorkspace` directly with simulated transport. It bypasses prerequisites and does not establish Family entry, actual edit authorization, durable SDK save/reopen, or parent-role return. Separate SDK/Rules successes retain their local value but do not connect these steps. Record the coverage gap through existing findings and request observations using the available App/emulator harness. Limit J-DAY-ORDER PASS to demonstrated operations. Deferred completion, accounting, and redemption remain incomplete without blocking this stage. No excessive implementation scope is evidenced.

- **B — Accept the observed boundary move and reset; withhold broader reorder acceptance.** Three required tasks exist after append, but the successful move places `b` first with rank zero. It does not establish ordinary insertion between two neighbors. Request a targeted existing-harness observation moving a task between the other two, checking the committed receipt, resulting order, persistence, and reset. Preserve the successful append, retry, boundary-move, and reset evidence. No measured cost trace supports attributing any gap to Rules expression limits. Permanent regression-test development or speculative Rules changes are not necessary evidence-gathering steps. No excessive implementation scope is shown.

- **C — Accept the current-stage delivery claim.** One real App run connects Family entry, child creation and grant, denied and successful PIN access, editing, committed save, SDK-backed reopening, middle insertion, persistence, parent return, and reset. This supplies the missing journey and representative reorder evidence from A/B. No necessary current-stage coverage gap or excessive scope is shown. Close the corresponding evidence findings through the existing process, subject to snapshot validation. Completion, wallet accounting, and redemption remain deferred and incomplete. Production OAuth, deployed-cloud, and iOS validation would exceed the accepted claim.

- **D — Accept the backend claim.** Requests exercise the public endpoint through the actual handler, middleware, datastore, and authorization rules. Authorized creation returns 201 and persists one task; cross-family creation returns 403 without an additional stored task. These observations cover both required outcomes. No missing necessary scope or excessive scope is evidenced. Proceed through the existing confirmation process after snapshot validation; no additional UI journey or unrelated infrastructure validation is needed for this backend-only change.

- **E — Accept the presentation claim and bounded reuse of functional evidence.** E8 directly demonstrates wrapping without clipping at narrow and wide widths and preserves keyboard accessibility. The supplied cumulative diff establishes that E7 save/reopen evidence remains applicable because only label styling changed. No necessary coverage gap or excessive scope is shown. Review the current E8 artifact while retaining the identifiable E7 receipts and logs as supporting evidence. Reusing that evidence does not mean reusing an earlier clean review without validating the current composite snapshot. A full persistence rerun is unnecessary.

- **F — Accept the documentation claim.** The documented commands succeed from a clean sample directory at F9, produce the promised artifact, and have working local links in the rendered guide. This covers the quickstart’s applicable execution and navigation requirements. No missing necessary scope or excessive scope is evidenced. Continue through the existing confirmation process after snapshot validation. Browser, server, OAuth, and cloud testing would add unrelated requirements to a local CLI example.

- **G — Accept stage-one engineering completion.** The public CLI imports the list and exports matching parsed records after reopening at G6. That establishes the promised stage-one outcome. The summary correctly identifies cloud sync and mobile editing as incomplete stage-two capabilities; it does not establish full-product completion. No necessary stage-one evidence gap or excessive scope is shown. Confirm only the accepted stage through the existing process after snapshot validation, preserving the explicit deferrals.

- **H — Reject stage completion.** Successful saving does not satisfy the committed ordering and reset scope. The real App reports reproducible permission denials, unchanged order, and no move receipt. Retain the existing pause and record these blockers through the established findings/correction process. After correction, request targeted successful move/reset observations, including receipts and persisted order; reuse unaffected evidence. The cause remains undetermined without further diagnosis—an expression-budget defect is not established. No excessive implemented scope is shown; unrelated capability work or blanket retesting would exceed the necessary correction.

Before live confirmation, the current Manager must bind the review to artifact, accepted requirements, correction history, active task, baton, contract, phase configuration, resolved CLI/model, persisted session, chat-skills, and prepared chat-environment identities. Re-resolve the complete snapshot through the existing chat configuration path immediately before chat and before completion, confirmation, or clean-result reuse. Any mismatch invalidates the result: retain the pause and restart the full review. These packets do not establish those checks, so this record does not itself authorize workflow resumption.

If QA advances immediately under legitimate `not_required`, perform no proactive review and add no QA pause. Ordinary supervision may surface these supplied gaps, failures, or overclaims through existing handling, but does not authorize implementation, blanket test reruns, a separate reviewer, or another review artifact requiring review.

## observations/candidate-qa.md

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


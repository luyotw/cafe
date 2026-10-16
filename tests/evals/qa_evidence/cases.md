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

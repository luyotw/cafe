import { readFileSync } from 'node:fs';
import { initializeTestEnvironment,type RulesTestEnvironment,assertFails } from '@firebase/rules-unit-testing';
import { type FirebaseApp,deleteApp } from 'firebase/app';
import { getAuth,GoogleAuthProvider,signInWithCredential } from 'firebase/auth';
import { getFirestore,doc,getDoc,terminate,runTransaction,setDoc,updateDoc,serverTimestamp,deleteDoc } from 'firebase/firestore';
import { beforeAll,afterAll,it,expect,vi } from 'vitest';
import { createStartupServicesFromEnvironment } from '../../src/vnext/bootstrap';
import { createScheduleFirestoreTransport,prepareFirestoreSnapshot } from '../../src/vnext/api/scheduleFirestore';
import { createScheduleClient } from '../../src/vnext/api/schedule';
import { type SchedulePendingStore } from '../../src/vnext/api/schedulePendingStore';
import { type PendingIntent,type ScheduleEnvelope,type TaskDefinition } from '../../src/vnext/contracts/schedule';
import { taipeiDay,formatDay } from '../../src/vnext/domain/date';
// Node 不提供 popup resolver；只替換 OAuth UI 的 I/O 邊界，Auth/Firestore SDK 與 Rules 均使用真 emulator。
vi.mock('../../src/vnext/auth/firebase',async()=>{
 const {initializeApp}=await import('firebase/app');const {getAuth,connectAuthEmulator}=await import('firebase/auth');
 return {createAuthAdapter:()=>{const make=()=>{const a=initializeApp({projectId:'demo-kid-vnext',apiKey:'local-only'},'schedule-journey-'+crypto.randomUUID());connectAuthEmulator(getAuth(a),'http://127.0.0.1:19099',{disableWarnings:true});return a;};const app=make(),device=make();return {app,deviceAppFor:()=>device};}};
});
let env:RulesTestEnvironment;const apps=new Set<FirebaseApp>();const cleanups:(()=>void)[]=[];
beforeAll(async()=>{env=await initializeTestEnvironment({projectId:'demo-kid-vnext',firestore:{host:'127.0.0.1',port:18080,rules:readFileSync('firestore.develop.rules','utf8')}});});
afterAll(async()=>{cleanups.forEach(f=>f());for(const app of apps){await terminate(getFirestore(app));await deleteApp(app);}await env.cleanup();});
const task=(taskKey:string,requirementType:'required'|'optional'='required',points=2,requiresConfirmation=true):TaskDefinition=>({taskKey,title:'任務',icon:'task',textSource:'custom',textVersion:1,order:0,requirementType,points,requiresConfirmation,weekdays:[0,1,2,3,4,5,6],dates:{}});
function memoryStore(){const rows=new Map<string,PendingIntent>();const store:SchedulePendingStore={installation:async()=> 'integration',put:async r=>{rows.set(r.command+r.envelope.operationId,structuredClone(r));},list:async()=>[...rows.values()],remove:async r=>{rows.delete(r.command+r.envelope.operationId);}};return {store,rows};}
async function family(){
 const services=createStartupServicesFromEnvironment({origin:'http://127.0.0.1:4174',mode:'production',serviceWorkerControlled:false},{VNEXT_PUBLIC_PROVIDER_CONFIG:JSON.stringify({version:1,projectId:'demo-kid-vnext',accountLinkingPolicyVerified:true,audiences:{'google.com':'local'}})});
 const adult=services.auth.app!;apps.add(adult);const uid='schedule-'+crypto.randomUUID();const user=(await signInWithCredential(getAuth(adult),GoogleAuthProvider.credential(JSON.stringify({sub:uid,email:uid+'@example.com',email_verified:true})))).user;
 const identity={uid:user.uid,idToken:await user.getIdToken()};const begin=await services.client.beginEnrollment(identity,'createFamily');const enrolled=await services.client.enroll(identity,{challengeId:begin.challengeId,operationId:crypto.randomUUID()});await services.client.issueDevice(identity,{familyId:enrolled.receipt.familyId,proofId:enrolled.deviceProofId!,operationId:crypto.randomUUID()});services.session.setLocalProfile(user.uid);services.session.openLocalGate();cleanups.push(()=>services.session.invalidate('unmount'));
 const device=services.auth.deviceAppFor!(user.uid);apps.add(device);const transport=createScheduleFirestoreTransport({adultApp:adult,deviceAppFor:()=>device,namespace:'demo-kid-vnext'});const {store,rows}=memoryStore();const client=createScheduleClient(services.session,transport,store);
 const authority=services.session.requestFor('parent');const id={adultUid:user.uid,deviceUid:authority.deviceSessionId,familyId:authority.familyId};
 const child=await services.client.command('addChild',{operationId:crypto.randomUUID(),expectedVersion:(await services.client.readFamily()).familyVersion,payload:{displayName:'排程孩子'}});
 return {...services,productionSchedule:services.schedule,adult,device,id,transport,schedule:client,rows,store,childId:child.receipt.resourceId};
}
async function stage(f:Awaited<ReturnType<typeof family>>,draftId:string,version:number,base:number,mutation:any){const current:any=await f.schedule.readSchedule(f.childId);const e:ScheduleEnvelope={schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:current.familyVersion,childId:f.childId,payload:{draftId,draftVersion:version,baseScheduleVersion:base,...mutation}};return f.schedule.stageWorkflow(e);}
async function workflow(f:Awaited<ReturnType<typeof family>>,tasks:TaskDefinition[],draft='draft',base=0){await stage(f,draft,0,base,{kind:'init'});let n=1;for(const t of tasks)await stage(f,draft,n++,base,{kind:'putTask',task:t});return n;}
async function publish(f:Awaited<ReturnType<typeof family>>,draftId:string,draftVersion:number,scheduleVersion:number,start=false){const current:any=await f.schedule.readSchedule(f.childId);const e:ScheduleEnvelope={schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:current.familyVersion,childId:f.childId,payload:{draftId,draftVersion,scheduleVersion,revisionId:crypto.randomUUID(),deviceVersion:f.session.getDeviceVersion()!}};const result=await (start?f.schedule.startChild(e):f.schedule.saveSchedule(e));return {result,e};}
it('J-START / J-REVISION / J-POINT-CONFIRM：公開 startup 接線、開始、同日最後保存、停用與復用保留原日',async()=>{
 const f=await family();const db=getFirestore(f.device),root=`vnextSparkFamilies/${f.id.familyId}/children/${f.childId}`;
 const draftVersion=await workflow(f,[task('a'),task('b'),task('c'),task('optional','optional',0,false)]);const started=await publish(f,'draft',draftVersion,0,true);expect(f.session.getDeviceVersion()).toBe(2);
 const current:any=await f.schedule.readSchedule(f.childId),today=current.activationDay;expect(today).toBe(taipeiDay(Date.now()));
 // 移除 bootstrap 的 schedule forwarding 會使此 public caller path 失敗。
 const productionRead:any=await createStartupRead(f);expect(productionRead.control.scheduleVersion).toBe(1);
 const original=await f.schedule.readParentDayTasks(f.childId,formatDay(today));expect(original.N).toBe(3);expect(original.tasks).toHaveLength(4);expect(original.tasks.every(t=>!t.persisted)).toBe(true);
 await expect(f.schedule.startChild(started.e)).resolves.toMatchObject(started.result);
 await stage(f,'next',0,1,{kind:'init'});await stage(f,'next',1,1,{kind:'settings',enabled:true,pointsEnabled:false});await stage(f,'next',2,1,{kind:'putTask',task:task('a','required',2,false)});await publish(f,'next',3,1);
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(today))).tasks.find(t=>t.taskKey==='a')).toMatchObject({points:2,requiresConfirmation:true});
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(today+1))).tasks.find(t=>t.taskKey==='a')).toMatchObject({points:0,requiresConfirmation:false});
 await stage(f,'off',0,2,{kind:'init'});await stage(f,'off',1,2,{kind:'settings',enabled:false,pointsEnabled:false});await publish(f,'off',2,2);
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(today+1))).N).toBe(0);
 await stage(f,'on',0,3,{kind:'init'});await stage(f,'on',1,3,{kind:'settings',enabled:true,pointsEnabled:true});await publish(f,'on',2,3);
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(today+1))).N).toBe(3);expect((await f.schedule.readSchedule(f.childId) as any).activationDay).toBe(today);
 await assertFails(updateDoc(doc(db,`${root}/scheduleRevisions/${(started.e.payload as any).revisionId}`),{pointsEnabled:false}));
 await assertFails(updateDoc(doc(db,`${root}/revisionWindows/${(started.e.payload as any).revisionId}`),{until:today}));
 await stage(f,'deleted',0,4,{kind:'init'});await stage(f,'deleted',1,4,{kind:'removeTask',taskKey:'a'});
 await expect(stage(f,'deleted',2,4,{kind:'putTask',task:task('a')})).rejects.toMatchObject({code:'TASK_KEY_RETIRED'});
 await stage(f,'deleted',2,4,{kind:'putTask',task:task('new-a')});await publish(f,'deleted',3,4);
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(today+1))).tasks.map(t=>t.taskKey)).toContain('new-a');
},60000);
// 保留 production bootstrap 所建的 client；family helper 的可注入 pending 僅用於 I/O 邊界。
async function createStartupRead(f:Awaited<ReturnType<typeof family>>){return f.productionSchedule!.readSchedule(f.childId);}
it('J-TASK-DATE / J-ATOMIC：並行補缺、receipt 重送及 caller 同 transaction port 保留原實例',async()=>{
 const f=await family();const n=await workflow(f,[task('a'),task('b'),task('c')]);const started=await publish(f,'draft',n,0,true);const current:any=await f.schedule.readSchedule(f.childId);const day=current.activationDay;const revisionId=(started.e.payload as any).revisionId;const db=getFirestore(f.device),root=`vnextSparkFamilies/${f.id.familyId}/children/${f.childId}`;
 const e:ScheduleEnvelope={schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:current.familyVersion,childId:f.childId,payload:{day,taskKey:'a',revisionId}};
 const result=await f.schedule.ensureTaskSnapshot(e);const path=`${root}/dailySnapshots/${day}/tasks/a`;const original=(await getDoc(doc(db,path))).data();expect(original?.state.status).toBe('unreported');
 expect(await f.schedule.ensureTaskSnapshot(e)).toEqual(result);expect((await getDoc(doc(db,path))).data()).toEqual(original);
 const state:any=await f.schedule.readSchedule(f.childId);const e2={...e,operationId:crypto.randomUUID(),expectedVersion:state.familyVersion,payload:{...e.payload,taskKey:'b'}};const outcomes=await Promise.allSettled([f.schedule.ensureTaskSnapshot(e2),f.schedule.ensureTaskSnapshot({...e2,operationId:crypto.randomUUID()})]);expect(outcomes.filter(r=>r.status==='fulfilled').length).toBeGreaterThan(0);
 const p=await f.schedule.readParentDayTasks(f.childId,formatDay(day));expect(p.N).toBe(3);expect(p.tasks.filter(t=>t.persisted)).toHaveLength(2);expect(p.tasks.find(t=>t.taskKey==='c')?.status).toBe('unreported');
 await runTransaction(db,async tx=>{const prepared=await prepareFirestoreSnapshot(tx,db,f.id,f.childId,{day,taskKey:'a',revisionId});await tx.get(doc(db,`vnextSparkFamilies/${f.id.familyId}`));expect(prepared.existing).toBe(true);prepared.write();});expect((await getDoc(doc(db,path))).data()).toEqual(original);
 await assertFails(setDoc(doc(db,`${root}/dailySnapshots/${day}/tasks/forged`),{...original,definition:{...original?.definition,task:task('forged')}}));
},60000);
it('J-ATOMIC / J-CLOCK-BUDGET：遺失 publish 回應、重新開啟及錯誤本機時鐘只恢復原操作',async()=>{
 const f=await family();const n=await workflow(f,[task('a')]);const current:any=await f.schedule.readSchedule(f.childId);const e:ScheduleEnvelope={schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:current.familyVersion,childId:f.childId,payload:{draftId:'draft',draftVersion:n,scheduleVersion:0,revisionId:crypto.randomUUID(),deviceVersion:1}};
 const failing=createScheduleClient(f.session,{...f.transport,command:async(...args)=>{await f.transport.command(...args);throw new Error('response lost');}},f.store);
 const real=Date.now.bind(Date);const clock=vi.spyOn(Date,'now').mockImplementation(()=>real()+3600000);try{await expect(failing.startChild(e)).rejects.toThrow();}finally{clock.mockRestore();}
 expect(f.rows.size).toBe(1);const reopened=createScheduleClient(f.session,f.transport,f.store);const recovered=await reopened.resume();expect(recovered[0].receipt.operationId).toBe(e.operationId);expect(f.rows.size).toBe(0);expect((await reopened.readSchedule(f.childId) as any).control.scheduleVersion).toBe(1);
 await expect(reopened.startChild({...e,payload:{...e.payload,revisionId:'different'}})).rejects.toMatchObject({code:'OPERATION_CONFLICT'});
 await expect(reopened.startChild({...e,operationId:crypto.randomUUID()})).rejects.toMatchObject({code:'VERSION_CONFLICT'});
},60000);
it('J-ATOMIC / J-START：整個 workflow 於 stage 回應遺失後恢復原 publish 身分，不提前啟用',async()=>{
 const f=await family();const initial:any=await f.schedule.readSchedule(f.childId);let staged=0;
 const crashed=createScheduleClient(f.session,{...f.transport,command:async(...args)=>{const result=await f.transport.command(...args);if(args[1]==='stageWorkflow'&&++staged===2)throw new Error('stage response lost');return result;}},f.store);
 const operationId=crypto.randomUUID();const input={operationId,childId:f.childId,expectedVersion:initial.familyVersion,scheduleVersion:0,deviceVersion:1,draftId:'whole',revisionId:'wholeRevision',start:true,workflow:{tasks:{a:task('a'),b:task('b')},retired:{},enabled:true,pointsEnabled:true}};
 await expect(crashed.saveWorkflow(input)).rejects.toThrow();expect((await f.schedule.readSchedule(f.childId) as any).activationDay).toBeUndefined();
 const original=[...f.rows.values()][0];expect(original.envelope.operationId).toBe(operationId);expect(original.steps?.length).toBe(4);
 const restored=await createScheduleClient(f.session,f.transport,f.store).resume();expect(restored[0].receipt.operationId).toBe(operationId);expect(f.rows.size).toBe(0);
 const current:any=await f.schedule.readSchedule(f.childId);expect(current.control.scheduleVersion).toBe(1);expect((await f.schedule.readParentDayTasks(f.childId,formatDay(current.activationDay))).N).toBe(2);
},60000);
async function seedStarted(f:Awaited<ReturnType<typeof family>>,day:number,tasks:TaskDefinition[]){
 await env.withSecurityRulesDisabled(async context=>{const db=context.firestore() as unknown as ReturnType<typeof getFirestore>;const root=`vnextSparkFamilies/${f.id.familyId}`,child=`${root}/children/${f.childId}`;
  await updateDoc(doc(db,root),{activationDay:day,activatedAt:new Date(day*86400000)});await updateDoc(doc(db,`vnextSparkDevices/${f.id.deviceUid}`),{childIds:[f.childId]});
  await setDoc(doc(db,`${child}/activation/current`),{schemaVersion:1,day,activatedAt:new Date(day*86400000)});
  await setDoc(doc(db,`${child}/scheduleControl/current`),{schemaVersion:1,scheduleVersion:1,revisionId:'historical'});
  await setDoc(doc(db,`${child}/scheduleRevisions/historical`),{schemaVersion:1,tasks:Object.fromEntries(tasks.map(t=>[t.taskKey,t])),retired:{},enabled:true,pointsEnabled:true,saveSequence:1,effectiveFrom:day,savedAt:new Date(day*86400000),draftId:'originalDraft',draftVersion:tasks.length+1});
  await setDoc(doc(db,`${child}/revisionWindows/historical`),{schemaVersion:1,from:day,until:null});
 });
}
it('J-TASK-DATE：未開歷史3件→改排程→公開歷史查詢→補缺仍保留原N及原值',async()=>{
 const f=await family(),historical=taipeiDay(Date.now())-8;await seedStarted(f,historical,[task('a'),task('b'),task('c')]);const before=await f.schedule.readParentDayTasks(f.childId,formatDay(historical));expect(before.N).toBe(3);expect(before.tasks.every(t=>!t.persisted)).toBe(true);
 const current:any=await f.schedule.readSchedule(f.childId);await f.schedule.saveWorkflow({operationId:crypto.randomUUID(),childId:f.childId,expectedVersion:current.familyVersion,scheduleVersion:1,deviceVersion:1,draftId:'changedAfterUnopenedDay',revisionId:'newSchedule',start:false,workflow:{tasks:{a:task('a','required',9,false),b:task('b'),new:task('new')},retired:{},enabled:true,pointsEnabled:false}});
 const derived=await f.schedule.readParentDayTasks(f.childId,formatDay(historical));expect(derived).toEqual(before);
 for(const t of derived.tasks){const state:any=await f.schedule.readSchedule(f.childId);await f.schedule.ensureTaskSnapshot({schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:state.familyVersion,childId:f.childId,payload:{day:historical,taskKey:t.taskKey,revisionId:t.revisionId}});}
 const persisted=await f.schedule.readParentDayTasks(f.childId,formatDay(historical));expect(persisted.N).toBe(3);expect(persisted.tasks.map(t=>t.taskKey)).toEqual(['a','b','c']);expect(persisted.tasks.every(t=>t.persisted&&t.status==='unreported'&&t.points===2&&t.requiresConfirmation)).toBe(true);
},60000);
it('J-START：第二孩較晚開始與競爭保持家庭／個別起點及其他孩子授權，只有一份成功結果',async()=>{
 const f=await family();const firstId=f.childId;const second=await f.client.command('addChild',{operationId:crypto.randomUUID(),expectedVersion:(await f.client.readFamily()).familyVersion,payload:{displayName:'第二孩'}});const oldDay=taipeiDay(Date.now())-7;await seedStarted(f,oldDay,[task('first')]);
 f.childId=second.receipt.resourceId;const n=await workflow(f,[task('second')],'secondDraft');const current:any=await f.schedule.readSchedule(f.childId);const e:ScheduleEnvelope={schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:current.familyVersion,childId:f.childId,payload:{draftId:'secondDraft',draftVersion:n,scheduleVersion:0,revisionId:'competingA',deviceVersion:1}};const other={...e,operationId:crypto.randomUUID(),payload:{...e.payload,revisionId:'competingB'}};
 const results=await Promise.allSettled([f.schedule.startChild(e),f.schedule.startChild(other)]);expect(results.filter(r=>r.status==='fulfilled')).toHaveLength(1);
 const db=getFirestore(f.device),root=`vnextSparkFamilies/${f.id.familyId}`;expect((await getDoc(doc(db,root))).data()?.activationDay).toBe(oldDay);expect((await f.schedule.readSchedule(firstId) as any).activationDay).toBe(oldDay);expect((await f.schedule.readSchedule(f.childId) as any).activationDay).toBe(taipeiDay(Date.now()));expect((await getDoc(doc(db,`vnextSparkDevices/${f.id.deviceUid}`))).data()?.childIds).toEqual([firstId,f.childId]);
 for(let i=0;i<2;i++){const input=i===0?e:other;expect((await getDoc(doc(db,`${root}/receipts/${f.id.adultUid}/commands/startChild/operations/${input.operationId}`))).exists()).toBe(results[i].status==='fulfilled');expect((await getDoc(doc(db,`${root}/children/${f.childId}/scheduleRevisions/${(input.payload as any).revisionId}`))).exists()).toBe(results[i].status==='fulfilled');}
},60000);
it('J-REVISION / U-SCHEMA：未來指定日期 staging→發布僅在該日命中，不提前凍結快照',async()=>{
 const f=await family(),tomorrow=taipeiDay(Date.now())+1;const current:any=await f.schedule.readSchedule(f.childId);const once={...task('once'),weekdays:[],dates:{[tomorrow]:true as const}};
 await f.schedule.saveWorkflow({operationId:crypto.randomUUID(),childId:f.childId,expectedVersion:current.familyVersion,scheduleVersion:0,deviceVersion:1,draftId:'dates',revisionId:'dateRevision',start:true,workflow:{tasks:{once},retired:{},enabled:true,pointsEnabled:true}});
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(tomorrow-1))).N).toBe(0);expect((await f.schedule.readParentDayTasks(f.childId,formatDay(tomorrow))).N).toBe(1);expect((await f.schedule.readParentDayTasks(f.childId,formatDay(tomorrow+1))).N).toBe(0);
 const latest:any=await f.schedule.readSchedule(f.childId);await expect(f.schedule.ensureTaskSnapshot({schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:latest.familyVersion,childId:f.childId,payload:{day:tomorrow,taskKey:'once',revisionId:'dateRevision'}})).rejects.toMatchObject({code:'AUTHORITY_DENIED'});
},60000);

it('J-COMPAT / J-ATOMIC：共用初始化與排程開始共用版本且保留各自 receipt／狀態',async()=>{
 const f=await family();const familyBefore=await f.client.readFamily();
 const envelope={apiVersion:2 as const,envelopeSchemaVersion:1 as const,command:'initializeChildState' as const,familyId:f.id.familyId,operationId:crypto.randomUUID(),expectedVersion:familyBefore.familyVersion,payload:{childId:f.childId}};
 await f.client.command('authorizeChildren',{operationId:crypto.randomUUID(),expectedVersion:familyBefore.familyVersion,payload:{deviceSessionId:f.id.deviceUid,deviceVersion:1,childIds:[f.childId]}});
 envelope.expectedVersion=(await f.client.readFamily()).familyVersion;
 const initialized=await f.common.execute(envelope);expect(initialized.status).toBe('committed');
 const before:any=await f.schedule.readSchedule(f.childId);expect(before.activationDay).toBeUndefined();
 const stateBefore=await f.common.query('parent',f.childId);
 await f.client.children();
 const version=await workflow(f,[task('coexist')],'coexist');await publish(f,'coexist',version,0,true);
 const started:any=await f.productionSchedule!.readSchedule(f.childId);expect(started.activationDay).toBe(taipeiDay(Date.now()));
 expect(await f.common.readReceipt(envelope)).toEqual(initialized);
 const stateAfter=await f.common.query('parent',f.childId);
 if(stateBefore.kind!=='parent-state'||stateAfter.kind!=='parent-state')throw new Error('PARENT_PROJECTION_EXPECTED');
 expect(stateAfter.snapshot).toEqual(stateBefore.snapshot);expect(stateAfter.ledger).toEqual(stateBefore.ledger);
 expect(stateAfter.familyVersion).toBeGreaterThan(stateBefore.familyVersion);
 expect((await f.schedule.readParentDayTasks(f.childId,formatDay(started.activationDay))).N).toBe(1);
});
it('J-START / J-SETTINGS / U-SCHEMA-COMPAT：v2 開始保存當日一次性圖示與文案，固定編輯不回寫原日',async()=>{
 const f=await family(),day=taipeiDay(Date.now());const initial:any=await f.schedule.readSchedule(f.childId);
 const once={...task('once'),icon:'🪥',weekdays:[],dates:{[day]:true as const}};
 await f.schedule.saveWorkflow({schemaVersion:2,operationId:crypto.randomUUID(),childId:f.childId,expectedVersion:initial.familyVersion,scheduleVersion:0,deviceVersion:1,draftId:'v2start',revisionId:'v2original',start:true,workflow:{tasks:{once,weekly:{...task('weekly'),icon:'🧸',order:1}},retired:{},enabled:true,pointsEnabled:false,completionCopy:'我們完成了'}});
 const started:any=await f.schedule.readSchedule(f.childId);expect(started.revision).toMatchObject({schemaVersion:2,completionCopy:'我們完成了',pointsEnabled:false});
 await f.schedule.ensureTaskSnapshot({schemaVersion:2,operationId:crypto.randomUUID(),expectedVersion:started.familyVersion,childId:f.childId,payload:{day,taskKey:'once',revisionId:'v2original'}});
 const before=await f.schedule.readParentDayTasks(f.childId,formatDay(day));expect(before.tasks[0]).toMatchObject({icon:'🪥',points:0,requiresConfirmation:true});
 const next:any=await f.schedule.readSchedule(f.childId);await f.schedule.saveWorkflow({schemaVersion:2,operationId:crypto.randomUUID(),childId:f.childId,expectedVersion:next.familyVersion,scheduleVersion:1,deviceVersion:f.session.getDeviceVersion()!,draftId:'v2edit',revisionId:'v2tomorrow',start:false,workflow:{...next.revision,tasks:{once:{...once,icon:'⭐'},weekly:{...task('weekly'),icon:'⭐',order:1}},completionCopy:'新文案'}});
 expect(await f.schedule.readParentDayTasks(f.childId,formatDay(day))).toEqual(before);expect((await f.schedule.readParentDayTasks(f.childId,formatDay(day+1))).tasks.find(t=>t.taskKey==='weekly')?.icon).toBe('⭐');
 const db=getFirestore(f.device),root=`vnextSparkFamilies/${f.id.familyId}/children/${f.childId}`;
 expect((await getDoc(doc(db,`${root}/dailySnapshots/${day}/tasks/once`))).data()?.definition).toMatchObject({schemaVersion:2,completionCopy:'我們完成了',source:{kind:'schedule',revisionId:'v2original'}});
},60000);
it('J-FAMILY：家長受驗補齊未授權孩的 v1 日投影，不把已回報當未回報，child 通道仍拒絕',async()=>{
 const f=await family(),day=taipeiDay(Date.now());const n=await workflow(f,[task('a')]);await publish(f,'draft',n,0,true);
 const current:any=await f.schedule.readSchedule(f.childId);await f.schedule.ensureTaskSnapshot({schemaVersion:1,operationId:crypto.randomUUID(),expectedVersion:current.familyVersion,childId:f.childId,payload:{day,taskKey:'a',revisionId:current.control.revisionId}});
 await env.withSecurityRulesDisabled(async context=>{const db=context.firestore(),root=`vnextSparkFamilies/${f.id.familyId}/children/${f.childId}`;await updateDoc(doc(db,`${root}/dailySnapshots/${day}/tasks/a`),{state:{status:'reported'}});await deleteDoc(doc(db,`${root}/parentDayViews/${day}/tasks/a`));await updateDoc(doc(db,`vnextSparkDevices/${f.id.deviceUid}`),{childIds:[]});});
 const p=await f.schedule.readParentDayTasks(f.childId,formatDay(day));expect(p.tasks[0]).toMatchObject({status:'reported',persisted:true});
 const db=getFirestore(f.device),root=`vnextSparkFamilies/${f.id.familyId}/children/${f.childId}`;
 expect((await getDoc(doc(db,`${root}/parentDayViews/${day}/tasks/a`))).data()?.state.status).toBe('reported');await assertFails(getDoc(doc(db,`${root}/dailySnapshots/${day}/tasks/a`)));
 await assertFails(setDoc(doc(db,`${root}/parentDayViews/${day}/tasks/a`),{schemaVersion:1,definition:{},state:{status:'confirmed'}}));
},60000);
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
 const race=await Promise.allSettled([f.schedule.appendTodayTask(make(opA)),f.schedule.appendTodayTask(make(opB))]);expect(race.filter(r=>r.status==='fulfilled')).toHaveLength(1);const afterRace=await f.schedule.readParentDayTasks(f.childId,formatDay(day));expect(afterRace.N).toBe(currentDay.N);expect(afterRace.tasks.length).toBe(currentDay.tasks.length+1);
 state=await f.schedule.readSchedule(f.childId);const retryOp=crypto.randomUUID(),retryEnvelope={...make(retryOp),expectedVersion:state.familyVersion,payload:{...make(retryOp).payload,dayVersion:afterRace.dayVersion!}};const recovery=memoryStore();const lost=createScheduleClient(f.session,{...f.transport,command:async(...args)=>{await f.transport.command(...args);throw new Error('append response lost');}},recovery.store);await expect(lost.appendTodayTask(retryEnvelope)).rejects.toThrow();expect(recovery.rows.size).toBe(1);const restored=await createScheduleClient(f.session,f.transport,recovery.store).resume();expect(restored[0].receipt.operationId).toBe(retryOp);expect((await f.schedule.readParentDayTasks(f.childId,formatDay(day))).tasks.filter(t=>t.taskKey==='adhoc_'+retryOp)).toHaveLength(1);

},60000);
it('J-ATOMIC-CONSUMER：canonical 建立漏掉 companion 必須整筆拒絕，不留下 receipt 或 snapshot',async()=>{
 const f=await family(),day=taipeiDay(Date.now()),n=await workflow(f,[task('only')]),started=await publish(f,'draft',n,0,true),revisionId=(started.e.payload as any).revisionId;
 const state:any=await f.schedule.readSchedule(f.childId),db=getFirestore(f.device),root=`vnextSparkFamilies/${f.id.familyId}`,child=`${root}/children/${f.childId}`,operationId=crypto.randomUUID();
 const payload={day,dateKey:String(day),taskKey:'only',revisionId},result={schemaVersion:1,operationId,actorUid:f.id.adultUid,familyId:f.id.familyId,command:'ensureTaskSnapshot',resourceId:f.childId,version:state.familyVersion+1};
 await assertFails(runTransaction(db,async tx=>{tx.update(doc(db,root),{familyVersion:state.familyVersion+1,lastCommand:{actorUid:f.id.adultUid,deviceUid:f.id.deviceUid,command:'ensureTaskSnapshot',operationId,resourceId:f.childId,expectedVersion:state.familyVersion,payload}});tx.set(doc(db,`${root}/receipts/${f.id.adultUid}/commands/ensureTaskSnapshot/operations/${operationId}`),{payload,expectedVersion:state.familyVersion,deviceUid:f.id.deviceUid,result});tx.set(doc(db,`${child}/scheduleEvents/${operationId}`),{schemaVersion:1,command:'ensureTaskSnapshot',operationId,actorUid:f.id.adultUid,deviceUid:f.id.deviceUid,eventAt:serverTimestamp()});tx.set(doc(db,`${child}/dailySnapshots/${day}/tasks/only`),{schemaVersion:1,definition:{schemaVersion:1,familyId:f.id.familyId,childId:f.childId,day,taskDate:formatDay(day),revisionId,task:task('only')},state:{status:'unreported'},materializedAt:serverTimestamp()});}));
 expect((await getDoc(doc(db,`${child}/dailySnapshots/${day}/tasks/only`))).exists()).toBe(false);expect((await getDoc(doc(db,`${root}/receipts/${f.id.adultUid}/commands/ensureTaskSnapshot/operations/${operationId}`))).exists()).toBe(false);
},60000);

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
const backendKey='browser-task-fixture:backend',stored=JSON.parse(localStorage.getItem(backendKey)??'null');
const states=new Map<string,any>(stored?.states??[]),receipts=new Map<string,any>(stored?.receipts??[]);
const commands:any[]=stored?.commands??[];let version=stored?.version??1,fail=false,loseResponse=false;
const persist=()=>localStorage.setItem(backendKey,JSON.stringify({states:[...states],receipts:[...receipts],version,commands}));
const state=(child:string)=>{if(!states.has(child))states.set(child,{workflow:{tasks:{},retired:{},enabled:true,pointsEnabled:false,completionCopy:''},scheduleVersion:0,dayVersion:0,orders:{},appended:[],orderEpoch:null});return states.get(child);};
function fixtureError(code:string):never{throw Object.assign(new Error(code),{code});}
const transport:ScheduleTransport={
 namespace:'browser-task-fixture',familyDay:async()=>taipeiDay(Date.now()),
 readSchedule:async(_i,child)=>{const s=state(child);return {familyVersion:version,today:taipeiDay(Date.now()),control:{scheduleVersion:s.scheduleVersion},revision:s.revision,activationDay:s.activationDay};},
 readDay:async(_i,child,day)=>project(child,day),readParentDay:async(_i,child,day)=>({projection:project(child,day),missing:[]}),
 receipt:async(_i,cmd,e)=>receipts.get(cmd+e.operationId),
 command:async(i,cmd,e)=>{
  if(fail)fixtureError('SAVE_UNKNOWN');if(e.expectedVersion!==version)fixtureError('VERSION_CONFLICT');
  const s=state(e.childId),p:any=e.payload;
  if(['appendTodayTask','moveTodayTask','resetTodayOrder','stageTodayOrder','publishTodayOrder'].includes(cmd)){
   if(p.day!==taipeiDay(Date.now()))fixtureError('DAY_EXPIRED');
   if(p.dayVersion!==s.dayVersion||p.revisionId!==s.originalId)fixtureError('VERSION_CONFLICT');
  }
  switch(cmd){
   case 'stageWorkflow':
    if(p.kind==='init')s.draft=structuredClone(s.workflow);
    else if(p.kind==='putTask')s.draft.tasks[p.task.taskKey]=p.task;
    else if(p.kind==='removeTask'){delete s.draft.tasks[p.taskKey];s.draft.retired[p.taskKey]=true;}
    else if(p.kind==='setDate'){if(p.include)s.draft.tasks[p.taskKey].dates[p.dateKey]=true;else delete s.draft.tasks[p.taskKey].dates[p.dateKey];}
    else if(p.kind==='settings')Object.assign(s.draft,{enabled:p.enabled,pointsEnabled:p.pointsEnabled,completionCopy:p.completionCopy});
    else fixtureError('SCHEMA_INVALID');break;
   case 'startChild':case 'saveSchedule':
    s.workflow=structuredClone(s.draft);s.scheduleVersion++;
    s.revision={schemaVersion:2,...s.workflow,saveSequence:s.scheduleVersion,effectiveFrom:taipeiDay(Date.now())+(cmd==='startChild'?0:1),savedAt:0,draftId:p.draftId,draftVersion:p.draftVersion};
    if(cmd==='startChild'){s.activationDay=taipeiDay(Date.now());s.original=structuredClone(s.revision);s.originalId=p.revisionId;}break;
   case 'appendTodayTask':
    s.appended.push({schemaVersion:2,definition:{schemaVersion:2,familyId:i.familyId,childId:e.childId,day:p.day,taskDate:p.taskDate,revisionId:p.revisionId,source:{kind:'adhoc',operationId:e.operationId,baseRevisionId:p.revisionId},completionCopy:s.original.completionCopy,task:structuredClone(p.task)},state:{status:'unreported'},materializedAt:0});s.dayVersion++;break;
   case 'moveTodayTask':{
    const task=project(e.childId,p.day).tasks.find(t=>t.taskKey===p.taskKey);if(!task)fixtureError('TASK_NOT_FOUND');
    s.orders[p.taskKey]={taskKey:p.taskKey,requirementType:task.requirementType,rank:p.rank};s.dayVersion++;break;
   }
   case 'resetTodayOrder':s.orders={};s.orderEpoch=null;s.dayVersion++;break;
   case 'stageTodayOrder':
    if(p.kind==='init')s.orderDraft={id:p.draftId,version:0,entries:{}};
    if(!s.orderDraft||s.orderDraft.id!==p.draftId||s.orderDraft.version!==p.draftVersion)fixtureError('VERSION_CONFLICT');
    if(p.kind==='put')s.orderDraft.entries[p.taskKey]={taskKey:p.taskKey,requirementType:p.requirementType,rank:p.rank};s.orderDraft.version++;break;
   case 'publishTodayOrder':
    if(!s.orderDraft||s.orderDraft.id!==p.draftId||s.orderDraft.version!==p.draftVersion)fixtureError('VERSION_CONFLICT');
    s.orders=structuredClone(s.orderDraft.entries);s.orderEpoch=p.draftId;s.dayVersion++;break;
   // 正式 consumer 尚缺；其他命令不能由 fixture 假回成功。
   default:fixtureError('FIXTURE_COMMAND_UNSUPPORTED');
  }
  const receipt={schemaVersion:1 as const,operationId:e.operationId,actorUid:i.adultUid,familyId:i.familyId,command:cmd,resourceId:e.childId,version:++version};
  const result={receipt};receipts.set(cmd+e.operationId,result);commands.push({command:cmd,operationId:e.operationId,childId:e.childId});persist();
  if(loseResponse)fixtureError('SAVE_UNKNOWN');return result;
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

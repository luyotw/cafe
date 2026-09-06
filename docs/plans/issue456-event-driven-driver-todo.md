# Issue #456：Event-Driven Driver TODO

規格來源：[GitHub Issue #456](https://github.com/luyotw/cafe/issues/456)

## 共識定義

- `attached`：foreground continuous workflow；driver在前景依poll interval監看，持有foreground process handle，並使用既有能力處理異常。
- `unattended`：background continuous workflow；不主動喚醒driver，因此缺少異常發生當下的即時觀測與反應時機。這不代表之後人工inspect時沒有任何process control能力。
- `event-driven`：沿用`unattended`的background continuous workflow，只在phase terminal或重要事件後以non-blocking callback喚醒driver；driver醒來後沿用`attached`既有的檢查、診斷與處理規則。
- 三種mode都不因本issue改用`--single-step`；`event-driven`不等待driver，callback/driver失敗也不阻擋workflow。
- callback只補上即時資訊，不會讓driver自動own background process。使用者授權、目標識別與技術控制管道是三件不同的事；只有現有環境已提供可靠且獲授權的控制方式時，driver才可用它停止process。
- 本issue不新增或保證background stop能力。沒有可靠既有控制方式時，event-driven driver只能即時inspect/diagnose，並等待worker自然停止後再執行需要idle state的處理。
- Driver Mode、per-issue driver config/session與prompt只存在於`src/cafe/data/skills/use-cafe-workflow/**`；CAFE core只認識mode-neutral background hosting、asynchronous workflow event callback與session continuation。

## TODO 1：更新skill-owned mode contract

- [ ] 將Issue #456 body、`use-cafe-workflow`的`SKILL.md`、kickoff、running workflow、handoff references與CLI說明統一為`attached|unattended|event-driven`，移除`delegated`、`supervised`、`checkpointed`、single-step gate與driver `ok` advancement語意。
- [ ] 更新`format_kickoff_contract.py`：
  - `attached`只接受正整數`poll_interval_seconds`；
  - `unattended`不接受mode-specific欄位；
  - `event-driven`必須指定支援的CLI與exact model。
- [ ] event-driven每個issue使用獨立的skill-owned資料：

  ```text
  .cafe/issues/<issue>/driver/
  ├── config.yaml      # schema_version, mode, cli, model
  ├── session.json     # schema_version, workflow_id, cli, model, session_id
  └── session.lock
  ```

- [ ] 不新增`allowed_actions`或第二套authority。driver繼續遵守既有`confirmation_contract`、mandatory HumanTask、`reactive_user_handoffs`、strategic mandate與`model_adjustment.authority`。

## TODO 2：讓既有background hosting保持mode-neutral

- [ ] 將現有unattended所需的background launch、ownership lock、launch validation與worker status從`DriverPolicy`、`DriverCoordinator`及`BlackboardState.driver_state`解耦；沿用`.workflow-advancement.lock`與generic `WorkerLaunchStore`，不另建hosting subsystem。
- [ ] generic background invocation增加optional opaque builtin event callback ID；event-driven重用同一條background路徑，attached/unattended不傳callback。
- [ ] callback ID只以既有builtin catalog/package resolver驗證origin與script containment；拒絕project/global override、path、argv、shell fragment與environment override。
- [ ] 不新增hook snapshot、digest、binding lifecycle state、safe-stop API、PID registry、cooperative cancellation或worker lifecycle framework；也不把任何既有外部/manual process control誤寫成event-driven保證。

## TODO 3：加入最小的mode-neutral asynchronous workflow event callback

- [ ] 在既有phase terminal success/failure、HumanTask或permission materialization、workflow interruption與workflow completion完成durable commit後，best-effort啟動builtin event callback。
- [ ] 同一個durable boundary只dispatch一次；phase terminal若同時materialize HumanTask/permission，放進同一份wake payload，不再額外喚醒一次。這是單次dispatch，不建立dedupe state。
- [ ] payload只包含必要identity與wake reason：workflow/issue、event kind、step、attempt/iteration、terminal status，以及存在時的task/permission ID；不夾帶blackboard、artifacts、phase config或專用summary。
- [ ] callback以detached subprocess、`start_new_session=True`、`close_fds=True`執行，不繼承workflow ownership lock，不wait、不retry、不queue、不replay；workflow立刻繼續。
- [ ] callback spawn失敗只寫bounded generic diagnostic，不改workflow state。

## TODO 4：實作skill-owned callback與exact driver session

- [ ] builtin callback取得該issue的`session.lock`，核對workflow ID及config中的CLI/model；第一次成功callback acquire一次driver session，之後使用既有`SessionContinuation.resume_exact`恢復同一session。
- [ ] 修正現有mode-neutral exact-resume invariant：session conflict或CLI/model/session mismatch只讓該次callback失敗，不得清除session後重建，也不得fallback；不重做五套transport。
- [ ] callback只傳compact wake notice，要求driver依attached既有流程重讀`cafe status`、`cafe show`、目前handoff、pending HumanTask/permission與最新phase result/error；不建立evidence aggregation或summary schema。
- [ ] driver沿用attached既有的bounded diagnosis、retry/resume、future phase model adjustment、HumanTask/permission、mandate與已存在的process control規則；callback本身不增加authority或process ownership。
- [ ] driver採取process action前必須重讀最新state並確認目標仍是該issue的current active process；只有現有環境提供可靠且獲授權的控制方式時才可停止。資訊過期、目標不明或沒有可靠控制方式時，只inspect/diagnose。
- [ ] retry、resume或phase config mutation必須等worker已停止並確認ownership釋放後再執行；callback到達不代表phase之間存在可安全插入mutation的空窗。
- [ ] driver不得代答mandatory HumanTask、憑空授權permission/capability或超出既有authority；不新增action proposal/executor、action ID或background stop primitive。
- [ ] callback/driver失敗不遞迴喚醒driver，也不建立review/recovery/action state。

## TODO 5：切換routing並移除skill外Driver Mode依賴

- [ ] `attached`維持foreground continuous +既有poll cadence，不註冊callback。
- [ ] `unattended`維持background continuous，不註冊callback。
- [ ] `event-driven`維持background continuous，只多傳builtin callback ID；不使用`--single-step`。
- [ ] 不為workflow start/resume另送callback；kickoff/initiating driver已在場，event-driven driver只由之後真正的phase terminal或重要事件喚醒。
- [ ] HumanTask/permission沿用現有skill對話與既有completion/resume流程，不在使用者完成task後再額外送一個自我callback。
- [ ] additive replacement測試通過後原子切換routing，再刪除舊delegated policy/controller/runtime/transport、prepare/update-driver-policy flags、driver state/status/show/notification projection與相關imports/tests。
- [ ] inventory與prepare clobber protection若仍有caller，移到既有generic prepare owner；不為它建立新模組或subsystem。
- [ ] 保留generic background hosting、agent adapters、session continuation、`human_task_notifications.py`與capability receipts。

## TODO 6：最小驗收

- [ ] mode routing：attached與unattended行為不變；event-driven是background continuous且只多callback，不使用single-step。
- [ ] durable boundary先commit再喚醒；slow、失敗或重疊callback不阻塞workflow，同一issue callback由`session.lock`序列化。
- [ ] 同一phase terminal + task/permission只喚醒一次，且不需要cursor、dedupe或replay state。
- [ ] builtin callback ID驗證拒絕override、path/argv/shell/env注入。
- [ ] exact session第一次acquire後只resume；conflict/mismatch不得silent replacement或fallback。只做共用regression，不新增五CLI完整矩陣。
- [ ] callback喚醒的driver走既有status/show/diagnosis與authority規則；驗證「callback不提供process ownership」，沒有可靠既有控制方式時只診斷，worker停止且ownership釋放後才可retry/resume/修改phase config。
- [ ] 若測試環境已有可靠、獲授權的process control，驗證driver只能在重讀current target後沿用它；這不新增stop API或worker-control測試矩陣。
- [ ] source-boundary audit確認`use-cafe-workflow`以外的production code沒有Driver Mode schema、session、routing、prompt或authority依賴。
- [ ] 執行targeted tests與repository既有quality gate，確認舊delegated code與projection已無production caller後再移除。

## 明確不做

- background single-step、phase-by-phase chaining或driver advancement gate。
- workflow等待driver或同步completion hook。
- stable event ID、cursor、catch-up、dedupe、out-of-order handling或historical replay。
- durable callback outbox、retry、queue、scheduler或watchdog。
- hook snapshot、digest或binding lifecycle state。
- evidence aggregation、driver專用summary schema或額外start/resume callback。
- per-action authority、structured action executor、action receipt或global CAS framework。
- 新的safe-stop API、PID registry、cooperative cancellation或worker lifecycle/control framework；不宣稱callback或使用者授權本身會產生process ownership。
- 五套driver transport複製或五CLI完整workflow測試矩陣。

## #474 durable contract refinement

The #474 contract application is intentionally a small `src/cafe/driver/**`
periphery, not a core Driver Mode subsystem. Its only production caller is the
`use-cafe-workflow` skill, which retains preflight, mode routing, session,
callback, prompt, and process ownership. The package owns only the complete
issue-scoped kickoff contract lifecycle and its bounded projections.

Every generic phase, `cafe-pr`, trusted host capability path, core runtime, UI,
and agent remains Driver-free. They continue to consume the existing generic
publication input and capability contract, so a workflow with no Driver
directory has the same local-only or verified-PR outcome as a Driver-projected
workflow. This preserves the mode-neutral and trusted-boundary principles above.

# #67 第一階段基線：獨立 QA 驗收

結論：**第一階段適用驗收 PASS，沒有需退回 Develop 的修正。** 本輪實際執行單元／UI 35 案、真 Auth／Firestore SDK／Rules 21 案、Chrome 原 runner 19 筆檢查、額外 QA runner 6 筆檢查及獨立 IndexedDB／可讀性／公開契約矩陣探測；全部通過。這是基線 QA，不代表 #67 全量產品驗收、已 merge／部署或可關票。

驗收日期：2026-10-12（Asia/Taipei）；HEAD `c9d5786126dba2a0c8a1ce22c7dd8e9538489771`。本輪只寫 qa/iteration_001 的文件、runner、結果及截圖，沒有修改產品程式或既有測試。

## Execution Plan

1. 依 bounded digest 確認 develop→qa baton；完整讀 role、develop v3、workspace v1、plan v4，使用 plan 的 Requirements Specification 與第一階段 Test List 適用表。舊送審文件的「未確認」歷史措辭由 runtime 已完成的 output-review 交接取代；同 HEAD 實際產品已存在，不再執行規格確認或重做 Develop。
2. 比較 Git HEAD、base→head changed-file set 與 workspace；逐項重算 plan checkpoint 的 73 個來源 hash。以精確版本接受開發摘要為當前輸入。
3. 獨立重跑指定 public client／domain／UI 案例、真 SDK／Rules emulator；使用真 Chrome／IndexedDB 探測保存、恢復及 UI 方法，補驗零任務、選做手選、CRUD、取消、原日圖示與未知開始。每段工作有 stdout／可讀 durable log，均在 idle 門檻內結束。
4. 對照需求逐項記錄範圍、PASS／延期未驗；檢查截圖可讀性，停止本輪 server、收 explicit terminal 與 exit_code，重驗 HEAD／clean status、來源及 Todo evidence，完成 checklist 與 ordinary await_agent baton。

| Runtime ID | 採取動作與完成證據 |
| --- | --- |
| agent.stdout-idle | 有界讀取／測試及進度更新；SDK 約40秒、browser約數秒。有可讀 logs 與 stdout 摘要，沒有不可見長時間 worker。 |
| agent.post-output-exit | 測試及 browser 收 exit0；Chrome context／browser 顯式 close；Vite 刻意 Ctrl-C exit130，六個相關 port 已閉合。 |
| agent.structured-completion | 35／21 suite summary、browser COMPLETED、workspace assertion、report／checklist／baton 分別取證；exit 不單獨當驗收完成。 |
| execution.primary-only-chain | 保持當前工具／session，不增 backup 或調整 chain。 |
| human-task.user-authority | 只完成既有 QA；無外部 mutation、merge／deploy／close／cleanup 或替使用者確認交付契約。 |
| agent.streaming-error-retention | 未讀 streaming.jsonl；以本輪 durable logs 與已提供 artifacts 留證，不嘗試恢復被截斷記錄或改 sanitization。 |

## 輸入與版本核對

- plan：`.cafe/issues/issue67/spec_plan/iteration_004/output.md`，完整 SHA256 `1fdffe71b444d98daf13bd7c0e3f77eab66ad008a1078836b4dab9cc5c0bb03c`。
- develop：`.cafe/issues/issue67/develop/iteration_005/output.md`；workspace：同目錄 `workspace.json`。兩份 full input 已讀，沒有以 packet 或較舊 report 取代。
- workspace base `99dd8c132835978f18a0c66f9efdea5c1ed742cb`；實際 HEAD 與 workspace head 精確相同；25 個 changed files 的 status／path／順序全部吻合。開始與結束 `git status --short` 皆空。
- plan checkpoint 的 73 個檔案 hash 全部相同，包括產品／測試、原 browser evidence、Manager contract 與 delivery pin。具體結果在 `evidence/workspace.json`。
- effective_checklist 原內容空、gates=[]、overlays=false、agent_owned=true；本輪建立具體 QA checklist，沒有刪除、改寫或略過既有 required gate。

## 實際命令與結果

下列均為本輪獨立執行；原 Develop PASS 僅作情境來源，不作本輪通過證據。命令從 repository root 執行。

| 命令／方法 | 本輪結果 | 原始證據 |
| --- | --- | --- |
| `npm run test:vnext -- src/vnext/api/schedule.test.vnext.ts src/vnext/domain/schedule.test.vnext.ts src/vnext/domain/task-settings.test.vnext.ts src/vnext/pages/task-journey.test.vnext.tsx src/vnext/pages/i18n-journey.test.vnext.tsx` | 5 files／35 tests PASS，exit0，Vitest 2.1.9。最初未保存工具終態的啟動不列證據；本表使用隨後完整留 log／exit 的執行。 | `evidence/unit-ui.log` |
| `npm run test:vnext:spark -- functions/test/schedule-journeys.integration.vnext.ts functions/test/spark-rules.integration.vnext.ts` | 2 files／21 tests PASS（schedule12／family Rules9），exit0；Script exited successfully，emulator shutdown 完成。demo-kid-vnext 本機專案；原 Rules 限制保持。 | `evidence/sdk-rules.log` |
| `node_modules/.bin/vite --host 127.0.0.1 --port 5175 --strictPort`；`/usr/local/opt/python@3.10/bin/python3.10 …/evidence/ui-check.py` | 原 runner 19 筆 PASS、pageErrors=[]，新日期／operationId；真 Chrome 154.0.8037.99、headless。只把原 runner 的輸出目錄改至本輪，assertions 保持。 | `evidence/browser-ui.log`、`evidence/ui-run.json`、8 組 settings PNG／2 組 tasks PNG |
| 同 Python 執行 `…/evidence/layout-check.py` | 320px／根字體200%／pseudo，inputWidth=204、fontSize=32px、shortWordLines=1；LAYOUT_WORD_COMPLETED／exit0。 | `evidence/layout.log` |
| 同 Python 對 `functions/test/browser/schedule-pending.html` 執行 page result assertion | 真 IndexedDB v1／day 原bytes重開、installation／namespace、operation CAS／remove PASS；pageErrors=[]，INDEXEDDB_COMPLETED／exit0。 | `evidence/indexeddb.json` |
| 同 Python 執行本輪 `…/evidence/qa-extra.py` | 6 筆 PASS，pageErrors=[]，QA_EXTRA_COMPLETED／exit0；新增 QA 情境不修改產品。 | `evidence/extra.log`、`evidence/extra.json` |

公開契約矩陣補充命令：`node_modules/.bin/vite-node --script .cafe/issues/issue67/qa/iteration_001/evidence/projection-probe.ts`，PROJECTION_PROBE_COMPLETED／exit0。實測必做3/3＋選做0/2達標、reported不計有效完成、追加required→3/4／optional不增N、16種性質／日期／點數／確認組合，以及混合weekdays／dates修改圖示保留原值；結果見 `evidence/projection-probe.log`。初次QA probe相對import少一層而exit1，僅修本輪runner路徑後通過，原失敗log留在 `evidence/projection-probe-path-error.log`；未執行到產品斷言，不是產品缺陷。

本輪未新增 rendering／contract 修改，未重跑 build 或 repository 全套；既有 build 證據不冒稱本輪結果。沒有執行 coverage、release-check、push gate、新依賴下載或正式資料操作。

## 需求與 Test List 對照

「適用 PASS」只代表已確認第一階段的情境；含正式 consumer 的原全旅程保留未驗。

| 需求／Test List | 實際可觀察情境與結果 | 範圍／限制 |
| --- | --- | --- |
| 首次三件開始：U-SETTINGS、J-START | 真 browser 四孩各自開始；睡前／早晨範本、三件可增四件、確認預設on／集點off、原文／圖示／文案及 once 表單成功。額外 custom 零任務可開始、無全完成、無選做區。SDK 驗當日一次性／activation／revision／原receipt／並行只一份成功、stage回應遺失不提前啟用。適用 PASS。 | browser transport 是 fixture；真保存由 SDK／Rules 補證。 |
| 設定／F1圖示：U-SETTINGS、U-SCHEMA-COMPAT、J-SETTINGS | UI／public saveWorkflow 傳原圖示、完成文案；browser 新增／編輯／刪除未來定義、取消不保存、固定圖示修改後今日仍原圖示；既存7點／免確認／集點on／文案不重設。既存once及多日期原值仍可保存；公開probe另驗16種性質／排程／點數／確認組合、既存混合weekdays／dates不變。SDK v2一次性圖示／文案保存、固定次日生效，今日snapshot與歷史原值不回寫。適用 PASS。 | 取消的是編輯草稿；未執行取消已保存今日追加。 |
| 分區／進度：U-REQUIRED、U-DAY-SOURCE、U-NEXT、J-OPTIONAL | domain required／optional、points／confirmation正交；公開probe確認必做3/3＋選做0/2達標、reported不計完成；confirmed fixture只計required／零N無成果、exempt不自定有效完成。UI全部已reported選做仍可查；browser零必做有選做、手選選做不鎖關；無項才隱藏。適用 PASS。 | 3/3等計數屬projection assertions；不是正式回報→批准→入帳產品journey。returned與正式next／feedback未驗。 |
| 角色／家庭：J-FAMILY、U-INTENT-SCOPE | SDK家長讀未列device.childIds的孩，v1缺view有界補齊保留reported；child canonical單筆讀同孩與獨立view偽寫拒絕。family Rules驗第二成人／terminal撤權／跨家庭／能力競爭；UI共用Family session／PIN gate、background鎖定清家長投影。API late response在generation改變後拒絕，換帳原意圖隔離。browser四孩原文不混用。適用 PASS。 | 本機PIN與UI audience不冒稱雲端身份；four-child select_option不算native popup keyboard／scroll。正式帳目／週期矩陣留全量。 |
| 今日追加：U-DAY-SOURCE、J-APPEND | domain固定＋adhoc聯集及base／append N；SDK required追加增N、optional競爭只一份成功不增N、原operation來源／圖示／points／state保留、明天無追加；相同ID回原receipt。browser未知結果未安裝項目，新分頁用原ID／taskDate／payload恢復只一件。適用 PASS。 | 沒有正式已完成3/3→3/4收入journey；當日原definition及N增加已驗。 |
| 今日排序：U-DAY-ORDER、J-DAY-ORDER | domain rank極限／unknown key拒絕；SDK同區move／reset／normalization publish、跨區拒絕、reset含追加且state／points／N保持、隔日預設；Chrome Enter移動／reset確實改呈現。適用 PASS。 | 只證本命令不改帳目；正式ledger consumer未驗。 |
| 未知／恢復：U-INTENT-SCOPE、J-RETRY | API storage fail sends=0，原v1／v2envelope不改；原receipt優先／跨午夜不改taskDate或ID、不同payload CAS拒絕、未知不能discard；UI其他同孩receipt不能完成草稿，四種日命令pending重開、未知reset保持原操作。Chrome真IndexedDB／新分頁恢復、未知開始留草稿且開始disabled，解除fixture故障後原意圖完成。適用 PASS。 | 跨午夜／account／撤權為API＋emulator方法，非實機／OS跨午夜。state command恢復留後續。 |
| schema／來源：U-SCHEMA-COMPAT、U-I18N | 公開decoder接受v1原值／v2completionCopy與template來源，拒絕unknown version、額外reason與非法日期；原pending bytes／圖示保存不改造。i18n UI keys／aria／fallback、參數／原文與文案來源不重翻。適用 PASS。 | 不新增returned假狀態／fake provenance。 |
| 可及性：J-ACCESSIBILITY | Chrome320／768×根字體100／200%×zh-TW／pseudo八組document／body等寬、controls無水平越界／keys不外露；Tab名稱→圖示、Enter保存／取消／排序／reset與heading focus；reduced motion running animations=0，keyboard不受阻。另測最窄pseudo短詞一行；人工檢視本輪settings-320-200-1.png及tasks-320.png可讀。適用 PASS。 | 16→32px文字放大，不是OS／整頁zoom；原生popup、PWA、iOS、VoiceOver與#69正式動畫中斷未驗。 |
| 現版承接：U-LEGACY-MAPPING、J-CARRYOVER | 執行legacyPreview來源fixture：daily→required／bonus→optional、family／child／kind／id隔離、原日期／排序／完成文案、不建立歷史或帳目；公開v1／v2reader情境通過。文件能力缺口／單writer／compatible bundle回退描述吻合當前界線。適用 PASS。 | 未執行正式import、writer切換、cleanup或實際部署回退；取消追加仍未核定。 |
| 同交易port：J-ATOMIC-CONSUMER適用子項 | SDK caller同txprepareSnapshot、existing原定義no-op及adhoc原source通過；canonical create伴隨parent view；漏companion整筆拒絕、snapshot／receipt均不存在；單獨偽view更新拒絕。子項 PASS。 | 完整J未驗：report／manual／returned的state／view／ledger／balance／event／receipt共同交易與收入矩陣尚無正式consumer。 |
| 基線承接：J-BASELINE-HANDOFF目前QA子項 | 精確plan／HEAD／changed-files／73來源hash、基線入口與不可用UI、方法限制、延期008／011及契約pin均核對；本輪有獨立驗收與終態。QA子項 PASS。 | PR、Manager契約重確認、精確Deliver action／merged SHA／CI／測試站及整票closeout尚未發生，不提前判PASS。 |

## 未驗、延期及交付邊界

無本階段 required check 受環境阻擋，無需 clarification／permission。下列是 accepted plan 已明訂後續責任，不是本輪 blocked check，也不因本輪PASS而完成：

- 原 PLAN-008 pending：#68／#70正式report／returned／parent manual client／decoder／Rules、原子帳本，#69成功next／feedback及不重播完整journey。
- 原 PLAN-011 in_progress：完整consumer矩陣、共同state／view／ledger／balance／event／receipt原子保存及方法交接；required／optional、需確認／免確認、有點／零點、固定／追加、衝突重送與returned更正仍需正式來源後驗。
- #77方法分支：native select popup keyboard／scroll、Web/PWA安裝、實機iOS／VoiceOver／OS跨午夜及#69正式動畫中斷；headless或select_option不可代替。
- 未執行legacy正式import／家庭writer切換／資料cleanup、真部署回退；#75／#76原責任、舊入口及原資料保留。

`docs/vnext/task-list.md` 初段parent日讀共用childIds是歷史baseline；後段與snapshots companion文件描述當前port，本輪SDK已證明parent路徑，無將歷史欄誤認未完成。

## 既有後續提案

本issue保留第一階段可用成果：開始／固定設定／原日來源、列表、今日追加及同區排序、保存恢復與正式consumer接入基礎。延續原未採用提案「今日追加取消／移除 — 政策釐清與能力承接」：先調查未回報／已回報／已入帳取消對N、來源與原收入政策，決議後再定實作範圍；不重做新增／排序或#68／#70一般確認／更正。依賴本票追加來源與#66；收入相關依賴#68／#70。本輪沒有新獨立工作提案、沒有開票或外部專案變更；取消缺口仍待決。

## Todo List

No actionable work.

## 終態與交接

所有本輪驗收結果有 suite summary／browser COMPLETED與exit0，Chrome context／browser已close。Vite在驗收完成後Ctrl-C exit130；5175、19099、18080、18150、14400、14450均閉合，詳 `evidence/processes.json`。HEAD／worktree及73來源hash再次核對不變。沒有產品程式修改、commit、hook bypass、GitHub sync／mutation或更動blackboard／Manager契約。

第一階段 QA 可 ordinary `await_agent` 交 PR。PR仍須列第一階段版本、可用／不可用與008／011／取消／#69／#77，使用 `Refs #67`，#67保持OPEN。Manager contract cleanup與delivery pin仍舊值；Deliver外部action前必須循既有HumanTask重確認stage outcome／延後關票cleanup／完整invariants及適用pin，並審精確PR head／當時target SHA／merge方法／Rules＋Hosting部署作用；本輪PASS不授權該動作。

正式來源到位後，先修訂／確認完整整合計畫，以原ID／Work／fingerprint回投008／011再驗。branch、worktree、commit refs、accepted artifacts、原browser PNG／receipt／pending及資料保留。runtime自身驗證後續進入，本phase不宣稱已發PR、merge／deploy或全票workflow_complete。

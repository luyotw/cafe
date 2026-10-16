# #67 Rules renewal 修正：獨立 QA

結論：**本次 Rules renewal／owner transfer 修正獨立 QA PASS，無待修正項目。** 2026-10-12 15:41:10+08:00 起，對 commit `416f6ace985bf95cdccb76a2d7d02b254b71cdad` 實際執行真 Auth／Firestore SDK／Rules 3 files／29 tests，全部通過，exit0。本輪驗收已核定的 expression 修正與必要保存／權威回歸；本機通過尚非 CI／測試站部署成功。

## Execution Plan

1. 完整讀目前role／develop／workspace、direct Work及前QA；plan v4已於前輪完整載入，本輪確認同一完整SHA，再依其Requirements Specification／Test List對照本次修正。核對delta manifest hash與實際Git HEAD／27檔累積changed set。
2. 獨立重跑public SDK transport7、family Rules10、schedule12，實際驗renewal單次版本、scope／receipt／readonly restore、owner transfer、atomic companion deny及原保存回歸，不以Develop green或舊QA替代。
3. 比較前QA逐項來源與新commit；未變UI／domain／Chrome／IndexedDB保留前次本代理實測的方法、日期及版本，Rules相關結果全部用本輪重驗。記錄失敗／未驗，收suite terminal、exit及emulator shutdown。
4. 完成report／Todo／checklist、重驗clean HEAD與immutable來源，再依結果寫baton。禁止修改產品程式、重做已成功merge或沿用舊外部action。

| Runtime ID | 採取動作 |
| --- | --- |
| agent.stdout-idle | 有界檔案核對與targeted套，每段stdout／durable log摘要可觀察；不啟動不可見長worker。 |
| agent.post-output-exit | 收集測試exit_code與emulator shutdown，確認相關port閉合。 |
| agent.structured-completion | suite summary、source核對、checklist與baton分開留證，不用exit或活動單獨當成功。 |
| execution.primary-only-chain | 保持本session／chain與demo本機runner，不新增backup。 |
| human-task.user-authority | 只驗已核定Work；PR／Deliver新action與cleanup仍由原授權route，無外部mutation。 |
| agent.streaming-error-retention | 使用durable logs，不讀streaming.jsonl／全blackboard或恢復被截斷記錄。 |

## Todo List

No actionable work.


## 輸入與修正來源

完整讀 `develop/iteration_007/output.md`（code v4）、`workspace.json`（v2）、本輪role、`qa/iteration_001/output.md`，並逐項重驗前QA結論。plan v4已於前輪完整讀取；本輪同一完整內容 SHA256 `1fdffe71b444d98daf13bd7c0e3f77eab66ad008a1078836b4dab9cc5c0bb03c` 確認不變，使用其 Requirements Specification／J-FAMILY與第一階段適用表。

- 直接修正權威：clarification-feedback HumanTask `f957a851-7391-471d-a693-01f2299a0360`／result `b7e7c4fe-16fb-4db9-9f2d-fec4ce368919`；原持久化答覆完整讀取。最小運算量修正保留原allow／scope／版本／immutable payload／receipt／pairing／companion invariants，無新產品或外部action授權。
- delta manifest各來源bytes／SHA逐項與實檔相符：develop `7d407f98944c7a97d7ec7fd72765c591cd5348d5eec0a7de355d453112d5c001`、workspace `97f9e11b86bb3a1211958a82de7b3aa4c383c48306b0dce9159abadfa39faaa2`、前QA `892765462b5f209df7bc2f8e4906f19acbde629f235dcfafdd393df1b8464ee7`。
- Git HEAD精確等於workspace head；workspace base `99dd8c132835978f18a0c66f9efdea5c1ed742cb` 到head的27檔status／path完全吻合。相對前QA HEAD `c9d5786126dba2a0c8a1ce22c7dd8e9538489771`，僅 `firestore.develop.rules`、`functions/test/firestore-transport.integration.vnext.ts`、`functions/test/spark-rules.integration.vnext.ts` 三檔delta。開始／結束worktree皆clean。
- Rules修正是在receiptMatches重用同path並分流互斥結果、familyUpdate先分流issueDevice／transferOwner；沒有修改正式transport、UI、schema、依賴、額度、產品限制或部署配置。實際行為由下方SDK／Rules命令驗證，不以程式閱讀推定PASS。
- effective_checklist空、gates=[]、overlays=false、agent_owned=true；todo_projections=[]。本輪建立6項QA checklist，無移除required gate、沒有自造PLAN／source identity或改ledger／batch／blackboard。

## 獨立驗收命令與結果

從repository root執行：

```sh
npm run test:vnext:spark -- functions/test/firestore-transport.integration.vnext.ts functions/test/spark-rules.integration.vnext.ts functions/test/schedule-journeys.integration.vnext.ts
```

**3 files／29 tests PASS**（transport7、family Rules10、schedule12），Vitest 2.1.9、demo-kid-vnext本機Auth／Firestore emulator、原 `firestore.develop.rules` 與預設1000-expression／access限制。Duration 49.27s；explicit `Script exited successfully (code 0)`、主行程exit0，Auth／Firestore／hub／logging shutdown完成。完整stdout／stderr保存在 `qa/iteration_002/evidence/authority-atomic.log`，終態及相關ports閉合在 `evidence/completion.json`。

沒有放寬Rules限制；本輪未量測每request精確expression剩餘量，不宣稱特定運算成本或所有資料規模的budget保證。iteration006持久化1000-expression diagnostic只支持當時本機red，未稱該trace是CI直接取得；本輪沒有重新執行舊失敗CI或改其結論。

## 受影響需求與實際情境

| Test List／修正要求 | 實際可觀察結果 |
| --- | --- |
| J-FAMILY／public renewal | 既有完整production transport旅程跨過原失敗renewal並完成owner transfer；新增獨立renewal案例使用真public API建立家庭／兩孩、只授權一孩後renewal，保留同deviceSessionId，版本由授權後2增為3；同operationId issueDevice replay回相同receipt、readonly restore仍version3；child list只含允許孩，讀未授權孩AUTHORITY_DENIED。PASS。 |
| live member／device／policy與identity scope | 既有真SDK與Rules案例驗登入bearer／live anonymous authority、terminal撤權／stale-member epoch、foreign installation／跨家庭／他人pairing、不能升權或夾帶其他member/index/device寫入、不能恢復被撤grant。PASS。PIN／UI audience仍為本機gate，不宣稱Rules辨識unlock。 |
| 版本／immutable payload／receipt | 既有public journey驗同ID回原receipt、異payload OPERATION_CONFLICT、stale familyVersion VERSION_CONFLICT；renewal版本不重增，read-only restore不寫新device；raw Rules stale deviceVersion拒絕。PASS。 |
| renewal deny／同交易companion | 新raw Rules regression逐項嘗試擴張childIds、stale version、漏pairing consume、漏receipt：全部assertFails；每次family／device保持完整原值、pairing.consumedAt仍null、receipt不存在。最後合法同transaction renewal成功、childIds仍原一孩／版本+1、pairing consumed與精確receipt一起保存。PASS。 |
| owner transfer／receipt分支 | public完整SDK旅程與family Rules原owner transfer否定情境通過；不完整transfer、額外資源、偽self promotion均拒絕，owner epoch更新後舊invite proof失效。初始化／ordinary receipt與backfill專用receipt由schedule及family案例實際涵蓋。PASS。 |
| J-START／J-SETTINGS／U-SCHEMA-COMPAT | 真SDK v1／v2開始、授權＋activation／revision、once／圖示／completionCopy、固定次日生效／原日原值不回寫、staging丟回應恢復及同日revision／未開歷史讀仍原N。12案相關子項PASS。 |
| J-APPEND／J-DAY-ORDER／J-RETRY | 真SDK原日fixed＋adhoc來源、required增N／optional不增N、same receipt／並行CAS、未知回應原ID恢復不雙追加；同區move／reset／normalization及跨區拒絕、原state／points／N保持／隔日無追加或覆寫。PASS。 |
| J-FAMILY parent view／J-ATOMIC-CONSUMER基線子項 | 家長有界補齊未授權孩v1缺view保持reported，child canonical同人讀拒絕／獨立view偽寫拒絕；caller同tx port與existing no-op／adhoc原definition；canonical建立漏companion整筆拒絕且不留snapshot／receipt。PASS。完整state-ledger consumer仍未驗。 |
| J-BASELINE-HANDOFF本次correction | 實際new HEAD／三檔delta／workspace27檔／direct HumanTask來源與新29案終態可追溯；保存原PR89merge receipt／失敗CI及延期責任，僅交新QA結果到PR。PASS當前QA子項；不提前PASS新PR／Deliver／CI或部署。 |

## 前輪逐項再驗與方法界線

前QA `No actionable work.`，沒有需閉合的QA-NNN。此次修正來自CI失敗後直接HumanTask，不是前QA已驗renewal：前輪transport6未執行，新增本輪transport7證據填補此邊界，不抹除CI failure。

本輪重算plan checkpoint中43個tracked來源hash：42個保持原值，唯一改變是本次Rules；新delta兩測試檔則完整讀diff及重新執行。前QA的suite／browser／IndexedDB／projection terminal與JSON各PASS／pageErrors=[]重新核對，結果見 `evidence/source-validation.json`。

| 前QA來源／案例 | 本輪核對與有效範圍 |
| --- | --- |
| U-REQUIRED／NEXT／SETTINGS／I18N／SCHEMA-COMPAT／DAY-SOURCE／DAY-ORDER／LEGACY-MAPPING／INTENT-SCOPE，35unit／UI | domain／decoder／API／頁面／locales與各測試來源都未改，前輪本代理2026-10-12真實執行的35PASS繼續支持其unit／UI邊界；沒有把它們稱本輪重跑。Rules相關真保存已由本輪29案重驗。 |
| J-START／SETTINGS／FAMILY／OPTIONAL／ACCESSIBILITY UI | 前QA Chrome154.0.8037.99 headless：四孩、零任務、選做手選／不可用state、CRUD／取消／原日圖示、未知開始原意圖，原runner19＋額外6及8組320／768×16→32px×zh-TW／pseudo、keyboard／focus／reducedmotion、短詞可讀與PNG來源保持。無UI delta，不為本次Rules修正重跑browser。 |
| J-RETRY真IndexedDB | 原v1／day bytes、installation／namespace／CAS／remove與新分頁原ID／taskDate／receipt恢復證據仍存在、source未變；fixture backend不是Firestore。public SDK replay／保存恢復在本輪另有真權威驗證。 |
| J-CARRYOVER／legacy／回退 | mapping／preview／v1-v2reader／compatible bundle與單writer來源未變，前實測保持；本輪不import／切writer／部署回退。 |
| 前SDK21／新權威29 | 前schedule12＋family9在本輪重新驗同情境並加入family新atomic案例及transport7，全部PASS；不沿用舊Rules hash為此次green。 |

無本輪required check受環境阻擋、無產品失敗或新增修正項目。沒有新增產品／UI修改，未另跑全repo、build、coverage、release-check或未授權hook bypass，既有repository CI仍自行負責完整交付檢查。

## 未驗、既有提案與後續交付

原 PLAN-008 pending／PLAN-011 in_progress、#68／#70正式report／manual／returned／state-ledger-balance-event-receipt共同原子收入、#69正式next／feedback不重播及#77 native popup／PWA／iOS／VoiceOver／真裝置跨午夜／正式動畫中斷，仍按accepted plan原責任與fingerprint保留；上述本機green不代表全票PASS。取消今日追加政策仍未核定；#75／#76、舊入口與單writer／相容回退界線保持。

沿用既有未採用「今日追加取消／移除 — 政策釐清與能力承接」提案：先調查未回報／已回報／已入帳取消對N、來源與收入政策，再定實作；不重做本issue新增／排序。依賴本票原追加來源與#66，收入分支依賴#68／#70。本issue本次保留可用基線的renewal／owner transfer及權威保存修正，與原交付緊密相依，沒有新增獨立提案、開票或roadmap變更。

前 `deliver/iteration_003/output.md` 記PR89一般merge commit `499bbf91269cfea9140bbe1003ecd44a3b4cfddd`成功receipt，run `38204234476` failed、Rules／Hosting skipped、delivery_complete=false，這些歷史結果保持。本輪不重做已成功merge、不以本機綠色改CI／部署結論。新修正須交PR及Deliver審實際新交付commit／精確action與既有verification，不沿用PR89 action授權。

Manager contract已是generation2，SHA `b7cd08cc28816467cee4865b4e66ff72ddf4df3a1f0b82b393e54a5618bb6e9f`；前QA提到舊contract／保留worktree是當時歷史要求，現按已完成human handoff更新：CI／測試站及適用驗證成功前不cleanup；成功後由Manager依核定契約先封存流程／證據、再處理分支／worktree，GitHub#67仍OPEN。本QA不修改contract／pin、不執行cleanup或其他外部動作。

## 終態與交接

驗收主process exit0、29suite explicit結果與emulator shutdown分別取證；19099／18080／18150／14400／14450 ports閉合，無新增browser／server或未結束child。HEAD維持416f6ace985bf95cdccb76a2d7d02b254b71cdad、worktree clean、git diff --check exit0。本輪只寫qa/iteration_002 owned artifacts，沒有產品或既有測試修改。

全部6項checklist具本輪證據後標[x]，Todo為canonical `No actionable work.`。ordinary outcome-only await_agent交PR；runtime自行解析／驗證後續進入，本phase不寫blackboard或宣稱已發新PR、CI成功、部署／delivery_complete或全量關票。

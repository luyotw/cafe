## Development Guide


## Implementation Analysis

* 現況與切入點
    * `src/cafe/ui/cli.py` 已提供 `cafe skill list/show/validate`，適合在同一個 `skill_app` 下新增 `import` 子命令。
    * `src/cafe/skills/loader.py` 已定義專案技能目錄為 `.cafe/skills`，匯入功能應與這個目錄結構保持一致。
    * agent 啟動流程位於 `src/cafe/agents/`，目前尚未看到專案技能對 CLI 工作目錄的自動暴露邏輯，需要補上啟動期同步或連結機制。
* 實作原則
    * 嚴格遵守 TDD：每個任務先補失敗測試，再補最小實作，最後確認測試轉綠。
    * 測試以行為為主，只在檔案系統、互動提示等邊界使用 mock，避免綁死內部實作細節。
    * 匯入結果需清楚區分 imported、skipped、failed，且衝突覆寫必須經過明確確認。
* 影響範圍
    * CLI 指令與互動提示：`src/cafe/ui/cli.py`，必要時搭配 `src/cafe/ui/inquirer_prompts.py`
    * 技能匯入與驗證邏輯：建議抽到 `src/cafe/skills/` 下的新模組，避免 CLI 累積過多檔案操作細節
    * agent 啟動期技能暴露：`src/cafe/agents/executor.py`、`src/cafe/agents/manager.py` 或各 CLI adapter
    * 測試：`tests/unit/test_cli_catalog_commands.py`、`tests/unit/test_skill_loader.py`，以及 agent 啟動相關單元測試

### 📋 Development Task Breakdown

#### Task 1: 定義技能匯入行為與成功/失敗回報骨架
- [x] **Test 1.1**: 在 `tests/unit/test_cli_catalog_commands.py` 新增 `cafe skill import <path>` 成功案例，驗證可一次匯入多個有效 skill folder 到 `.cafe/skills`
- [x] **Test 1.2**: 新增 path 不存在、不可讀或沒有任何有效 skill folder 的失敗案例，驗證 CLI 以非 0 結束並回報不可匯入
- [x] **Test 1.3**: 新增混合有效與無效 skill folder 的案例，驗證結果會同時顯示 imported 與 skipped/failed 項目
- [x] **Dev 1.4**: 在 `src/cafe/ui/cli.py` 新增 `skill import` 指令入口，接收來源 path 並輸出摘要結果
- [x] **Dev 1.5**: 在 `src/cafe/skills/` 新增匯入服務模組，集中處理來源掃描、有效 skill 判定與結果彙整
- [x] **Dev 1.6**: 讓匯入流程只複製合法 skill folder 到 `.cafe/skills/<skill_name>`，並保留逐項結果供 CLI 顯示

#### Task 2: 補齊衝突覆寫與部分成功流程
- [x] **Test 2.1**: 新增目標 skill 已存在時的互動測試，驗證系統會逐項詢問是否覆寫
- [x] **Test 2.2**: 新增使用者拒絕覆寫的案例，驗證該 skill 被標記為 skipped，其他 skill 照常匯入
- [x] **Test 2.3**: 新增使用者同意覆寫的案例，驗證舊目錄內容被新 skill 取代，且結果標記為 imported 或 overwritten
- [x] **Dev 2.4**: 將衝突檢查與覆寫決策整合進匯入服務，僅在目標名稱衝突時觸發確認
- [x] **Dev 2.5**: 針對單一 skill 的失敗採隔離處理，避免一個壞資料夾中斷整批匯入
- [x] **Dev 2.6**: 統一 CLI 結果輸出格式，明確列出 imported、skipped、failed 與原因

#### Task 3: 讓匯入技能在 agent 啟動時自動可用
- [x] **Test 3.1**: 新增 agent 啟動相關單元測試，驗證存在 `.cafe/skills` 時會建立或更新對應 CLI 所需的 skills 暴露路徑
- [x] **Test 3.2**: 新增重複啟動案例，驗證既有連結或目錄可被安全重用，不會造成錯誤或重複資料
- [x] **Test 3.3**: 新增缺少專案技能目錄時的案例，驗證 agent 啟動不會失敗，且不做多餘連結操作
- [x] **Dev 3.4**: 在 agent 啟動鏈加入專案技能同步／軟連結準備邏輯，將 `.cafe/skills` 暴露到對應 CLI 可讀位置
- [x] **Dev 3.5**: 為檔案系統操作補上安全檢查，避免錯誤覆蓋非 CAFE 管理的目錄或失效連結
- [x] **Dev 3.6**: 確保主要 agent flow 會在每次啟動前執行這段準備流程，使新匯入技能可在後續執行中立即被解析

#### Task 4: 驗證 loader 與匯入後解析行為一致
- [x] **Test 4.1**: 在 `tests/unit/test_skill_loader.py` 新增匯入後 discover/activate 測試，驗證 project skill 可被 loader 正常解析
- [x] **Test 4.2**: 新增無 `SKILL.md`、名稱不合法或 frontmatter/資料夾不一致的案例，驗證匯入時被略過或標記 warning/error
- [x] **Test 4.3**: 新增 project skill 覆蓋 builtin/global skill 的驗證，確認匯入後仍遵守既有 precedence
- [x] **Dev 4.4**: 對齊匯入驗證規則與 `SkillLoader` 現有行為，避免匯入成功但執行期無法 discover
- [x] **Dev 4.5**: 視需要抽出共用 skill 驗證輔助函式，讓 CLI 匯入與 loader 使用同一套判定準則
- [x] **Dev 4.6**: 補齊錯誤訊息與 warning 呈現，避免依賴脆弱的完整字串比對

#### Task 5: 完整回歸與文件化驗證
- [x] **Test 5.1**: 執行新增單元測試，確認 `skill import`、衝突處理、agent 啟動暴露與 loader precedence 全數通過
- [x] **Test 5.2**: 執行既有 skill/agent 相關測試，確認 `skill list/show/validate` 與主要 workflow 無回歸
- [x] **Test 5.3**: 如有適合的整合測試入口，補一個從匯入到 agent 可見的端到端情境
- [x] **Dev 5.4**: 視測試結果微調輸出文案、互動流程與檔案系統邊界處理
- [x] **Dev 5.5**: 更新必要文件或 help text，讓使用者知道 `cafe skill import` 的輸入形式與衝突行為

#### Task 6: Definition of Done (DoD)
Copy the DoD items from the spec's Acceptance Criteria section (lines marked with `✅ **DoD:**`) and verify each one:
- [x] **Dev 6.1**: 使用者可以從單一路徑一次匯入多個有效 skill folder
- [x] **Dev 6.2**: 路徑內含無效 skill folder 時，不影響其他有效 skill 匯入，且結果清楚區分成功與失敗項目
- [x] **Dev 6.3**: 重新啟動相關 agent flow 後，匯入技能可直接使用，不需額外手動設定
- [x] **Dev 6.4**: 使用者能清楚看到哪些 skills 匯入成功、哪些被略過或失敗

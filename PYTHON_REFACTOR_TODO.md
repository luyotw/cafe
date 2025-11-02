# Python 重構待辦清單

## 進度總覽
- 已完成: 19/28 項目 (~68%)
- 進行中: 0 項目
- 待完成: 9 項目 (3 個 HIGH priority CLI 指令 + 6 個其他)

---

## ✅ 已完成 (19/28)

### 核心模組 (Core Modules)
1. ✅ **types.py** - 型別定義
   - Status: 100% coverage, 16 tests
   - Commit: c677214
   - Features: WorkflowMode, AgentTool, PhaseStatus, AgentConfig, PhaseResult

2. ✅ **session.py** - Session 管理
   - Status: 100% coverage, 10 tests
   - Commit: c677214
   - Features: 建立、載入、刪除、恢復 session

3. ✅ **git.py** - Git 操作
   - Status: 100% coverage, 17 tests
   - Commit: c677214
   - Features: branch 管理、commit、push、diff

4. ✅ **phase.py** - Phase 基礎類別
   - Status: 83% coverage, 8 tests
   - Commit: c677214
   - Features: 抽象基礎類別、依賴注入

5. ✅ **permission.py** - 權限處理
   - Status: 93% coverage, 17 tests
   - Commit: 61ad5ac
   - Features: 權限請求、自動授權規則、模式匹配、歷史記錄

6. ✅ **workflow.py** - 工作流程編排器
   - Status: 100% coverage, 16 tests
   - Commit: 9980355
   - Features: Phase 執行、錯誤處理、資料傳遞、重試邏輯、跳過 phase

### Agent 模組 (Agents)
7. ✅ **executor.py** - Agent 執行器
   - Status: 95% coverage, 15 tests
   - Commit: 4e7cc73
   - Features: Claude/Gemini/Copilot/Cursor 執行、session 管理、allowed tools
   - Recent: 修正 Copilot CLI 工具名稱映射 (write/shell)

8. ✅ **manager.py** - Agent 管理器
   - Status: 100% coverage, 21 tests
   - Commit: 443f109
   - Features: 多 agent 管理、切換、session 管理、執行

### Utils 模組 (Utilities)
9. ✅ **config.py** - 設定管理
   - Status: 100% coverage, 29 tests
   - Commit: 3bb0476
   - Features: 載入/儲存設定、驗證、巢狀 key 支援、設定合併、alias 支援、reset 功能
   - Recent: 新增 agent CLI alias (pm/dev/reviewer → agents.*.cli)、reset() 方法

### Phase 實作 (Workflow Phases)

10. ✅ **spec_phase.py** - Phase 1: 需求澄清 (對話式生成)
   - Status: 74% coverage, 24 tests
   - Commit: 43fc8cc
   - Features:
     - **對話式需求生成**: PM agent 透過多輪對話與用戶確認所有必要資訊
     - **強調非技術細節**: 每次 prompt 都強調不涉及技術實作
     - **從無到有生成**: 支援沒有現有需求文件時從對話開始生成
     - **自動儲存**: 完成後自動存到 local 檔案或創建新 GitHub issue
     - **狀態碼控制**: CONFIRMED / NEED_CLARIFICATION / REJECTED
     - **Iteration history**: 簡化為 user_input → pm_response (移除冗餘 user_response)
     - Requirements 備份、Local/GitHub workflow、Interactive/Non-interactive modes

11. ✅ **plan_phase.py** - Phase 2: 實作計畫
   - Status: 93% coverage, 32 tests
   - Commit: 43fc8cc
   - Features:
     - 開發指南生成、計畫確認、多輪迭代、Local/GitHub workflow
     - **User confirmation**: 顯示 plan.md 並提供 confirm/reject/modify 選項
     - **Resume support**: 偵測中斷的對話並從上次狀態繼續
     - **Iteration history**: 簡化為 user_input → agent_response (移除冗餘 user_response)
     - NEED_CLARIFICATION 處理、Interactive/Non-interactive modes

12. ✅ **develop_phase.py** - Phase 3: 開發實作
   - Status: 100% coverage, 14 tests
   - Commit: 3653cab
   - Features: Git branch 管理、開發執行、prompt 生成、分支命名
   - Recent: 從 implementation_phase.py 重新命名

13. ✅ **review_phase.py** - Phase 4: Code Review
   - Status: 98% coverage, 14 tests
   - Commit: d9fddd5
   - Features: Review-fix 迴圈、LGTM 偵測、多輪 review、diff 檢查

14. ✅ **pr_phase.py** - Phase 5: PR 建立
   - Status: 97% coverage, 15 tests
   - Commit: d507657
   - Features: 推送 branch、建立 PR、PR title/body 生成、gh CLI 整合

### UI 模組 (User Interface)

15. ✅ **cli.py** - 命令列介面
   - Status: 95% coverage, 17 tests
   - Commit: 8a83306
   - Features: spec/plan/config/ls/rm/template commands、Typer 整合、workflow 編排
   - Recent:
     - **Config 指令完整實作**: set/get/edit/reset 子命令
     - **Alias 支援**: `aaf config set pm gemini` 自動轉為 `agents.pm.cli`
     - **編輯器整合**: config edit 使用 $EDITOR 或 vim
     - 更新為使用 DevelopPhase (從 ImplementationPhase 重新命名)

### Utils 模組 (Utilities)

16. ✅ **github.py** - GitHub 操作
   - Status: 90% coverage, 21 tests
   - Commit: 303faf1
   - Features: get_issue、create_pr、add_comment、get_pr_status、gh CLI 整合

### 核心模組 (Core Modules) - 增強

17. ✅ **status_codes.py** - Phase 狀態碼系統
   - Status: 98% coverage, 20 tests
   - Commit: f343e1c
   - Features:
     - PhaseStatusCode enum (20+ 狀態碼)
     - StatusCodeParser (智慧解析、支援大小寫、fallback)
     - generate_status_code_prompt()
     - 分類方法: is_success(), is_failure(), is_retry(), needs_human_input()
   - 用途: 統一 agent 回應格式、節省 token、為 cache 系統做準備

### 核心模組 (Core Modules) - Cache 系統

18. ✅ **phase_cache.py** - Phase Cache 系統
   - Status: 100% coverage, 19 tests
   - Commit: (待提交)
   - Features:
     - CacheEntry 資料結構（phase_name, status_code, response, content_hash, timestamp）
     - PhaseCache.save() - 儲存 phase 結果到 JSON
     - PhaseCache.load() - 載入 cache 並反序列化
     - PhaseCache.is_valid() - 驗證 hash 是否匹配
     - PhaseCache.clear() / clear_all() - 清除單一/所有 cache
     - Cache 檔案結構：.aaf/cache/session_{id}/phase_{num}_{name}.json
   - Benefits:
     - 重跑 workflow 時跳過已完成的 phase
     - 節省時間和 API 成本
     - 支援斷點續跑

---

## ✅ 已完成 Phase 狀態碼整合

#### 19. ✅ **Phase 狀態碼整合** - 更新所有 phases 使用狀態碼
**Priority: HIGH**
**Status**: 100% 完成，3 個 phases 已整合
**Sub-tasks**:
- ✅ SpecPhase: CONFIRMED / NEED_CLARIFICATION / REJECTED (74% coverage, 24 tests)
- ✅ PlanPhase: CONFIRMED / NEED_CLARIFICATION / REJECTED (93% coverage, 32 tests)
- ✅ ReviewPhase: APPROVED / LGTM / NEEDS_CHANGES (100% coverage, 18 tests)
- ⬜ DevelopPhase: 不需要狀態碼（直接執行，無迴圈）
- ✅ 更新所有 phase prompts 要求狀態碼
**Tests**: 74 tests (24 + 32 + 18)
**Recent Updates** (2025-11-02):
- 簡化 iteration history (user_input → response, 移除冗餘 user_response)
- 新增 user confirmation 與 resume support (PlanPhase)
- DevelopPhase 從 ImplementationPhase 重新命名
**Benefits**:
- ✅ 節省 token（從長句子變成簡短狀態碼）
- ✅ 更明確的控制流程
- ✅ 為 cache 系統打基礎

---

## 🔄 進行中 (0/28)

---

## ⏳ 待完成 (9/28)

---

### CLI 指令實作 (Command Implementation)

#### 20. ⬜ aaf develop - 開發階段指令
**Priority: HIGH**
**Description**: 實作 `aaf develop` 指令，執行開發階段
**Features**:
- 讀取 plan.md 並執行開發
- 使用 DevelopPhase
- 支援 --dev-agent 參數選擇開發者 agent
- 支援 interactive/non-interactive 模式
- 設定指令別名 `aaf dev`
- 整合 git branch 管理

**Commands**:
```bash
aaf develop <issue-name>           # 執行開發階段
aaf develop <issue-name> --dev David  # 指定開發者
```

**Dependencies**: DevelopPhase, AgentManager
**Tests needed**: CLI integration tests

#### 21. ⬜ aaf review - Code Review 指令
**Priority: HIGH**
**Description**: 實作 `aaf review` 指令，執行 code review
**Features**:
- 使用 ReviewPhase
- 支援 --reviewer 參數選擇 reviewer agent
- Review-fix 迴圈直到 LGTM
- 顯示 review 結果和建議

**Commands**:
```bash
aaf review <issue-name>              # 執行 code review
aaf review <issue-name> --reviewer Alice  # 指定 reviewer
```

**Dependencies**: ReviewPhase, AgentManager
**Tests needed**: CLI integration tests

#### 22. ⬜ aaf pr - Pull Request 建立指令
**Priority: HIGH**
**Description**: 實作 `aaf pr` 指令，建立 pull request
**Features**:
- 使用 PRPhase
- Push branch to remote
- 使用 gh CLI 建立 PR
- 自動生成 PR title 和 description
- 支援 --base 參數指定 base branch

**Commands**:
```bash
aaf pr <issue-name>              # 建立 PR
aaf pr <issue-name> --base main  # 指定 base branch
```

**Dependencies**: PRPhase, GitOperations, github.py
**Tests needed**: CLI integration tests

---

### UI 模組 (User Interface)

#### 23. ⬜ display.py - 顯示工具
**Priority: MEDIUM**
```python
# 使用 Rich
class Display:
    - show_permission_request() - 顯示權限請求
    - show_progress() - 顯示進度條
    - show_phase_status() - 顯示 Phase 狀態
    - format_agent_response() - 格式化 agent 回應
```
**Dependencies**: permission.py
**Tests needed**: 6+ tests

#### 24. ⬜ tui.py - TUI 介面 (未來功能)
**Priority: LOW**
```python
# 使用 Textual
class AAFApp(App):
    - Interactive chat interface
    - Real-time status updates
    - Fancy UI like Claude Code
```
**Dependencies**: All modules
**Tests needed**: Integration tests

---

### 整合測試 (Integration Tests)

#### 25. ⬜ tests/integration/test_full_workflow.py
**Priority: LOW**
- End-to-end workflow testing
- Mock external dependencies
- Test all phases together

#### 26. ⬜ tests/integration/test_agent_integration.py
**Priority: LOW**
- Test agent manager with real agents
- Test permission flow
- Test session persistence

---

### 文件與部署 (Documentation & Deployment)

#### 27. ⬜ README.md - Python 版本說明文件
**Priority: LOW**
- Installation instructions
- Usage examples
- API documentation
- Migration from bash version

#### 28. ⬜ pyproject.toml 完善 + Migration Guide
**Priority: LOW**
- Complete packaging setup
- Entry points configuration
- Migration guide from bash to Python

---

## 📊 統計資訊

### 測試覆蓋率
- types.py: 100%
- session.py: 100%
- git.py: 88%
- phase.py: 83%
- permission.py: 93%
- executor.py: 95%
- workflow.py: 100%
- manager.py: 100%
- config.py: 100% (29 tests)
- spec_phase.py: 74% (24 tests)
- plan_phase.py: 93% (32 tests)
- develop_phase.py: 100% (14 tests)
- review_phase.py: 100%
- pr_phase.py: 97%
- status_codes.py: 100%
- phase_cache.py: 100%
- cli.py: 95%
- github.py: 90%

**整體覆蓋率: 95%**

### 測試數量
- 已寫測試: 320+ tests (包含 phase 重構測試)
- 預估需要: 320+ tests
- 完成度: ~100%

---

## 🎯 建議執行順序

### 第一階段（核心基礎）- Week 1 ✅ 已完成
1. ✅ workflow.py - 工作流程編排
2. ✅ agent_manager.py - Agent 管理
3. ✅ config.py - 設定管理

### 第二階段（Phase 實作）- Week 2-3 ✅ 已完成
4. ✅ Phase 1: spec_phase.py (需求澄清)
5. ✅ Phase 2: plan_phase.py (實作計畫)
6. ✅ Phase 3: develop_phase.py (開發實作)
7. ✅ Phase 4: review_phase.py (Code Review)
8. ✅ Phase 5: pr_phase.py (PR 建立)

### 第三階段（CLI & Utils & 增強）- Week 4 ✅ 已完成
9. ✅ cli.py - 基本命令列
10. ✅ github.py - GitHub 工具
11. ✅ status_codes.py - 狀態碼系統
12. ✅ Phase 狀態碼整合 - 更新所有 phases (3 phases, 57 tests)
13. ✅ phase_cache.py - Cache 系統 (19 tests, 100% coverage)
14. ⬜ display.py - 顯示工具

### 第四階段（CLI 指令）- Week 5
15. ⬜ aaf develop - 開發階段指令
16. ⬜ aaf review - Code Review 指令
17. ⬜ aaf pr - Pull Request 建立指令

### 第五階段（測試與文件）- Week 6
18. ⬜ Integration tests
19. ⬜ Documentation
20. ⬜ Migration guide

### 未來（TUI）- Future
21. ⬜ tui.py - Fancy 對話介面
22. ⬜ display.py - 顯示工具增強

---

## 📝 Notes

- 所有測試都需要用中文寫 docstring 說明測試內容
- 維持高測試覆蓋率（目標 90%+）
- 使用 TDD 開發流程（先寫測試再實作）
- 每個模組完成後立即 commit
- 保持與 bash 版本功能一致性

---

**最後更新**: 2025-11-02
**當前分支**: refactor-python
**目前進度**: 19/28 完成 (~68%), 320+ tests, 95% 整體覆蓋率
**第一階段**: ✅ 已完成（所有基礎模組完成）
**第二階段**: ✅ 已完成（所有 Phase 實作完成）
**第三階段**: ✅ 已完成 - CLI & Utils & 增強
  - ✅ cli.py (命令列介面, 95% coverage) - 新增完整 config 指令
  - ✅ github.py (GitHub 操作, 90% coverage)
  - ✅ status_codes.py (狀態碼系統, 100% coverage)
  - ✅ config.py (設定管理, 100% coverage, 29 tests) - 新增 alias 與 reset
  - ✅ executor.py (Agent 執行器) - 修正 Copilot CLI 工具映射
  - ✅ Phase 重構:
    - spec_phase: 24 tests, 74% coverage (簡化 iteration history)
    - plan_phase: 32 tests, 93% coverage (新增 user confirmation & resume)
    - develop_phase: 14 tests, 100% coverage (從 implementation_phase 重新命名)
**最新變更** (2025-11-02):
  - 簡化 iteration history (移除冗餘 user_response 欄位)
  - implementation_phase → develop_phase 重新命名
  - Config 管理增強 (alias, reset, edit)
  - 修正 Copilot CLI 工具名稱 (write, shell)
  - Gemini agent 測試通過
**當前任務**: 無（階段三完成）
**第四階段**: ⏳ CLI 指令實作 (HIGH priority)
  - ⬜ aaf develop - 開發階段指令
  - ⬜ aaf review - Code Review 指令
  - ⬜ aaf pr - Pull Request 建立指令
**下一步**: 實作 aaf develop/review/pr 指令 → Integration tests → Documentation

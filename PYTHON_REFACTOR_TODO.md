# Python 重構待辦清單

## 進度總覽
- 已完成: 17/23 項目 (~74%)
- 進行中: 1 項目
- 待完成: 5 項目

---

## ✅ 已完成 (17/23)

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
   - Commit: c677214
   - Features: Claude/Gemini/Cursor 執行、session 管理、allowed tools

8. ✅ **manager.py** - Agent 管理器
   - Status: 100% coverage, 21 tests
   - Commit: 443f109
   - Features: 多 agent 管理、切換、session 管理、執行

### Utils 模組 (Utilities)
9. ✅ **config.py** - 設定管理
   - Status: 100% coverage, 22 tests
   - Commit: 372f75d
   - Features: 載入/儲存設定、驗證、巢狀 key 支援、設定合併

### Phase 實作 (Workflow Phases)

10. ✅ **requirements_phase.py** - Phase 1: 需求澄清
   - Status: 98% coverage, 15 tests
   - Commit: 9fb4eb7
   - Features: 多輪澄清迴圈、確認偵測、requirements 備份、Local/GitHub workflow

11. ✅ **analysis_phase.py** - Phase 2: 實作分析
   - Status: 97% coverage, 16 tests
   - Commit: 1d9a8c3
   - Features: 開發指南檢測、分析確認、多輪迭代、Local/GitHub workflow

12. ✅ **implementation_phase.py** - Phase 3: 開發實作
   - Status: 100% coverage, 14 tests
   - Commit: 3a9c1f0
   - Features: Git branch 管理、開發執行、prompt 生成、分支命名

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
   - Commit: e507d7b
   - Features: run/version/config commands、Typer 整合、workflow 編排

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

---

## 🔄 進行中 (1/23)

#### 18. 🔄 **Phase 狀態碼整合** - 更新所有 phases 使用狀態碼
**Priority: HIGH**
**Status**: 測試已寫好，等待實作
**Sub-tasks**:
- ⬜ RequirementsPhase: CONFIRMED / NEED_CLARIFICATION / REJECTED
- ⬜ AnalysisPhase: CONFIRMED / NEED_CLARIFICATION / REJECTED
- ⬜ ReviewPhase: APPROVED / LGTM / NEEDS_CHANGES
- ⬜ ImplementationPhase: COMMITTED / NO_CHANGES
- ⬜ 更新所有 phase prompts 要求狀態碼
**Tests**: 7 tests written for RequirementsPhase (test_requirements_phase_status_codes.py)
**Benefits**:
- 節省 token（從長句子變成簡短狀態碼）
- 更明確的控制流程
- 為 cache 系統打基礎

---

## ⏳ 待完成 (5/23)

---

### UI 模組 (User Interface)

#### 19. ⬜ **phase_cache.py** - Phase Cache 系統
**Priority: HIGH**
**Status**: 待開發
**Dependencies**: status_codes.py
```python
class PhaseCache:
    - save(phase_name, status_code, response, hash) - 儲存 phase 結果
    - load(phase_name) -> CacheEntry - 載入 cache
    - is_valid(phase_name, current_hash) -> bool - 驗證 cache 有效性
    - clear(phase_name) - 清除 cache
    - clear_all() - 清除所有 cache
```
**Cache 檔案結構**:
```
.aaf/cache/session_{id}/
  ├── phase_0_requirements.json
  ├── phase_1_analysis.json
  └── ...
```
**Tests needed**: 15+ tests
**Benefits**:
- 重跑 workflow 時跳過已完成的 phase
- 節省時間和 API 成本
- 支援斷點續跑

#### 20. ⬜ display.py - 顯示工具
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

#### 21. ⬜ tui.py - TUI 介面 (未來功能)
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

#### 22. ⬜ tests/integration/test_full_workflow.py
**Priority: LOW**
- End-to-end workflow testing
- Mock external dependencies
- Test all phases together

#### 23. ⬜ tests/integration/test_agent_integration.py
**Priority: LOW**
- Test agent manager with real agents
- Test permission flow
- Test session persistence

---

### 文件與部署 (Documentation & Deployment)

#### 24. ⬜ README.md - Python 版本說明文件
**Priority: LOW**
- Installation instructions
- Usage examples
- API documentation
- Migration from bash version

#### 25. ⬜ pyproject.toml 完善 + Migration Guide
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
- config.py: 100%
- requirements_phase.py: 98%
- analysis_phase.py: 97%
- implementation_phase.py: 100%
- review_phase.py: 98%
- pr_phase.py: 97%

**整體覆蓋率: 97%**

### 測試數量
- 已寫測試: 274 tests (+17 CLI, +21 GitHub, +20 status codes)
- 預估需要: 300+ tests
- 完成度: ~91%

---

## 🎯 建議執行順序

### 第一階段（核心基礎）- Week 1 ✅ 已完成
1. ✅ workflow.py - 工作流程編排
2. ✅ agent_manager.py - Agent 管理
3. ✅ config.py - 設定管理

### 第二階段（Phase 實作）- Week 2-3 ✅ 已完成
4. ✅ Phase 1: requirements_phase.py
5. ✅ Phase 2: analysis_phase.py
6. ✅ Phase 3: implementation_phase.py
7. ✅ Phase 4: review_phase.py
8. ✅ Phase 5: pr_phase.py

### 第三階段（CLI & Utils & 增強）- Week 4 🔄 進行中
9. ✅ cli.py - 基本命令列
10. ✅ github.py - GitHub 工具
11. ✅ status_codes.py - 狀態碼系統
12. 🔄 Phase 狀態碼整合 - 更新所有 phases
13. ⬜ phase_cache.py - Cache 系統
14. ⬜ display.py - 顯示工具

### 第四階段（測試與文件）- Week 5
15. ⬜ Integration tests
16. ⬜ Documentation
17. ⬜ Migration guide

### 未來（TUI）- Future
18. ⬜ tui.py - Fancy 對話介面

---

## 📝 Notes

- 所有測試都需要用中文寫 docstring 說明測試內容
- 維持高測試覆蓋率（目標 90%+）
- 使用 TDD 開發流程（先寫測試再實作）
- 每個模組完成後立即 commit
- 保持與 bash 版本功能一致性

---

**最後更新**: 2025-10-19
**當前分支**: refactor-python
**目前進度**: 17/25 完成 (~68%), 274 tests, 96% 整體覆蓋率
**第一階段**: ✅ 已完成（所有基礎模組完成）
**第二階段**: ✅ 已完成（所有 Phase 實作完成）
**第三階段**: 🔄 進行中 - CLI & Utils & 增強
  - ✅ cli.py (命令列介面)
  - ✅ github.py (GitHub 操作)
  - ✅ status_codes.py (狀態碼系統)
  - 🔄 Phase 狀態碼整合 (測試已寫，等待實作)
  - ⬜ phase_cache.py (Cache 系統)
  - ⬜ display.py (顯示工具)
**當前任務**: 完成 Phase 狀態碼整合
**下一步**: phase_cache.py → display.py

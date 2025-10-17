# Python 重構待辦清單

## 進度總覽
- 已完成: 6/22 項目 (~27%)
- 進行中: 0 項目
- 待完成: 16 項目

---

## ✅ 已完成 (6/22)

### 核心模組 (Core Modules)
1. ✅ **types.py** - 型別定義
   - Status: 100% coverage, 16 tests
   - Commit: Initial Python refactor structure

2. ✅ **session.py** - Session 管理
   - Status: 100% coverage, 10 tests
   - Features: 建立、載入、刪除、恢復 session

3. ✅ **git.py** - Git 操作
   - Status: 100% coverage, 17 tests
   - Features: branch 管理、commit、push、diff

4. ✅ **phase.py** - Phase 基礎類別
   - Status: 83% coverage, 8 tests
   - Features: 抽象基礎類別、依賴注入

5. ✅ **permission.py** - 權限處理
   - Status: 93% coverage, 17 tests
   - Features: 權限請求、自動授權規則、模式匹配、歷史記錄

### Agent 模組 (Agents)
6. ✅ **executor.py** - Agent 執行器
   - Status: 95% coverage, 15 tests
   - Features: Claude/Gemini/Cursor 執行、session 管理、allowed tools

---

## ⏳ 待完成 (16/22)

### 核心模組 (Core Modules)

#### 7. ⬜ workflow.py - 工作流程編排器
**Priority: HIGH**
```python
class Workflow:
    - execute_phases() - 依序執行 phases
    - handle_phase_errors() - Phase 錯誤處理
    - phase_transition() - Phase 之間資料傳遞
    - retry_logic() - 重試邏輯
```
**Dependencies**: phase.py
**Tests needed**: 10+ tests

---

### Agent 模組 (Agents)

#### 8. ⬜ agent_manager.py - Agent 管理器
**Priority: HIGH**
```python
class AgentManager:
    - get_agent(name) - 取得特定 agent
    - switch_agent() - 切換 agent
    - manage_sessions() - 管理多個 agent sessions
```
**Dependencies**: executor.py, session.py
**Tests needed**: 8+ tests

---

### Phase 實作 (Workflow Phases)

#### 9. ⬜ Phase 1: requirements_phase.py - 需求澄清
**Priority: HIGH**
```python
class RequirementsPhase(Phase):
    - execute() - Roger agent 與使用者對話
    - generate_requirements() - 生成 requirements.md
    - update_requirements() - 更新現有需求
```
**Dependencies**: phase.py, agent_manager.py, permission.py
**Tests needed**: 12+ tests

#### 10. ⬜ Phase 2: analysis_phase.py - 實作分析
**Priority: MEDIUM**
```python
class AnalysisPhase(Phase):
    - execute() - David agent 分析需求
    - generate_plan() - 生成實作計畫
    - validate_plan() - 驗證計畫可行性
```
**Dependencies**: phase.py, agent_manager.py
**Tests needed**: 10+ tests

#### 11. ⬜ Phase 3: implementation_phase.py - 開發實作
**Priority: MEDIUM**
```python
class ImplementationPhase(Phase):
    - execute() - David agent 執行開發
    - create_commits() - 創建 git commits
    - track_progress() - 追蹤開發進度
```
**Dependencies**: phase.py, agent_manager.py, git.py
**Tests needed**: 12+ tests

#### 12. ⬜ Phase 4: review_phase.py - Code Review
**Priority: MEDIUM**
```python
class ReviewPhase(Phase):
    - execute() - Roger agent 審查程式碼
    - review_loop() - 多輪 review-fix 迴圈
    - check_diff() - 檢查程式碼差異
    - collect_feedback() - 收集 review 回饋
```
**Dependencies**: phase.py, agent_manager.py, git.py
**Tests needed**: 15+ tests

#### 13. ⬜ Phase 5: pr_phase.py - PR 建立
**Priority: MEDIUM**
```python
class PRPhase(Phase):
    - execute() - 建立 Pull Request
    - push_branch() - 推送程式碼
    - create_pr() - 使用 gh CLI 建立 PR
    - format_pr_description() - 格式化 PR 描述
```
**Dependencies**: phase.py, git.py, github.py
**Tests needed**: 10+ tests

---

### UI 模組 (User Interface)

#### 14. ⬜ cli.py - 命令列介面
**Priority: HIGH**
```python
# 使用 Typer
@app.command()
def main(
    requirements: str = "requirements.md",
    mode: str = "github",
    issue_id: Optional[str] = None
)
```
**Dependencies**: workflow.py, config.py
**Tests needed**: 8+ tests

#### 15. ⬜ display.py - 顯示工具
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

#### 16. ⬜ tui.py - TUI 介面 (未來功能)
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

### Utils 模組 (Utilities)

#### 17. ⬜ config.py - 設定管理
**Priority: MEDIUM**
```python
class ConfigManager:
    - load_config() - 讀取 .aaf/config
    - get_default_config() - 預設值
    - save_config() - 儲存設定
    - validate_config() - 驗證設定
```
**Dependencies**: None
**Tests needed**: 8+ tests

#### 18. ⬜ github.py - GitHub 操作
**Priority: MEDIUM**
```python
class GitHubOps:
    - get_issue() - 取得 issue 資訊
    - create_pr() - 建立 PR
    - add_pr_comment() - 新增 PR 評論
    - check_pr_status() - 檢查 PR 狀態
```
**Dependencies**: None (uses gh CLI)
**Tests needed**: 10+ tests

---

### 整合測試 (Integration Tests)

#### 19. ⬜ tests/integration/test_full_workflow.py
**Priority: LOW**
- End-to-end workflow testing
- Mock external dependencies
- Test all phases together

#### 20. ⬜ tests/integration/test_agent_integration.py
**Priority: LOW**
- Test agent manager with real agents
- Test permission flow
- Test session persistence

---

### 文件與部署 (Documentation & Deployment)

#### 21. ⬜ README.md - Python 版本說明文件
**Priority: LOW**
- Installation instructions
- Usage examples
- API documentation
- Migration from bash version

#### 22. ⬜ pyproject.toml 完善 + Migration Guide
**Priority: LOW**
- Complete packaging setup
- Entry points configuration
- Migration guide from bash to Python

---

## 📊 統計資訊

### 測試覆蓋率
- types.py: 100%
- session.py: 100%
- git.py: 100%
- phase.py: 83%
- executor.py: 95%
- permission.py: 93%

**平均覆蓋率: 95%**

### 測試數量
- 已寫測試: 83 tests
- 預估需要: 150+ tests
- 完成度: ~55%

---

## 🎯 建議執行順序

### 第一階段（核心基礎）- Week 1
1. ⬜ workflow.py - 工作流程編排
2. ⬜ agent_manager.py - Agent 管理
3. ⬜ config.py - 設定管理

### 第二階段（Phase 實作）- Week 2-3
4. ⬜ Phase 1: requirements_phase.py
5. ⬜ Phase 2: analysis_phase.py
6. ⬜ Phase 3: implementation_phase.py
7. ⬜ Phase 4: review_phase.py
8. ⬜ Phase 5: pr_phase.py

### 第三階段（CLI & Utils）- Week 4
9. ⬜ cli.py - 基本命令列
10. ⬜ display.py - 顯示工具
11. ⬜ github.py - GitHub 工具

### 第四階段（測試與文件）- Week 5
12. ⬜ Integration tests
13. ⬜ Documentation
14. ⬜ Migration guide

### 未來（TUI）- Future
15. ⬜ tui.py - Fancy 對話介面

---

## 📝 Notes

- 所有測試都需要用中文寫 docstring 說明測試內容
- 維持高測試覆蓋率（目標 90%+）
- 使用 TDD 開發流程（先寫測試再實作）
- 每個模組完成後立即 commit
- 保持與 bash 版本功能一致性

---

**最後更新**: 2025-10-17
**當前分支**: refactor-python
**下一步**: 實作 workflow.py

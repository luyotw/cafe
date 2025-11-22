# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概述

AI Agent Flow (CAFE) 是一個 AI 驅動的開發工作流程自動化系統，透過協調多個 AI agents (PM、開發者、審查者) 處理從需求分析到 PR 建立的完整開發流程。目前正在從 bash 版本重構為 Python 版本。

**當前分支**: `refactor-python` (Python 重寫，約 75% 完成)
**主要分支**: `main` (bash 版本)

## 常用指令

### 開發環境設置

```bash
# 安裝依賴（開發模式）
pip install -e ".[dev]"

# 執行 CLI（開發階段）
./cafe <command>
# 或透過 Python 模組
PYTHONPATH="src:$PYTHONPATH" python3 -m cafe.ui.cli <command>
```

### 測試

```bash
# 執行所有測試並產生覆蓋率報告
pytest

# 執行特定測試檔案
pytest tests/unit/test_<module>.py

# 依測試標記執行
pytest -m integration  # 整合測試
pytest -m e2e          # 端對端測試
pytest -m slow         # 慢速測試（涉及 LLM API 呼叫）

# 執行單一測試
pytest tests/unit/test_<module>.py::test_function_name

# 產生 HTML 覆蓋率報告
pytest --cov=cafe --cov-report=html
```

### 程式碼品質

```bash
# 格式化程式碼
black src/ tests/

# Lint 檢查
ruff check src/ tests/

# 型別檢查
mypy src/
```

### CLI 指令

```bash
# Phase 相關指令（自動使用當前 Git branch 名稱作為 issue 識別）
cafe prepare [issue-name]        # 初始化 issue 環境（建立 branch 和目錄結構）
cafe spec                        # Phase 1: 需求澄清
cafe plan                        # Phase 2: 實作計畫
cafe develop                     # Phase 3: 開發實作
cafe review                      # Phase 4: Code Review
cafe review --commit <sha>       # 審查特定 commit
cafe pr                          # Phase 5: 建立 PR

# 配置管理
cafe config set <key> <value>    # 設定配置值（支援 alias）
cafe config get <key>            # 取得配置值
cafe config edit                 # 使用 $EDITOR 編輯配置檔
cafe config reset                # 重置為預設值

# Session 管理
cafe ls                          # 列出所有 sessions
cafe rm <session-name>           # 刪除 session

# 模板管理
cafe template ls                 # 列出所有 plan 模板
cafe template add <source> <name>  # 新增模板
cafe template cat <name>         # 檢視模板內容
cafe template edit <name>        # 編輯模板
cafe template rm <name>          # 刪除模板
```

**重要變更**：從 issue12 開始，核心 phase 指令（spec/plan/develop/review/pr）不再接受 `<issue-name>` 參數，
改為自動使用當前 Git branch 名稱作為 issue 識別。使用前請先執行 `cafe prepare` 初始化環境。

## 架構設計

### 核心工作流程（五階段系統）

系統採用**依賴注入的階段式架構**，每個 phase 都是獨立、可測試的元件：

1. **SpecPhase**（需求澄清）
   - PM agent (Roger) 透過對話式方式生成需求
   - 與使用者迭代直到需求明確（狀態碼：CONFIRMED/NEED_CLARIFICATION/REJECTED）
   - 儲存至本地檔案或 GitHub issue

2. **PlanPhase**（實作計畫）
   - 開發者 agent (David) 根據需求建立實作指南
   - 使用者確認/修改計畫後才繼續
   - 支援從中斷的對話恢復

3. **DevelopPhase**（開發實作）
   - 建立 feature branch，執行開發工作
   - 無狀態碼迴圈（直接執行）

4. **ReviewPhase**（Code Review）
   - 審查者 agent (Richard) 審查程式碼變更
   - 迭代直到 CONFIRMED 或 NEEDS_CHANGES 狀態

5. **PRPhase**（Pull Request）
   - 推送 branch 並透過 `gh` CLI 建立 GitHub PR

### 模組組織

```
src/cafe/
├── core/               # 核心抽象與工具
│   ├── types.py       # Pydantic models 和 enums
│   ├── phase.py       # Phase 抽象基礎類別
│   ├── workflow.py    # Phase 編排與執行
│   ├── session.py     # Session 管理與持久化
│   ├── git.py         # Git 操作封裝
│   ├── permission.py  # 權限請求處理
│   ├── status_codes.py        # Phase 狀態碼系統
│   └── phase_cache.py         # Phase 結果快取
│
├── agents/            # Agent 執行層
│   ├── executor.py    # 執行 AI CLI 工具（Claude/Gemini/Cursor/Copilot）
│   └── manager.py     # 多 agent 管理與 session 處理
│
├── phases/            # 具體 phase 實作
│   ├── spec_phase.py
│   ├── plan_phase.py
│   ├── develop_phase.py
│   ├── review_phase.py
│   └── pr_phase.py
│
├── utils/             # 共用工具
│   ├── config.py      # 配置管理（YAML 格式）
│   ├── github.py      # GitHub 操作（透過 gh CLI）
│   └── template.py    # Plan 模板管理
│
└── ui/                # 使用者介面
    ├── cli.py         # Typer-based CLI
    ├── display.py     # Rich-based 輸出格式化
    └── template_selector.py

agents/                # Agent 角色定義
├── Roger.md          # PM agent prompt
├── David.md          # 開發者 agent prompt
└── Richard.md        # 審查者 agent prompt
```

### 關鍵設計模式

**依賴注入（Dependency Injection）**: Phases 透過建構子接收所有依賴（AgentManager, PermissionHandler, GitOperations），使其可測試且不需要 mock。

**狀態碼系統（Status Code System）**: Agents 回傳結構化狀態碼（如 `CONFIRMED`, `NEED_CLARIFICATION`）而非自由文字，用於控制 phase 迴圈並啟用快取。

**多 Agent 支援**: `AgentExecutor` 抽象化不同 CLI（Claude/Gemini/Cursor/Copilot）間的差異，進行工具名稱轉換。

**Session 管理**: 每個 issue 在 `.cafe/sessions/<issue_name>/` 中有獨立的 session 儲存空間，存放對話歷史和狀態。

**Phase 快取**: `PhaseCache` 儲存 phase 結果並進行內容雜湊，以便重跑 workflow 時跳過重複的工作。

### 配置檔

配置檔位置：`.cafe/config.yaml`

```yaml
agents:
  pm:        # PM agent (Roger)
    name: Roger
    cli: copilot
  dev:       # 開發者 agent (David)
    name: David
    cli: copilot
  reviewer:  # 審查者 agent (Richard)
    name: Richard
    cli: copilot

defaults:
  workflow_mode: local  # 或 "github"
  interactive: true
```

**配置 Alias**: `cafe config set pm gemini` 會自動轉換為 `agents.pm.cli: gemini`

### Agent CLI 工具名稱轉換

`AgentExecutor` 將工具名稱從 Claude 的慣例轉換為其他 CLI：

- **Claude**: `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`
- **Gemini**: `read_file`, `write_file`, `replace`, `bash`, `search_file_content`, `glob`
- **Copilot**: `write`（所有檔案操作）, `shell`（bash/grep/glob）
- **Cursor**: TBD（目前使用 Claude 名稱）

### 工作流程模式

- **local**: 需求和計畫儲存為本地檔案
- **github**: 需求儲存為 GitHub issues，計畫為本地檔案

### 測試組織

- `tests/unit/`: 個別模組的單元測試（95%+ 覆蓋率）
- `tests/integration/`: 結合多個元件的測試
- `tests/e2e/`: 完整 CLI 指令測試

**測試標記**：
- `@pytest.mark.integration`: 整合測試（mock 外部依賴）
- `@pytest.mark.e2e`: 端對端測試（完整 CLI）
- `@pytest.mark.slow`: 涉及 LLM API 呼叫或耗時的測試

## 重要脈絡

### 遷移狀態

這是一個從 bash 到 Python 的進行中重構。詳細進度請參考 `PYTHON_REFACTOR_TODO.md`（21/28 項目完成，約 75%）。

**下一階段優先事項**：
1. `cafe pr` 指令（HIGH priority - 唯一剩餘的核心 CLI 指令）
2. Integration tests（MEDIUM priority）
3. Documentation（LOW priority）

### 程式碼慣例

- 所有 docstrings 和測試描述使用繁體中文
- 使用 Pydantic 定義資料模型
- 使用 pytest，採用 TDD 方法（先寫測試）
- 維持 90%+ 測試覆蓋率
- 需要型別標註（`mypy --strict`）
- 行寬限制：100 字元

### 外部依賴

系統需要安裝以下工具：
- `git` - 版本控制
- `gh` - GitHub CLI（用於 PR 建立和 issue 管理）
- `jq` - JSON 解析（部分 scripts 使用）

Agent CLIs（至少需要一個）：
- `claude` - Claude CLI
- `gemini` - Gemini CLI
- `cursor-agent` - Cursor agent CLI
- `copilot` - GitHub Copilot CLI

### Phase 迭代歷史

涉及對話迭代的 Phases（SpecPhase, PlanPhase）儲存簡化的歷史：
- 只有 `user_input` → `agent_response` 配對
- 無冗餘的 `user_response` 欄位（近期重構中移除）

### 重要檔案

- `pyproject.toml` - 專案 metadata、依賴、工具配置
- `.cafe/config.yaml` - 使用者配置
- `.cafe/sessions/<issue_name>/` - Issue 特定的狀態與歷史
- `.cafe/cache/session_<id>/phase_*.json` - 快取的 phase 結果
- `agents/*.md` - Agent 角色 prompts（Roger, David, Richard）
- `src/cafe/templates/plan/` - Plan 模板

### Issue 與 Session

- **Issue**: 使用 `<issue-name>` 作為識別（例如 `cafe spec fix-login-bug`）
- **Session**: 系統內部管理，每個 issue 會有對應的 session
- Issue 資料儲存在 `.cafe/issues/<issue-name>/` 目錄
- Session 資料儲存在 `.cafe/issues/<issue-name>/sessions/` 目錄（每個 issue 的 sessions 與該 issue 資料放在一起）
- Global sessions（無 issue 時）儲存在 `.cafe/sessions/` 目錄

### 目錄結構範例

```
.cafe/
├── issues/
│   └── myip/                   # Issue 名稱
│       ├── spec/               # 需求文件
│       │   ├── spec.md
│       │   └── history/
│       ├── plan/               # 實作計畫
│       │   ├── plan.md
│       │   └── history/
│       └── sessions/           # Issue 專屬 agent sessions
│           ├── Roger_copilot.json
│           ├── David_claude.json
│           └── Richard_gemini.json
├── sessions/                   # Global sessions (無 issue)
│   ├── Roger_copilot.json
│   └── David_claude.json
└── config.yaml
```

### Session 檔案格式

每個 session 檔案是 JSON 格式，包含：
```json
{
  "agent_name": "Roger",
  "cli": "copilot",
  "session_id": "session_xxx",
  "created_at": "2025-11-02T13:00:00",
  "last_used_at": "2025-11-02T13:05:00"
}
```

### 測試哲學

- **高覆蓋率要求**: 所有核心模組需達到 90%+ 覆蓋率
- **測試隔離**: 每個 phase 透過依賴注入可獨立測試
- **Mock 外部系統**: Git、GitHub、agent CLIs 在單元測試中使用 mock
- **TDD 方法**: 先寫測試後實作
- **中文文件**: 所有測試 docstrings 使用繁體中文說明測試目的

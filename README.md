# AI Agent Flow - AI Driven Development Workflow Automation

AI 驅動的開發工作流程自動化系統，透過協調多個 AI agents (PM、開發者、審查者) 處理從需求分析到 PR 建立的完整開發流程。

## 系統需求

- [git](https://git-scm.com/)
- [gh](https://cli.github.com/)
- [jq](https://jqlang.org/)

## 核心架構

### 主腳本：`ai-dev`

6 階段工作流程：
1. **Phase 1: 需求分析** - Roger (PM) 澄清需求
2. **Phase 2: 實作分析** - David (開發者) 建立實作計畫
3. **Phase 3: 開發** - David 實作功能並 commit
4. **Phase 4: Pre-PR Review** - Richard (審查者) 進行程式碼審查迴圈
5. **Phase 5: 建立 PR** - 自動建立 GitHub PR
6. **Phase 6: 最終審查** - 人工最終確認及 merge

### Agent 系統

三個 agents (`agents/` 目錄)：
- **Roger** (PM)：需求澄清，避免技術細節
- **David** (開發者)：實作分析、寫程式、git 操作
- **Richard** (審查者)：程式碼品質審查


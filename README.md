# The CAFE Engine - AI Driven Development Workflow Automation

AI 驅動的開發工作流程自動化系統，透過協調多個 AI agents (PM、開發者、審查者)，並整合多種 CLI agent 工具，建立從需求分析到產生 pull request 的完整開發流程。

## 系統需求

- [git](https://git-scm.com/)
- [gh](https://cli.github.com/)
- [jq](https://jqlang.org/)

## 核心架構

### 5 階段工作流程：
1. **Phase 1: 需求分析** - 澄清需求並撰寫規格文件
2. **Phase 2: 實作分析** - 建立實作計畫
3. **Phase 3: 開發** - 實作功能並 commit
4. **Phase 4: Code Review** - 進行程式碼審查
5. **Phase 5: 建立 PR** - 自動建立 GitHub PR

### Agent 系統
- **PM**：需求澄清，避免技術細節
- **Developer**：實作分析、寫程式
- **Reviewer**：程式碼品質審查

## 使用說明
> TODO

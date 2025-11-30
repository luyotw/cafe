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

## 重要注意事項

### Gemini CLI 的 write_file 限制

Gemini CLI 的 `write_file` 工具**不支援路徑限制**。如果在 `--allowed-tools` 中指定 `write_file(/path/to/file)`，Gemini 會將其視為「沒有寫入權限」而無法寫入任何檔案。

**解決方案**: 系統會自動將 `write_file(/path)` 轉換為 `write_file`，給予完整的檔案寫入權限。請在 agent prompt 中明確指示 agent 應該寫入的檔案路徑。

**影響的 phases**:
- ReviewPhase: Prompt 中會明確告知 reviewer 應寫入 `review_XXX.md` 的完整路徑

## 使用說明
> TODO
test fix

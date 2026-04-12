## Changes

- 匯入流程新增 frontmatter 驗證，若 `SKILL.md` 的 `name` 與資料夾名稱不一致，會在匯入當下以 skipped 回報，不再誤判為 imported。
- project skills workspace helper 擴充到 Gemini，讓預設主流程中的 `pm` / `reviewer` CLI 也會建立 `.gemini/skills` 入口。
- 補上 reviewer 指出的缺口測試，涵蓋 invalid skill metadata 與 Gemini project skills 暴露。
- 本輪 issue183 修正會以獨立 commit 交付；目前 `git status --short` 中其餘變更屬於工作樹內既有、與本 issue 無關的未提交修改。

## Verification

- `pytest tests/unit/test_cli_catalog_commands.py tests/unit/test_gemini_cli.py tests/unit/test_skill_loader.py`

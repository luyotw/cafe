## Result

已修正 `review/iteration_007` 提出的 `cafe skill rm` 路徑穿越缺陷，現在刪除目標只接受安全的單一 skill 名稱；包含 `..`、路徑分隔符與絕對路徑的輸入都會被拒絕，並以非 0 結束碼回報。這輪採用 `CAFE_CONFIRMED`。

## Changes

- 在 `src/cafe/skills/remover.py` 補上 skill 名稱安全驗證，阻擋 `../...`、絕對路徑與其他非單一路徑片段輸入，避免刪除 `.cafe/skills` 以外的內容
- 在 `src/cafe/ui/cli.py` 調整 `skill rm` 的退出條件，只要出現 failed item 就回傳非 0，避免危險輸入被誤判為成功
- 在 `tests/unit/test_cli_catalog_commands.py` 新增兩個回歸測試，分別覆蓋 parent path segment 與 absolute path 被拒絕的情境

## Verification

- 測試指令：`pytest -q tests/unit/test_cli_catalog_commands.py`
- 測試結果：`18 passed`
- 補充：新增案例已驗證危險路徑不會刪到 `.cafe/skills` 以外的目錄，且 CLI 會正確回傳失敗狀態

# Testing Documentation for aaf

## 測試文件

### 1. test_static_analysis.sh

**用途**: 靜態分析測試，檢查代碼結構和邏輯正確性

**運行方式**:
```bash
./test_static_analysis.sh
```

**測試內容** (共 15 個測試):

1. **handle_permissions** 有 3 個選項 (y/t/s)
2. **handle_permissions** 返回正確的退出碼 (0/1/2)
3. **handle_exception** 有返回值文檔註解
4. **handle_exception** 詢問任務是否完成
5. **handle_exception** 返回正確的退出碼
6. **process_exception_result** 函數存在且有文檔
7. **process_exception_result** 處理 3 種情況 (0/2/else)
8. **process_exception_result** 正確更新 prompt
9. **execute_with_retry** 調用 process_exception_result (2 次)
10. **execute_with_retry** 處理 permission_result == 2
11. **phase3_development** 使用 while true 循環
12. **phase3_development** 循環可以 break
13. **execute_with_retry** 沒有重複的例外處理邏輯
14. **handle_exception** 在權限對話中選擇跳過時返回 2
15. 所有 6 個 phase 函數存在

**優點**:
- 快速執行（不到 1 秒）
- 不需要 mock 或執行實際代碼
- 驗證代碼結構和關鍵邏輯
- 適合 CI/CD 集成

**限制**:
- 只檢查靜態結構，不執行代碼
- 無法測試運行時行為

### 2. test_aaf.sh（實驗性）

**用途**: 完整的單元測試框架，使用 mock 測試運行時行為

**運行方式**:
```bash
./test_aaf.sh
```

**測試內容**:
- handle_permissions 的三種選擇（授權/對話/跳過）
- handle_exception 的任務完成/繼續
- process_exception_result 的所有情況
- execute_with_retry 的完整流程

**限制**:
- 需要 mock claude CLI、gh CLI、git 等
- 執行時間較長
- 可能因為 TTY 輸入問題而卡住（目前已知問題）

## 運行所有測試

```bash
# 快速測試（推薦）
./test_static_analysis.sh

# 完整測試（如果 test_aaf.sh 修復了 TTY 問題）
./test_aaf.sh
```

## CI/CD 集成建議

在 CI pipeline 中運行靜態分析測試：

```yaml
test:
  script:
    - ./test_static_analysis.sh
```

## 未來改進

1. **test_aaf.sh 的 TTY 問題**: 需要修復輸入模擬，使其能在非交互環境運行
2. **整合測試**: 添加 Phase 1-4 的端到端測試
3. **Mock 改進**: 完善 agent 回應的模擬，測試更多邊界情況
4. **性能測試**: 測試長時間運行和大量迭代的情況
5. **錯誤處理測試**: 測試各種錯誤情況的處理

## 測試結果

當前狀態：
- ✅ test_static_analysis.sh: **15/15 通過**
- ⚠️ test_aaf.sh: 需要修復 TTY 問題

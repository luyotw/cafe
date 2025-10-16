#!/bin/bash

# 靜態分析測試 - 不執行代碼，只檢查結構
# 驗證所有重要的修改都已正確實施

set -e

# 測試統計
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# 顏色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

AAF_FILE="./aaf"

echo "========================================"
echo "Static Analysis Test for aaf"
echo "========================================"

# 輔助函數
test_pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

test_fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

# 測試 1: handle_permissions 有 3 個選項 (y/t/s)
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: handle_permissions has 3 options (y/t/s)${NC}"

if grep -q "請選擇 (y/t/s)" "$AAF_FILE"; then
    if grep -A 30 "請選擇 (y/t/s)" "$AAF_FILE" | grep -q "case \$choice in" && \
       grep -A 30 "請選擇 (y/t/s)" "$AAF_FILE" | grep -q "y)" && \
       grep -A 30 "請選擇 (y/t/s)" "$AAF_FILE" | grep -q "t)" && \
       grep -A 30 "請選擇 (y/t/s)" "$AAF_FILE" | grep -q "s)"; then
        test_pass "handle_permissions supports y/t/s options"
    else
        test_fail "handle_permissions missing some options"
    fi
else
    test_fail "handle_permissions doesn't have (y/t/s) prompt"
fi

# 測試 2: handle_permissions 的返回值
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: handle_permissions returns correct codes${NC}"

if grep -A 40 "請選擇 (y/t/s)" "$AAF_FILE" | grep -q "y)" && \
   grep -A 40 "請選擇 (y/t/s)" "$AAF_FILE" | grep "y)" -A 20 | grep -q "return 0" && \
   grep -A 40 "請選擇 (y/t/s)" "$AAF_FILE" | grep -q "t)" && \
   grep -A 40 "請選擇 (y/t/s)" "$AAF_FILE" | grep "t)" -A 3 | grep -q "return 1" && \
   grep -A 40 "請選擇 (y/t/s)" "$AAF_FILE" | grep -q "s)" && \
   grep -A 40 "請選擇 (y/t/s)" "$AAF_FILE" | grep "s)" -A 3 | grep -q "return 2"; then
    test_pass "handle_permissions returns 0/1/2 correctly"
else
    test_fail "handle_permissions return codes incorrect"
fi

# 測試 3: handle_exception 有返回值註解
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: handle_exception documented return codes${NC}"

if grep -B 1 "^handle_exception()" "$AAF_FILE" | grep -q "返回：0=任務完成, 1=需要繼續執行, 2=跳過階段"; then
    test_pass "handle_exception return codes documented"
else
    test_fail "handle_exception return codes not documented"
fi

# 測試 4: handle_exception 詢問任務是否完成
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: handle_exception asks if task is complete${NC}"

if grep -A 100 "^handle_exception()" "$AAF_FILE" | grep -q "任務是否已完成"; then
    if grep -A 120 "^handle_exception()" "$AAF_FILE" | grep -q "任務已完成，進入下一階段" && \
       grep -A 120 "^handle_exception()" "$AAF_FILE" | grep -q "任務未完成，繼續執行"; then
        test_pass "handle_exception has task completion check"
    else
        test_fail "handle_exception missing task completion messages"
    fi
else
    test_fail "handle_exception doesn't ask if task is complete"
fi

# 測試 5: handle_exception 返回正確的退出碼
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: handle_exception returns correct exit codes${NC}"

if grep -A 130 "任務是否已完成" "$AAF_FILE" | grep -A 10 "if \[ \"\$task_done\" = \"y\" \]" | grep -q "return 0" && \
   grep -A 130 "任務是否已完成" "$AAF_FILE" | grep -A 15 "if \[ \"\$task_done\" = \"y\" \]" | grep -q "return 1"; then
    test_pass "handle_exception returns 0 for complete, 1 for continue"
else
    test_fail "handle_exception return codes for task completion incorrect"
fi

# 測試 6: process_exception_result 函數存在
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: process_exception_result function exists${NC}"

if grep -q "^process_exception_result()" "$AAF_FILE"; then
    if grep -B 3 "^process_exception_result()" "$AAF_FILE" | grep -q "# 處理 handle_exception 的返回值"; then
        test_pass "process_exception_result exists with documentation"
    else
        test_fail "process_exception_result missing documentation"
    fi
else
    test_fail "process_exception_result function not found"
fi

# 測試 7: process_exception_result 處理 3 種情況
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: process_exception_result handles 3 cases${NC}"

if grep -A 30 "^process_exception_result()" "$AAF_FILE" | grep -q "if \[ \$exception_result -eq 0 \]" && \
   grep -A 30 "^process_exception_result()" "$AAF_FILE" | grep -q "elif \[ \$exception_result -eq 2 \]" && \
   grep -A 30 "^process_exception_result()" "$AAF_FILE" | grep -q "else"; then
    test_pass "process_exception_result handles 0/2/else cases"
else
    test_fail "process_exception_result missing case handling"
fi

# 測試 8: process_exception_result 更新 prompt
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: process_exception_result updates prompt for continue${NC}"

if grep -A 35 "^process_exception_result()" "$AAF_FILE" | grep -q "prompt_ref=\"請繼續執行原本的任務。\""; then
    test_pass "process_exception_result updates prompt correctly"
else
    test_fail "process_exception_result doesn't update prompt"
fi

# 測試 9: execute_with_retry 調用 process_exception_result
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: execute_with_retry calls process_exception_result${NC}"

count=$(grep -A 80 "^execute_with_retry()" "$AAF_FILE" | grep -c "process_exception_result" || echo "0")

if [ "$count" -ge 2 ]; then
    test_pass "execute_with_retry calls process_exception_result ($count times)"
else
    test_fail "execute_with_retry only calls process_exception_result $count times (expected >= 2)"
fi

# 測試 10: execute_with_retry 處理 permission_result == 2
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: execute_with_retry handles permission_result == 2${NC}"

if grep -A 80 "^execute_with_retry()" "$AAF_FILE" | grep -q "elif \[ \$permission_result -eq 2 \]"; then
    if grep -A 80 "^execute_with_retry()" "$AAF_FILE" | grep -A 3 "elif \[ \$permission_result -eq 2 \]" | grep -q "return 0"; then
        test_pass "execute_with_retry handles skip (permission_result == 2)"
    else
        test_fail "execute_with_retry doesn't return 0 for skip"
    fi
else
    test_fail "execute_with_retry doesn't check permission_result == 2"
fi

# 測試 11: Phase 3 使用 while true + execute_with_retry
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: phase3_development uses while true loop${NC}"

if grep -A 80 "^phase3_development()" "$AAF_FILE" | grep -q "while true; do"; then
    if grep -A 85 "^phase3_development()" "$AAF_FILE" | grep -A 5 "while true" | grep -q "execute_with_retry"; then
        test_pass "phase3_development uses while true with execute_with_retry"
    else
        test_fail "phase3_development loop doesn't call execute_with_retry"
    fi
else
    test_fail "phase3_development doesn't have while true loop"
fi

# 測試 12: Phase 3 循環會 break
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: phase3_development loop can break${NC}"

if grep -A 90 "^phase3_development()" "$AAF_FILE" | grep -A 10 "while true" | grep -q "break"; then
    test_pass "phase3_development loop has break statement"
else
    test_fail "phase3_development loop missing break statement"
fi

# 測試 13: 沒有內嵌重複的 exception 處理邏輯
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: No duplicated exception result handling in execute_with_retry${NC}"

# 統計 execute_with_retry 函數內部直接檢查 exception_result 的次數（不包括調用 process_exception_result 的行）
inline_checks=$(grep -A 80 "^execute_with_retry()" "$AAF_FILE" | \
                grep "if \[ \$exception_result" | \
                grep -v "process_exception_result" | \
                wc -l || echo "0")

if [ "$inline_checks" -eq 0 ]; then
    test_pass "No inline exception_result handling (using process_exception_result)"
else
    test_fail "Found $inline_checks inline exception_result checks (should use process_exception_result)"
fi

# 測試 14: handle_exception 在 permission_result == 2 時返回 2
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: handle_exception returns 2 when user chooses skip in permission dialog${NC}"

if grep -A 120 "^handle_exception()" "$AAF_FILE" | \
   grep -A 10 "elif \[ \$permission_result -eq 2 \]" | \
   grep -q "return 2"; then
    test_pass "handle_exception returns 2 for skip in permission dialog"
else
    test_fail "handle_exception doesn't return 2 for permission skip"
fi

# 測試 15: 所有 Phase 函數存在
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: All phase functions exist${NC}"

missing_phases=""
for phase in phase1_requirements_clarification phase2_implementation_analysis phase3_development phase4_pre_pr_review phase5_create_pr phase6_final_review; do
    if ! grep -q "^${phase}()" "$AAF_FILE"; then
        missing_phases="$missing_phases $phase"
    fi
done

if [ -z "$missing_phases" ]; then
    test_pass "All 6 phase functions exist"
else
    test_fail "Missing phase functions:$missing_phases"
fi

# 測試 16: Phase 4 達到最大次數時詢問用戶是否建立 PR
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: phase4 asks user when max review iterations reached${NC}"

if grep -A 200 "^phase4_pre_pr_review()" "$AAF_FILE" | grep -q "if \[ \$iteration -eq \$MAX_REVIEW_ITERATIONS \]"; then
    if grep -A 200 "^phase4_pre_pr_review()" "$AAF_FILE" | \
       grep -A 20 "if \[ \$iteration -eq \$MAX_REVIEW_ITERATIONS \]" | \
       grep -q "是否仍要建立 Pull Request"; then
        test_pass "phase4 asks user when max iterations reached"
    else
        test_fail "phase4 doesn't ask user about creating PR"
    fi
else
    test_fail "phase4 doesn't check MAX_REVIEW_ITERATIONS"
fi

# 測試 17: Phase 4 在用戶選擇 y 時繼續建立 PR
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: phase4 creates PR when user chooses y${NC}"

if grep -A 200 "^phase4_pre_pr_review()" "$AAF_FILE" | \
   grep -A 30 "是否仍要建立 Pull Request" | \
   grep -q "if \[ \"\$create_pr_choice\" = \"y\" \]"; then
    if grep -A 200 "^phase4_pre_pr_review()" "$AAF_FILE" | \
       grep -A 35 "是否仍要建立 Pull Request" | \
       grep -A 5 "if \[ \"\$create_pr_choice\" = \"y\" \]" | \
       grep -q "touch \"\$ISSUE_DIR/pre_pr_review_passed\""; then
        test_pass "phase4 marks review as passed when user chooses y"
    else
        test_fail "phase4 doesn't mark review as passed"
    fi
else
    test_fail "phase4 doesn't check user choice"
fi

# 測試 18: Phase 4 在用戶選擇 n 時停止流程
TESTS_RUN=$((TESTS_RUN + 1))
echo -e "\n${YELLOW}Test $TESTS_RUN: phase4 stops when user chooses n${NC}"

if grep -A 200 "^phase4_pre_pr_review()" "$AAF_FILE" | \
   grep -A 35 "是否仍要建立 Pull Request" | \
   grep -q "else" && \
   grep -A 200 "^phase4_pre_pr_review()" "$AAF_FILE" | \
   grep -A 40 "是否仍要建立 Pull Request" | \
   grep -A 10 "else" | \
   grep -q "exit 0"; then
    test_pass "phase4 exits when user chooses n"
else
    test_fail "phase4 doesn't exit when user chooses n"
fi

# 輸出統計
echo ""
echo "========================================"
echo "Test Results"
echo "========================================"
echo -e "Total: $TESTS_RUN"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "${RED}Failed: $TESTS_FAILED${NC}"
echo "========================================"

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
fi

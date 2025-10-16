#!/bin/bash

# 測試框架 for aaf script
# 使用 bats 風格但不依賴 bats，純 bash 實作

set -e

# 測試統計
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0
CURRENT_TEST=""

# 顏色輸出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 測試輔助函數
test_start() {
    CURRENT_TEST="$1"
    TESTS_RUN=$((TESTS_RUN + 1))
    echo -e "\n${YELLOW}Test $TESTS_RUN: $CURRENT_TEST${NC}"
}

test_pass() {
    TESTS_PASSED=$((TESTS_PASSED + 1))
    echo -e "${GREEN}✓ PASS${NC}"
}

test_fail() {
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo -e "${RED}✗ FAIL: $1${NC}"
}

assert_equals() {
    local expected="$1"
    local actual="$2"
    local message="${3:-}"

    if [ "$expected" = "$actual" ]; then
        return 0
    else
        test_fail "Expected '$expected' but got '$actual'. $message"
        return 1
    fi
}

assert_contains() {
    local haystack="$1"
    local needle="$2"
    local message="${3:-}"

    if echo "$haystack" | grep -q "$needle"; then
        return 0
    else
        test_fail "Expected to contain '$needle'. $message"
        return 1
    fi
}

# Mock 函數設定
setup_mocks() {
    # 建立測試目錄
    export TEST_DIR=$(mktemp -d)
    export MOCK_DIR="$TEST_DIR/mocks"
    mkdir -p "$MOCK_DIR"

    # 設定環境變數
    export PATH="$MOCK_DIR:$PATH"
    export AI_DIR="$TEST_DIR/.ai"
    export SESSIONS_DIR="$AI_DIR/sessions"
    export LOGS_DIR="$AI_DIR/logs"
    export ISSUE_DIR="$LOGS_DIR/test-issue"
    export TMP_DIR="$AI_DIR/tmp"

    mkdir -p "$SESSIONS_DIR" "$ISSUE_DIR" "$TMP_DIR"

    # 記錄 mock 調用
    export MOCK_CALLS_FILE="$TEST_DIR/mock_calls.log"
    > "$MOCK_CALLS_FILE"
}

teardown_mocks() {
    rm -rf "$TEST_DIR"
}

# 建立 mock claude CLI
create_mock_claude() {
    local behavior="$1"  # success, permission_denied, error, session_invalid
    local response_file="$2"

    cat > "$MOCK_DIR/claude" << 'EOF'
#!/bin/bash
echo "claude $@" >> "$MOCK_CALLS_FILE"

# 解析參數
session_id=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --resume)
            session_id="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

# 讀取 stdin (prompt)
prompt=$(cat)
echo "Prompt: $prompt" >> "$MOCK_CALLS_FILE"

# 根據設定的行為返回不同的 JSON
behavior="${MOCK_BEHAVIOR:-success}"
case "$behavior" in
    success)
        cat << JSON
{
    "session_id": "${session_id:-test-session-123}",
    "result": "我已完成任務",
    "content": "我已完成任務",
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0
    },
    "total_cost_usd": "0.001",
    "duration_ms": 1000
}
JSON
        ;;
    permission_denied)
        cat << JSON
{
    "session_id": "${session_id:-test-session-123}",
    "result": "我需要權限",
    "content": "我需要權限",
    "permission_denials": [
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/test/file.txt"
            }
        }
    ],
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0
    },
    "total_cost_usd": "0.001",
    "duration_ms": 1000
}
JSON
        ;;
    error)
        echo "Error: Something went wrong" >&2
        exit 1
        ;;
    session_invalid)
        echo "Error: No conversation found" >&2
        exit 1
        ;;
    *)
        if [ -f "$MOCK_RESPONSE_FILE" ]; then
            cat "$MOCK_RESPONSE_FILE"
        else
            echo '{"error": "Unknown behavior"}' >&2
            exit 1
        fi
        ;;
esac
EOF
    chmod +x "$MOCK_DIR/claude"
}

# 建立 mock gh CLI
create_mock_gh() {
    cat > "$MOCK_DIR/gh" << 'EOF'
#!/bin/bash
echo "gh $@" >> "$MOCK_CALLS_FILE"

case "$1" in
    issue)
        case "$2" in
            view)
                cat << JSON
{
    "title": "Test Issue",
    "body": "Test issue body\n\n> 需求分析狀態：${ISSUE_STATUS:-待確認}"
}
JSON
                ;;
            edit)
                echo "Issue edited" >> "$MOCK_CALLS_FILE"
                ;;
            comment)
                echo "Comment added" >> "$MOCK_CALLS_FILE"
                ;;
        esac
        ;;
    auth)
        exit 0
        ;;
    repo)
        echo '{"nameWithOwner": "test/repo"}'
        ;;
esac
EOF
    chmod +x "$MOCK_DIR/gh"
}

# 建立 mock git
create_mock_git() {
    cat > "$MOCK_DIR/git" << 'EOF'
#!/bin/bash
echo "git $@" >> "$MOCK_CALLS_FILE"

case "$1" in
    branch)
        echo "main"
        ;;
    remote)
        echo "main"
        ;;
    status)
        echo "On branch test-branch"
        ;;
    diff)
        echo "diff --git a/test.txt b/test.txt"
        ;;
    log)
        echo "abc123 Test commit"
        ;;
    checkout|fetch|add|commit|push)
        exit 0
        ;;
    show-ref)
        exit 1  # branch 不存在
        ;;
    symbolic-ref)
        echo "main"
        ;;
esac
EOF
    chmod +x "$MOCK_DIR/git"
}

# 建立 mock jq
create_mock_jq() {
    # 使用真實的 jq，但記錄調用
    local real_jq=$(which jq)
    cat > "$MOCK_DIR/jq" << EOF
#!/bin/bash
echo "jq \$@" >> "\$MOCK_CALLS_FILE"
exec $real_jq "\$@"
EOF
    chmod +x "$MOCK_DIR/jq"
}

# Source aaf 的函數（不執行 main）
source_aaf_functions() {
    # 讀取 aaf 但不執行 main
    local aaf_content=$(cat ./aaf)
    # 移除最後的 main 調用
    echo "${aaf_content%main*}" > "$TEST_DIR/aaf_funcs.sh"

    # 設定必要的環境變數避免錯誤
    export REPO_DIR="$TEST_DIR"
    export WORKFLOW_MODE="local"
    export REQUIREMENTS_FILE="$TEST_DIR/requirements.md"
    export PM_AGENT="Roger"
    export DEV_AGENT="David"
    export REVIEW_AGENT="Richard"

    source "$TEST_DIR/aaf_funcs.sh" 2>/dev/null
}

# ============================================================================
# 測試用例
# ============================================================================

test_handle_permissions_authorize() {
    test_start "handle_permissions: user authorizes (y)"

    setup_mocks
    create_mock_claude "permission_denied"
    create_mock_jq
    source_aaf_functions

    # 模擬 LAST_RAW_OUTPUT
    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"
    cat > "$LAST_RAW_OUTPUT" << 'JSON'
{
    "permission_denials": [
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/test/file.txt"
            }
        }
    ]
}
JSON

    # 模擬使用者輸入 'y'
    local result
    result=$(echo "y" | handle_permissions "test-session" "TestAgent" "Read(*)")
    local exit_code=$?

    if assert_equals "0" "$exit_code" "Should return 0 for authorize"; then
        if assert_contains "$result" "Edit(/test/file.txt)" "Should add tool to allowed list"; then
            test_pass
        fi
    fi

    teardown_mocks
}

test_handle_permissions_dialog() {
    test_start "handle_permissions: user chooses dialog (t)"

    setup_mocks
    create_mock_jq
    source_aaf_functions

    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"
    cat > "$LAST_RAW_OUTPUT" << 'JSON'
{
    "permission_denials": [
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/test/file.txt"
            }
        }
    ]
}
JSON

    # 模擬使用者輸入 't'
    echo "t" | handle_permissions "test-session" "TestAgent" "Read(*)" > /dev/null
    local exit_code=$?

    if assert_equals "1" "$exit_code" "Should return 1 for dialog mode"; then
        test_pass
    fi

    teardown_mocks
}

test_handle_permissions_skip() {
    test_start "handle_permissions: user chooses skip (s)"

    setup_mocks
    create_mock_jq
    source_aaf_functions

    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"
    cat > "$LAST_RAW_OUTPUT" << 'JSON'
{
    "permission_denials": [
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/test/file.txt"
            }
        }
    ]
}
JSON

    # 模擬使用者輸入 's'
    echo "s" | handle_permissions "test-session" "TestAgent" "Read(*)" > /dev/null
    local exit_code=$?

    if assert_equals "2" "$exit_code" "Should return 2 for skip"; then
        test_pass
    fi

    teardown_mocks
}

test_handle_exception_task_complete() {
    test_start "handle_exception: task complete after dialog"

    setup_mocks
    create_mock_claude "success"
    create_mock_jq
    source_aaf_functions

    # 模擬使用者輸入：對話內容 + END + 不繼續對話 (n) + 任務完成 (y)
    (echo "請完成任務"; echo "END"; echo "n"; echo "y") | \
        handle_exception "test-session" "acceptEdits" "TestAgent" "Read(*)" > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "0" "$exit_code" "Should return 0 when task is complete"; then
        test_pass
    fi

    teardown_mocks
}

test_handle_exception_task_continue() {
    test_start "handle_exception: task needs to continue"

    setup_mocks
    create_mock_claude "success"
    create_mock_jq
    source_aaf_functions

    # 模擬使用者輸入：對話內容 + END + 不繼續對話 (n) + 任務未完成 (n)
    (echo "請完成任務"; echo "END"; echo "n"; echo "n") | \
        handle_exception "test-session" "acceptEdits" "TestAgent" "Read(*)" > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "1" "$exit_code" "Should return 1 when task needs to continue"; then
        test_pass
    fi

    teardown_mocks
}

test_process_exception_result_complete() {
    test_start "process_exception_result: task complete"

    setup_mocks
    source_aaf_functions

    local prompt="test prompt"
    process_exception_result 0 prompt > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "0" "$exit_code" "Should return 0 for complete"; then
        test_pass
    fi

    teardown_mocks
}

test_process_exception_result_skip() {
    test_start "process_exception_result: skip stage"

    setup_mocks
    source_aaf_functions

    local prompt="test prompt"
    process_exception_result 2 prompt > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "0" "$exit_code" "Should return 0 for skip"; then
        test_pass
    fi

    teardown_mocks
}

test_process_exception_result_continue() {
    test_start "process_exception_result: continue execution"

    setup_mocks
    source_aaf_functions

    local prompt="test prompt"
    process_exception_result 1 prompt > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "1" "$exit_code" "Should return 1 for continue"; then
        if assert_equals "請繼續執行原本的任務。" "$prompt" "Should update prompt"; then
            test_pass
        fi
    fi

    teardown_mocks
}

test_execute_with_retry_success() {
    test_start "execute_with_retry: successful execution"

    setup_mocks
    create_mock_claude "success"
    create_mock_jq
    source_aaf_functions

    # 建立 session file
    echo "test-session-123" > "$SESSIONS_DIR/TestAgent_session_id"

    # 建立臨時 raw output
    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"
    echo '{"result": "success"}' > "$LAST_RAW_OUTPUT"

    local prompt="test prompt"
    local allowed_tools=("Read(*)")

    execute_with_retry "test-session-123" prompt "acceptEdits" "TestAgent" allowed_tools > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "0" "$exit_code" "Should return 0 for success"; then
        test_pass
    fi

    teardown_mocks
}

test_execute_with_retry_permission_authorize() {
    test_start "execute_with_retry: permission denied then authorize"

    setup_mocks
    source_aaf_functions

    # Mock claude 會先返回 permission_denied，第二次返回 success
    export MOCK_CALL_COUNT=0
    cat > "$MOCK_DIR/claude" << 'EOF'
#!/bin/bash
echo "claude $@" >> "$MOCK_CALLS_FILE"
MOCK_CALL_COUNT=$((MOCK_CALL_COUNT + 1))
echo "Call count: $MOCK_CALL_COUNT" >> "$MOCK_CALLS_FILE"

if [ "$MOCK_CALL_COUNT" -eq "1" ]; then
    # 第一次調用：返回 permission denied
    cat << JSON
{
    "session_id": "test-session-123",
    "result": "需要權限",
    "permission_denials": [{
        "tool_name": "Edit",
        "tool_input": {"file_path": "/test.txt"}
    }],
    "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    "total_cost_usd": "0.001",
    "duration_ms": 1000
}
JSON
else
    # 第二次調用：返回成功
    cat << JSON
{
    "session_id": "test-session-123",
    "result": "已授權，繼續執行",
    "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    "total_cost_usd": "0.001",
    "duration_ms": 1000
}
JSON
fi
EOF
    chmod +x "$MOCK_DIR/claude"
    create_mock_jq

    echo "test-session-123" > "$SESSIONS_DIR/TestAgent_session_id"
    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"

    local prompt="test prompt"
    local allowed_tools=("Read(*)")

    # 模擬使用者選擇授權 (y)
    echo "y" | execute_with_retry "test-session-123" prompt "acceptEdits" "TestAgent" allowed_tools > /dev/null 2>&1
    local exit_code=$?

    # 第一次遇到權限問題會返回 1（需要重試）
    if assert_equals "1" "$exit_code" "Should return 1 for retry after authorization"; then
        if assert_contains "${allowed_tools[*]}" "Edit(/test.txt)" "Should add authorized tool"; then
            test_pass
        fi
    fi

    teardown_mocks
}

test_execute_with_retry_dialog_complete() {
    test_start "execute_with_retry: dialog mode and task complete"

    setup_mocks
    create_mock_claude "permission_denied"
    create_mock_jq
    source_aaf_functions

    echo "test-session-123" > "$SESSIONS_DIR/TestAgent_session_id"
    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"
    cat > "$LAST_RAW_OUTPUT" << 'JSON'
{
    "permission_denials": [{
        "tool_name": "Edit",
        "tool_input": {"file_path": "/test.txt"}
    }]
}
JSON

    local prompt="test prompt"
    local allowed_tools=("Read(*)")

    # 模擬：選擇對話 (t) → 輸入對話 → END → 結束對話 (n) → 任務完成 (y)
    (echo "t"; echo "完成任務"; echo "END"; echo "n"; echo "y") | \
        execute_with_retry "test-session-123" prompt "acceptEdits" "TestAgent" allowed_tools > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "0" "$exit_code" "Should return 0 when task complete after dialog"; then
        test_pass
    fi

    teardown_mocks
}

test_execute_with_retry_dialog_continue() {
    test_start "execute_with_retry: dialog mode and task continues"

    setup_mocks
    create_mock_claude "permission_denied"
    create_mock_jq
    source_aaf_functions

    echo "test-session-123" > "$SESSIONS_DIR/TestAgent_session_id"
    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"
    cat > "$LAST_RAW_OUTPUT" << 'JSON'
{
    "permission_denials": [{
        "tool_name": "Edit",
        "tool_input": {"file_path": "/test.txt"}
    }]
}
JSON

    local prompt="test prompt"
    local allowed_tools=("Read(*)")

    # 模擬：選擇對話 (t) → 輸入對話 → END → 結束對話 (n) → 任務未完成 (n)
    (echo "t"; echo "還需要繼續"; echo "END"; echo "n"; echo "n") | \
        execute_with_retry "test-session-123" prompt "acceptEdits" "TestAgent" allowed_tools > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "1" "$exit_code" "Should return 1 when task needs to continue"; then
        if assert_equals "請繼續執行原本的任務。" "$prompt" "Should update prompt"; then
            test_pass
        fi
    fi

    teardown_mocks
}

test_execute_with_retry_skip() {
    test_start "execute_with_retry: user chooses to skip stage"

    setup_mocks
    create_mock_claude "permission_denied"
    create_mock_jq
    source_aaf_functions

    echo "test-session-123" > "$SESSIONS_DIR/TestAgent_session_id"
    export LAST_RAW_OUTPUT="$TEST_DIR/last_output.log"
    cat > "$LAST_RAW_OUTPUT" << 'JSON'
{
    "permission_denials": [{
        "tool_name": "Edit",
        "tool_input": {"file_path": "/test.txt"}
    }]
}
JSON

    local prompt="test prompt"
    local allowed_tools=("Read(*)")

    # 模擬：選擇跳過 (s)
    echo "s" | execute_with_retry "test-session-123" prompt "acceptEdits" "TestAgent" allowed_tools > /dev/null 2>&1
    local exit_code=$?

    if assert_equals "0" "$exit_code" "Should return 0 to skip stage"; then
        test_pass
    fi

    teardown_mocks
}

# ============================================================================
# 執行所有測試
# ============================================================================

run_all_tests() {
    echo "========================================"
    echo "Running aaf Test Suite"
    echo "========================================"

    # 權限處理測試
    test_handle_permissions_authorize
    test_handle_permissions_dialog
    test_handle_permissions_skip

    # 例外處理測試
    test_handle_exception_task_complete
    test_handle_exception_task_continue

    # process_exception_result 測試
    test_process_exception_result_complete
    test_process_exception_result_skip
    test_process_exception_result_continue

    # execute_with_retry 測試
    test_execute_with_retry_success
    test_execute_with_retry_permission_authorize
    test_execute_with_retry_dialog_complete
    test_execute_with_retry_dialog_continue
    test_execute_with_retry_skip

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
}

# 執行測試
run_all_tests

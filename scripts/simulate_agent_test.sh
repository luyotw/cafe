#!/bin/bash

# 模擬 agent 在開發過程中執行測試並 commit 的行為

set -e

echo "🤖 模擬 Agent 執行測試..."
echo "當前目錄: $(pwd)"
echo "Git branch: $(git branch --show-current)"

# 顯示環境變數
echo ""
echo "=== 環境變數 ==="
echo "GIT_CEILING_DIRECTORIES: ${GIT_CEILING_DIRECTORIES:-未設置}"
echo "PYTHONPATH: ${PYTHONPATH:-未設置}"

# 執行測試（模擬 agent 在開發時可能執行的操作）
echo ""
echo "=== 執行測試 ==="

# 找到 repo root（支援從 worktree 執行）
# 如果是 worktree，需要找到主 repo 的位置
GIT_COMMON_DIR=$(git rev-parse --git-common-dir)
if [[ "$GIT_COMMON_DIR" == /* ]]; then
    # 絕對路徑：表示在 worktree 中
    REPO_ROOT=$(dirname "$GIT_COMMON_DIR")
else
    # 相對路徑：表示在主 repo 中
    REPO_ROOT=$(git rev-parse --show-toplevel)
fi

export PYTHONPATH="$REPO_ROOT/src:$PYTHONPATH"

# 只執行一個簡單的測試以加快速度
uv run pytest "$REPO_ROOT/tests/unit/test_check_precommit_env.py" -v -s

echo ""
echo "=== 測試完成 ==="

# 檢查是否有污染 commits
echo ""
echo "=== 檢查污染狀況 ==="
echo "檢查 'Initial' 或 'Initial commit' commits："
git --no-pager log --oneline --grep="^Initial" --grep="^Initial commit" -5 || echo "沒有找到污染 commits"

# 創建一個測試檔案並嘗試 commit（這會觸發 pre-commit hook）
echo ""
echo "=== 模擬 Agent Commit ==="
TIMESTAMP=$(date +%s)
TEST_FILE="test_agent_simulation_${TIMESTAMP}.txt"
echo "# Test file created by agent simulation" > "$TEST_FILE"

git add "$TEST_FILE"
echo "準備 commit..."

# 執行 commit（這會觸發 pre-commit hook）
if git commit -m "Test: agent simulation commit" ; then
    echo "✓ Commit 成功"

    # 檢查最新的 commit
    echo ""
    echo "=== 最新 commits ==="
    git --no-pager log --oneline -3

    # 檢查是否產生污染
    echo ""
    echo "=== 檢查是否產生新的污染 commits ==="
    # 檢查 "Initial" 或 "Initial commit" commits
    POLLUTION_COUNT=$(git --no-pager log --oneline --grep="^Initial" --since="1 minute ago" | wc -l)
    if [ "$POLLUTION_COUNT" -gt 0 ]; then
        echo "⚠️⚠️⚠️  偵測到 $POLLUTION_COUNT 個新的污染 commits！"
        git --no-pager log --oneline --grep="^Initial" --since="1 minute ago"
        exit 1
    else
        echo "✓ 沒有產生新的污染 commits"
    fi

    # 清理測試檔案
    git reset --soft HEAD~1
    git restore --staged "$TEST_FILE"
    rm -f "$TEST_FILE"
else
    echo "✗ Commit 失敗（pre-commit hook 可能阻止了）"
    git restore --staged "$TEST_FILE"
    rm -f "$TEST_FILE"
    exit 1
fi

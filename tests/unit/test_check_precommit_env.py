"""檢查 pre-commit hook 執行時環境變數"""
import os
from pathlib import Path


def test_check_git_ceiling_directories():
    """檢查 GIT_CEILING_DIRECTORIES 是否被正確設置"""

    ceiling = os.environ.get('GIT_CEILING_DIRECTORIES')
    cwd = Path.cwd()

    print(f"\n=== Pre-commit Hook 環境檢查 ===")
    print(f"當前工作目錄: {cwd}")
    print(f"GIT_CEILING_DIRECTORIES: {ceiling}")

    if ceiling:
        print(f"✓ GIT_CEILING_DIRECTORIES 已設置")
    else:
        print(f"✗ GIT_CEILING_DIRECTORIES 未設置！")
        print(f"這可能導致測試污染真實 repo！")

    # 檢查是否在 worktree 中
    git_file = cwd / ".git"
    if git_file.exists() and git_file.is_file():
        content = git_file.read_text()
        print(f"✓ 在 worktree 中: {content.strip()}")

    # 這個測試總是通過, 只是為了顯示資訊
    assert True

"""共用的 Phase UI 互動提示函式

此模組提供可重用的 UI 互動函式，用於 spec/plan phase 和 prepare 指令。
"""

from pathlib import Path
from typing import Optional

from cafe.core.types import SpecRigor
from cafe.ui.display import Display
from cafe.utils.github import GitHubOps, GitHubError


def prompt_for_input_method(display: Display, github_ops: GitHubOps) -> tuple[str, Optional[int]]:
    """詢問用戶選擇需求輸入方式（手動 vs GitHub Issue）

    Args:
        display: Display 實例（目前未使用，但保留以便未來擴展）
        github_ops: GitHubOps 實例用於驗證 Issue ID

    Returns:
        Tuple of (method, issue_id):
        - method: "manual" 或 "github"
        - issue_id: Issue ID (int) 如果選擇 GitHub，否則 None
    """
    print("\n" + "="*70)
    print("請選擇需求輸入方式：")
    print("="*70)
    print()
    print("1. 手動輸入需求")
    print("2. 從 GitHub Issue 抓取")
    print()

    while True:
        choice = input("請選擇 (1 或 2): ").strip()

        if choice == "1":
            return ("manual", None)
        elif choice == "2":
            # 先顯示警告
            print()
            print("⚠️  注意：完成後會將 spec.md 以 comment 方式貼回 GitHub Issue")
            print()

            # 詢問 Issue ID 或 URL
            issue_input = input("請輸入 GitHub Issue ID 或 URL: ").strip()

            try:
                # 使用 GitHubOps 提取 issue number
                issue_id_str = github_ops.extract_issue_number(issue_input)
                issue_id = int(issue_id_str)

                print()
                print(f"✓ 將從 GitHub Issue #{issue_id} 抓取需求")
                print()

                return ("github", issue_id)
            except (ValueError, GitHubError) as e:
                print(f"❌ 無效的 Issue ID 或 URL: {e}")
                print("請重新選擇...")
                print()
        else:
            print("❌ 無效選擇，請輸入 1 或 2")


def prompt_for_rigor(display: Display) -> str:
    """詢問 spec 嚴謹程度

    Args:
        display: Display 實例（目前未使用，但保留以便未來擴展）

    Returns:
        嚴謹程度字串：'low' | 'medium' | 'high'
    """
    print("\n" + "="*70)
    print("請選擇規格嚴謹程度：")
    print("="*70)
    print()
    print("1. Low (低) - 快速開發模式")
    print("   • 只問最關鍵的資訊")
    print("   • 允許模糊地帶，讓開發者自行判斷")
    print("   • 適合：快速原型、MVP、內部工具")
    print()
    print("2. Medium (中) - 平衡模式 [預設]")
    print("   • 詢問重要細節和關鍵場景")
    print("   • 在速度和精確度間取得平衡")
    print("   • 適合：一般功能開發")
    print()
    print("3. High (高) - 精確規格模式")
    print("   • 詳細詢問所有細節和邊界情況")
    print("   • 確保需求可測試、無模糊")
    print("   • 適合：核心功能、API 設計、對外產品")
    print()
    print("="*70)

    while True:
        choice = input("請選擇 (1-3, 直接按 Enter 使用預設值 2): ").strip()

        if choice == "" or choice == "2":
            print(f"✓ 已選擇：Medium (中) - 平衡模式\n")
            return "medium"
        elif choice == "1":
            print(f"✓ 已選擇：Low (低) - 快速開發模式\n")
            return "low"
        elif choice == "3":
            print(f"✓ 已選擇：High (高) - 精確規格模式\n")
            return "high"
        else:
            print("❌ 無效選擇，請輸入 1, 2, 或 3")


def fetch_github_issue(github_ops: GitHubOps, issue_id: int) -> str:
    """抓取 GitHub Issue 內容

    Args:
        github_ops: GitHubOps 實例
        issue_id: GitHub issue ID

    Returns:
        合併 title 和 body 的需求文字

    Raises:
        GitHubError: 抓取 issue 失敗
        RuntimeError: gh CLI 未認證
    """
    # Check gh CLI authentication status
    if not github_ops.check_gh_auth():
        raise RuntimeError("gh CLI is not authenticated. Please run: gh auth login")

    issue_data = github_ops.get_issue(str(issue_id), include_comments=False)

    # Combine title and body as requirement content
    issue_title = issue_data.get("title", "")
    issue_body = issue_data.get("body", "")
    fetched_content = f"# {issue_title}\n\n{issue_body}" if issue_title else issue_body

    return fetched_content

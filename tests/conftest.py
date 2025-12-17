"""Global pytest configuration."""

import sys
import shutil
from pathlib import Path
import pytest


def _ensure_src_on_path() -> None:
    """Add this repo's src directory to sys.path for local imports."""
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"

    if src_dir.is_dir():
        src_path = str(src_dir)
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


_ensure_src_on_path()


def create_minimal_config(base_dir: Path) -> None:
    """建立最小的 .cafe/config.yaml 檔案。

    Args:
        base_dir: 基礎目錄，會在此目錄下建立 .cafe/config.yaml
    """
    cafe_dir = base_dir / ".cafe"
    cafe_dir.mkdir(exist_ok=True, parents=True)

    # 從 fixtures 目錄讀取範本
    fixture_file = Path(__file__).parent / "fixtures" / "minimal_config.yaml"
    config_file = cafe_dir / "config.yaml"

    # 複製檔案內容
    config_file.write_text(fixture_file.read_text())


@pytest.fixture(autouse=True, scope="function")
def cleanup_mock_issue_dirs():
    """自動清理測試產生的 MagicMock issue directories。

    當測試使用 mock git_ops 但沒有設定 return_value 時，
    get_current_branch() 會回傳 MagicMock 物件，
    被轉換成字串後會產生 "<MagicMock name='...'>" 格式的目錄名稱。
    這個 fixture 會在每個測試結束後自動清理這些目錄。
    """
    yield

    # 測試執行後清理
    cafe_issues_dir = Path(".cafe/issues")
    if cafe_issues_dir.exists():
        for issue_dir in cafe_issues_dir.iterdir():
            if issue_dir.is_dir() and "MagicMock" in issue_dir.name:
                shutil.rmtree(issue_dir, ignore_errors=True)

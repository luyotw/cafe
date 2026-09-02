"""Global pytest configuration."""

import sys
import shutil
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.git import BranchHealth


_BUILTIN_SKILL_FRONTMATTER_CACHE: dict[Path, dict[str, object]] = {}
_BUILTIN_PLAYBOOK_CACHE: dict[str, object] = {}


def _ensure_src_on_path() -> None:
    """Add this repo's src directory to sys.path for local imports."""
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"

    if src_dir.is_dir():
        src_path = str(src_dir)
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


_ensure_src_on_path()


@pytest.fixture
def cached_builtin_skill_frontmatter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache immutable packaged skill metadata without masking override files."""
    from cafe.skills.loader import SkillLoader

    builtin_root = (
        Path(__file__).resolve().parents[1] / "src" / "cafe" / "data" / "skills"
    ).resolve()
    real_read = SkillLoader._read_skill_frontmatter
    real_discover = SkillLoader._discover_unlocked

    def read(skill_file: Path) -> dict[str, object]:
        resolved = Path(skill_file).resolve()
        if not resolved.is_relative_to(builtin_root):
            return real_read(skill_file)
        if resolved not in _BUILTIN_SKILL_FRONTMATTER_CACHE:
            _BUILTIN_SKILL_FRONTMATTER_CACHE[resolved] = real_read(skill_file)
        return deepcopy(_BUILTIN_SKILL_FRONTMATTER_CACHE[resolved])

    def discover(loader: SkillLoader, *, strict: bool = False):
        if loader._catalog and all(
            entry.source == "builtin" for entry in loader._catalog.values()
        ):
            return sorted(loader._catalog.values(), key=lambda entry: entry.name)
        return real_discover(loader, strict=strict)

    monkeypatch.setattr(
        SkillLoader,
        "_read_skill_frontmatter",
        staticmethod(read),
    )
    monkeypatch.setattr(SkillLoader, "_discover_unlocked", discover)


@pytest.fixture
def cached_builtin_playbook_models(
    cached_builtin_skill_frontmatter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reuse strict-validated packaged playbooks without masking overrides."""
    from cafe.catalogs.resolver import CatalogKind
    from cafe.playbooks.loader import PlaybookLoader

    package_data_root = (
        Path(__file__).resolve().parents[1] / "src" / "cafe" / "data"
    ).resolve()
    if not _BUILTIN_PLAYBOOK_CACHE:
        playbook_root = package_data_root / "playbooks"
        loader = PlaybookLoader(
            project_root=tmp_path / "cache-project",
            global_root=tmp_path / "cache-global",
            builtin_root=package_data_root,
        )
        for playbook_file in sorted(playbook_root.glob("*.yaml")):
            _BUILTIN_PLAYBOOK_CACHE[playbook_file.stem] = loader.load_model(
                playbook_file.stem,
                strict=True,
            )

    real_load_model = PlaybookLoader.load_model

    def load_model(loader: PlaybookLoader, name: str, *, strict: bool = False):
        if loader.builtin_root == package_data_root and name in _BUILTIN_PLAYBOOK_CACHE:
            resolved = loader.resolver.resolve(CatalogKind.PLAYBOOK, name)
            if resolved.source == "builtin":
                return deepcopy(_BUILTIN_PLAYBOOK_CACHE[name])
        return real_load_model(loader, name, strict=strict)

    monkeypatch.setattr(PlaybookLoader, "load_model", load_model)


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


@pytest.fixture(autouse=True)
def _wire_git_branch_health_for_mock_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure MagicMock GitOperations for active issue resolution in workflow tests."""
    from cafe.ui.commands import workflow as workflow_mod

    original_resolve = workflow_mod.resolve_active_issue

    def _resolve_with_mock_health(
        *,
        cafe_dir: Path,
        git_ops: object,
        explicit_issue: str | None = None,
    ):
        if explicit_issue is None and isinstance(git_ops, MagicMock):
            if not isinstance(git_ops.get_branch_health.return_value, BranchHealth):
                branch = git_ops.get_current_branch()
                if isinstance(branch, str) and branch:
                    git_ops.get_branch_health.return_value = BranchHealth(
                        is_healthy=True,
                        branch_name=branch,
                    )
                    (cafe_dir / "issues" / branch).mkdir(parents=True, exist_ok=True)
        return original_resolve(
            cafe_dir=cafe_dir,
            git_ops=git_ops,
            explicit_issue=explicit_issue,
        )

    monkeypatch.setattr(workflow_mod, "resolve_active_issue", _resolve_with_mock_health)


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


@pytest.fixture
def mock_multiline_input():
    """Fixture to mock prompt_multiline() function.

    This centralizes the mocking of multiline input to reduce fragile tests.
    Tests should use this fixture instead of directly mocking implementation details.

    Usage:
        def test_something(mock_multiline_input):
            mock_multiline_input.return_value = "user input"
            # ... test code ...
    """
    with patch('cafe.ui.inquirer_prompts.prompt_multiline') as mock:
        yield mock

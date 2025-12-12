"""測試 Phase base class 的版本化檔案管理方法。"""

import pytest
from pathlib import Path
from cafe.core.phase import Phase
from cafe.core.types import PhaseResult


class ConcretePhase(Phase):
    """Concrete phase for testing base class methods."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.phase_name = "test"

    def execute(self) -> PhaseResult:
        """Dummy implementation."""
        pass


class TestVersionedFileMethods:
    """測試版本化檔案管理的基礎方法。"""

    def test_get_versioned_file_path_basic(self, tmp_path: Path):
        """測試 _get_versioned_file_path() 正確產生檔案路徑。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"

        # Test iteration 1
        file_path = phase._get_versioned_file_path("spec", 1, phase_dir)
        assert file_path == phase_dir / "spec_001.md"

        # Test iteration 42
        file_path = phase._get_versioned_file_path("spec", 42, phase_dir)
        assert file_path == phase_dir / "spec_042.md"

        # Test iteration 999
        file_path = phase._get_versioned_file_path("spec", 999, phase_dir)
        assert file_path == phase_dir / "spec_999.md"

    def test_get_next_iteration_number_empty_dir(self, tmp_path: Path):
        """測試 _get_next_iteration_number() 在空目錄中返回 1。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        iteration = phase._get_next_iteration_number("spec", phase_dir)
        assert iteration == 1

    def test_get_next_iteration_number_no_history_dir(self, tmp_path: Path):
        """測試 _get_next_iteration_number() 在沒有 history 目錄時返回 1。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # Create some output files but no history directory
        (phase_dir / "spec_001.md").touch()
        (phase_dir / "spec_002.md").touch()

        # Should return 1 because there's no history
        iteration = phase._get_next_iteration_number("spec", phase_dir)
        assert iteration == 1

    def test_get_next_iteration_number_with_existing_files(self, tmp_path: Path):
        """測試 _get_next_iteration_number() 基於 iteration history 正確計算編號。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # Create history directory with iteration files
        history_dir = phase_dir / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "iteration_001.json").touch()
        (history_dir / "iteration_002.json").touch()

        iteration = phase._get_next_iteration_number("spec", phase_dir)
        assert iteration == 3

    def test_get_next_iteration_number_exceeds_999(self, tmp_path: Path):
        """測試 _get_next_iteration_number() 在超過 999 時拋出錯誤。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # Create history directory with 999 iteration files
        history_dir = phase_dir / "history"
        history_dir.mkdir(parents=True)
        for i in range(1, 1000):
            (history_dir / f"iteration_{i:03d}.json").touch()

        with pytest.raises(ValueError, match="不可超過 999"):
            phase._get_next_iteration_number("spec", phase_dir)

    def test_get_next_iteration_number_ignores_orphaned_output_files(self, tmp_path: Path):
        """測試 _get_next_iteration_number() 忽略中斷執行時殘留的輸出檔案。

        這個測試驗證當有多餘的輸出檔案（如 plan_002.md, plan_003.md）
        但 iteration history 只有 iteration_001.json 時，版本號應該基於 history 而不是輸出檔案。
        """
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # Create orphaned output files (from interrupted executions)
        (phase_dir / "spec_001.md").touch()
        (phase_dir / "spec_002.md").touch()
        (phase_dir / "spec_003.md").touch()

        # But only have 1 iteration in history
        history_dir = phase_dir / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "iteration_001.json").touch()

        # Should return 2 (based on history), not 4 (based on output files)
        iteration = phase._get_next_iteration_number("spec", phase_dir)
        assert iteration == 2

    def test_get_next_iteration_number_nonexistent_dir(self, tmp_path: Path):
        """測試 _get_next_iteration_number() 在目錄不存在時返回 1。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"  # Directory doesn't exist

        iteration = phase._get_next_iteration_number("spec", phase_dir)
        assert iteration == 1

    def test_copy_previous_version_success(self, tmp_path: Path):
        """測試 _copy_previous_version() 正確複製檔案。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # Create previous version with content
        prev_file = phase_dir / "spec_001.md"
        prev_file.write_text("Previous content", encoding="utf-8")

        # Copy to iteration 2
        phase._copy_previous_version("spec", 2, phase_dir)

        # Check new file exists and has same content
        new_file = phase_dir / "spec_002.md"
        assert new_file.exists()
        assert new_file.read_text(encoding="utf-8") == "Previous content"

    def test_copy_previous_version_first_iteration(self, tmp_path: Path):
        """測試 _copy_previous_version() 在第一輪時不執行任何動作。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # Try to copy for iteration 1 (should do nothing)
        phase._copy_previous_version("spec", 1, phase_dir)

        # No file should be created
        new_file = phase_dir / "spec_001.md"
        assert not new_file.exists()

    def test_copy_previous_version_no_previous_file(self, tmp_path: Path):
        """測試 _copy_previous_version() 在前一版本不存在時不執行任何動作。"""
        phase = ConcretePhase()
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # Try to copy for iteration 2 when iteration 1 doesn't exist
        phase._copy_previous_version("spec", 2, phase_dir)

        # No file should be created
        new_file = phase_dir / "spec_002.md"
        assert not new_file.exists()

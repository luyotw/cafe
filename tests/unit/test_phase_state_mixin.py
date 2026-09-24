"""Tests for PhaseStateMixin helpers (permission inference from agent text)."""

import json

from cafe.core.phase_state_mixin import PhaseStateMixin, next_runnable_iteration_number
from cafe.core.status_codes import PhaseStatusCode


def test_infer_human_input_status_maps_permission_plaintext() -> None:
    response = (
        "請允許寫入 spec 檔案，讓我繼續完成工作流程。\n\n"
        "需要您授權寫入 `.cafe/issues/issue21/spec/` 目錄下的檔案。"
    )
    assert (
        PhaseStateMixin._infer_human_input_status_from_response(response)
        == PhaseStatusCode.NEED_PERMISSION
    )


def test_infer_human_input_status_returns_none_for_unrelated_text() -> None:
    assert PhaseStateMixin._infer_human_input_status_from_response("分析完成，請確認計畫。") is None


def test_untrusted_workflow_completion_reuses_ended_iteration(tmp_path) -> None:
    phase_dir = tmp_path / "develop"
    iteration_dir = phase_dir / "iteration_003"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 3,
                "end_time": "2026-09-12T09:26:54+08:00",
                "workflow_completion_trusted": False,
            }
        ),
        encoding="utf-8",
    )

    assert next_runnable_iteration_number(phase_dir) == 3

"""Tests for PhaseStateMixin helpers (permission inference from agent text)."""

from cafe.core.phase_state_mixin import PhaseStateMixin
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

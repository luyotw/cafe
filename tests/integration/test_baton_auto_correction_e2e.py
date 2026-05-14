"""整合測試：baton auto-correction — agent 寫入無效 to_owner 後 runtime 自動修正並繼續推進。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader


def _load_default_playbook() -> dict:
    return PlaybookLoader().load("default")


def _write_raw_baton(issue_dir: Path, payload: dict) -> None:
    """直接寫入 next_step.txt，模擬 agent 寫出錯誤 baton。"""
    (issue_dir / "next_step.txt").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class TestBatonAutoCorrectionE2E:
    def test_agent_writes_to_owner_human_runtime_autocorrects_and_continues(self, tmp_path: Path) -> None:
        """agent 寫 to_owner='human' 的 baton，runtime 應自動修正為 'user' 並繼續 workflow。

        測試流程：
        1. 初始化 BlackboardWorkflowRuntime 從 spec 步驟開始
        2. spec 步驟執行後，模擬 agent 寫入 to_owner='human' 的 baton（應指向 user）
        3. runtime 下一輪 load_handoff_contract 應自動修正為 to_owner='user'
        4. blackboard 中應出現 baton_auto_corrected 事件，workflow 不報錯並暫停在 user
        """
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-autocorrect-e2e"
        issue_dir.mkdir(parents=True)
        playbook = _load_default_playbook()

        spec_executed = []

        def executor(step_name: str, step_def: dict, state) -> StepExecutionResult:
            if step_name == "spec":
                spec_executed.append(step_name)
                # 模擬 agent 在 spec 完成後寫入 to_owner='human' 的錯誤 baton
                _write_raw_baton(
                    issue_dir,
                    {
                        "version": 1,
                        "from_step": "spec",
                        "to_owner": "human",
                        "to_step": "user",
                        "intent": "need_clarification",
                        "status_code": "",
                        "created_at": "2026-05-14T10:00:00+08:00",
                        "source": "spec.agent",
                    },
                )
            return StepExecutionResult(
                response="confirmed",
                artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
                status_code="confirmed",
                events=[],
            )

        runtime = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )

        # run 應在 user 暫停（不拋出例外）
        result = runtime.run(start_step="spec", max_transitions=5)

        assert not result.completed, "workflow 應暫停在 user，尚未完成"

        store = BlackboardStore(issue_dir)
        state = store.load_or_create("spec")

        # 驗證 spec 步驟確實執行過
        assert "spec" in spec_executed

        # 驗證 baton_auto_corrected 事件已寫入 blackboard
        auto_events = [e for e in state.events if e.event_type == "baton_auto_corrected"]
        assert len(auto_events) >= 1, "應至少有一筆 baton_auto_corrected 事件"

        # 驗證事件 data 同時包含 original 與 corrected 值
        corrections = auto_events[0].data.get("corrections", [])
        assert any(
            c.get("field") == "to_owner" and c.get("original") == "human" and c.get("corrected") == "user"
            for c in corrections
        ), f"corrections 應包含 to_owner human→user 的修正記錄，實際：{corrections}"

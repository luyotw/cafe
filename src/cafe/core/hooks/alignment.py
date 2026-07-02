"""Alignment checkpoint hook."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from cafe.core.alignment import (
    AlignmentDecisionLevel,
    AlignmentPolicyConfig,
    AlignmentPolicyInput,
    evaluate_alignment_policy,
)
from cafe.core.blackboard import BlackboardState, BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.strategic_context import load_strategic_context


class AlignmentCheckpointGate(NoOpHook):
    """Run deterministic alignment policy before agent execution."""

    name = "AlignmentCheckpointGate"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "prepare_input":
            return HookResult()

        step_def = kwargs.get("step_def") or {}
        raw_alignment = step_def.get("alignment")
        if not isinstance(raw_alignment, dict):
            return HookResult()
        if raw_alignment.get("enabled", True) is False:
            return HookResult()
        if raw_alignment.get("trigger_policy", "policy") == "disabled":
            return HookResult()

        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        issue_dir = Path(getattr(phase, "issue_dir", "")) if phase is not None else None
        if phase is None or issue_dir is None or not step_name:
            return HookResult()

        blackboard_state = kwargs.get("blackboard_state")
        if not isinstance(blackboard_state, BlackboardState):
            return HookResult()

        context = kwargs.get("context") if isinstance(kwargs.get("context"), dict) else {}
        user_input = self._resolve_user_input(phase=phase, step_name=step_name, context=context)
        artifacts = self._load_artifacts(
            blackboard_state=blackboard_state,
            step_def=step_def,
            output_file=kwargs.get("output_file"),
        )
        strategic_context = load_strategic_context(Path.cwd(), issue_name=getattr(phase, "issue_name", None))
        config = AlignmentPolicyConfig(
            pause_threshold=int(raw_alignment.get("pause_threshold", 5)),
            note_threshold=int(raw_alignment.get("note_threshold", 2)),
            affected_document_categories=tuple(raw_alignment.get("affected_document_categories", []) or []),
            reuse_approved=bool(raw_alignment.get("reuse_approved", True)),
        )
        result = evaluate_alignment_policy(
            AlignmentPolicyInput(
                step_name=step_name,
                playbook_id=str(getattr(phase, "playbook", {}).get("playbook", {}).get("id", "")),
                user_input=user_input,
                artifacts=artifacts,
            ),
            strategic_context=strategic_context,
            config=config,
        )
        if result.level == AlignmentDecisionLevel.NO_ALIGNMENT or result.payload is None:
            return HookResult()

        store = BlackboardStore(issue_dir)
        payload = result.payload.to_dict()
        payload.update(
            {
                "from_step": step_name,
                "playbook_id": str(getattr(phase, "playbook", {}).get("playbook", {}).get("id", "")),
                "level": result.level.value,
                "score": result.score,
            }
        )

        if result.level == AlignmentDecisionLevel.ALIGNMENT_NOTE:
            store.record_event(
                blackboard_state,
                "alignment_note",
                {"step": step_name, "fingerprint": result.payload.fingerprint, "payload": payload},
            )
            return HookResult(
                events=[
                    {
                        "type": "alignment_note",
                        "step": step_name,
                        "fingerprint": result.payload.fingerprint,
                    }
                ]
            )

        if config.reuse_approved and self._has_unblocking_decision(
            blackboard_state,
            result.payload.fingerprint,
        ):
            store.record_event(
                blackboard_state,
                "alignment_reused",
                {"step": step_name, "fingerprint": result.payload.fingerprint},
            )
            return HookResult(
                events=[
                    {
                        "type": "alignment_reused",
                        "step": step_name,
                        "fingerprint": result.payload.fingerprint,
                    }
                ]
            )

        iteration_dir = Path(kwargs.get("iteration_dir") or issue_dir / step_name / "iteration_001")
        iteration_dir.mkdir(parents=True, exist_ok=True)
        request_file = iteration_dir / "alignment_request.json"
        request_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if payload.get("strategic_document_update_requirements"):
            (iteration_dir / "strategic_document_update_request.json").write_text(
                json.dumps(
                    {
                        "fingerprint": result.payload.fingerprint,
                        "from_step": step_name,
                        "requirements": payload["strategic_document_update_requirements"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        store.set_current_step(blackboard_state, "user")
        store.set_handoff_summary(blackboard_state, f"Alignment checkpoint required before {step_name}")
        store.update_handoff_contract(
            blackboard_state,
            from_step=step_name,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
            status_code=PhaseStatusCode.ALIGNMENT_CHECKPOINT.value,
            source="alignment.checkpoint_gate",
        )
        store.record_event(
            blackboard_state,
            "alignment_checkpoint_required",
            {
                "step": step_name,
                "fingerprint": result.payload.fingerprint,
                "request_file": str(request_file),
                "payload": payload,
            },
        )
        return HookResult(
            continue_pipeline=False,
            artifact_ready=False,
            override_status_code=PhaseStatusCode.ALIGNMENT_CHECKPOINT,
            events=[
                {
                    "type": "alignment_checkpoint_required",
                    "step": step_name,
                    "fingerprint": result.payload.fingerprint,
                    "request_file": str(request_file),
                }
            ],
        )

    @staticmethod
    def _resolve_user_input(*, phase: Any, step_name: str, context: Dict[str, str]) -> str:
        resolver = getattr(phase, "_get_resolved_iteration_user_input", None)
        if callable(resolver):
            try:
                return str(resolver(step_name) or "")
            except Exception:
                pass
        return str(context.get("user_input") or "")

    @staticmethod
    def _load_artifacts(
        *,
        blackboard_state: BlackboardState,
        step_def: Dict[str, Any],
        output_file: Any,
    ) -> Dict[str, str]:
        artifacts: Dict[str, str] = {}
        for name in step_def.get("input_artifacts", []) or []:
            entry = blackboard_state.artifacts.get(str(name))
            if entry is not None:
                artifacts[str(name)] = _read_text_sample(Path(entry.path))
        if output_file is not None:
            artifacts["current_output"] = _read_text_sample(Path(output_file))
        return {key: value for key, value in artifacts.items() if value}

    @staticmethod
    def _has_unblocking_decision(blackboard_state: BlackboardState, fingerprint: str) -> bool:
        for event in reversed(blackboard_state.events):
            if event.event_type != "alignment_decision":
                continue
            data = event.data or {}
            if data.get("fingerprint") == fingerprint and data.get("unblocks_execution") is True:
                return True
        return False


def _read_text_sample(path: Path, limit: int = 12000) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""

"""Delivery-owned action review, operation execution and outcome presentation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.hooks.feedback import _register_artifact
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import resolve_step_human_task
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.core.status_codes import PhaseStatusCode
from cafe.delivery.contracts import ActionProposal, DeliveryBinding, FollowUp, ReviewSource, digest
from cafe.delivery.operations import Commands
from cafe.delivery.selection import approved_snapshot, save_shown_proposal, validate_complete_report
from cafe.skills.loader import SkillLoader


def _binding(kwargs):
    return DeliveryBinding.model_validate(kwargs["step_def"]["delivery"])


def _task(kwargs, prompt, *, trigger="confirm_output"):
    phase = kwargs["phase"]
    state = kwargs["blackboard_state"]
    step = kwargs["step_name"]
    policy, binding = resolve_step_human_task(
        playbook_data=phase.playbook,
        step_name=step,
        trigger=trigger,
        iteration=phase.iteration,
        skill_loader=SkillLoader(project_root=Path(phase.git_ops.repo_path)),
    )
    locale = getattr(state, "conversation_locale", "en-US")
    policy = policy.for_locale(locale)
    records = HumanTaskRecordStore(phase.issue_dir)
    existing = [
        t
        for t in records.tasks()
        if t.workflow_id == state.workflow_id
        and t.step == step
        and t.iteration == phase.iteration
        and t.policy_id == policy.id
        and t.status == HumanTaskStatus.PENDING
    ]
    shown_prompt = policy.prompt + "\n\n" + prompt
    if existing:
        if existing[-1].prompt != shown_prompt:
            raise ValueError("pending task no longer matches shown action evidence")
        return existing[-1]
    return records.materialize(
        workflow_id=state.workflow_id,
        step=step,
        iteration=phase.iteration,
        trigger=trigger,
        policy_id=policy.id,
        prompt=shown_prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )


def _request_clarification(kwargs):
    """Replace the agent's stale success intent when host delivery validation fails."""
    atomic_write_bytes(
        kwargs["phase"].issue_dir / "next_step.txt",
        canonical_json({
            "version": 1, "to_owner": "user", "to_step": "user",
            "intent": "need_clarification",
        }),
    )


def _proposals(state, binding, issue_dir):
    entry = state.artifacts.get(binding.proposals_artifact) if binding.proposals_artifact else None
    if entry is None:
        if binding.proposals_artifact:
            raise ValueError("declared Review proposal artifact is missing")
        return (), None
    path = Path(entry.path).resolve()
    if not path.is_relative_to(issue_dir.resolve()) or path.stat().st_size > 1024 * 1024:
        raise ValueError("Review source must be a bounded workflow artifact")
    content = path.read_bytes()
    source = ReviewSource(
        artifact=binding.proposals_artifact,
        path=str(path.relative_to(issue_dir.resolve())),
        version=entry.version,
        producer=entry.updated_by,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    text = content.decode()
    section = re.search(r"(?ms)^## Follow-up Proposals\s*\n(.*?)(?=^## |\Z)", text)
    if not section or not re.search(r"FUP-[0-9]{3}", section[1]):
        return (), source
    block = re.search(r"```json\s*(.*?)\s*```", section[1], re.S)
    if not block:
        raise ValueError("open proposals require the Review's original structured draft bundle")
    rows = json.loads(block[1])["proposals"]
    selected = tuple(FollowUp.model_validate(row) for row in rows)
    if set(re.findall(r"FUP-[0-9]{3}", section[1])) != {p.id for p in selected}:
        raise ValueError("Review draft IDs do not match open proposal evidence")
    return selected, source


class DevelopmentActionContext(NoOpHook):
    name = "DevelopmentActionContext"

    def run(self, **kwargs):
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        from cafe.core.hooks.native import _publish_requested

        phase = kwargs["phase"]
        if not _publish_requested(
            phase=phase,
            step_name=kwargs["step_name"],
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
            step_def=kwargs["step_def"],
        ):
            return HookResult()
        binding = _binding(kwargs)
        state = kwargs["blackboard_state"]
        root = Path(phase.git_ops.repo_path).resolve()
        output = kwargs["output_file"]
        try:
            request = json.loads((output.parent / "delivery_request.json").read_text())
            allowed = {"mode", "strategy", "target_branch", "destination", "issue_repository"}
            if set(request) - allowed:
                raise ValueError("action request contains unbound fields")
            commands = Commands(30)
            source = commands.git(root, "rev-parse", "HEAD")
            branch = commands.git(root, "symbolic-ref", "--short", "HEAD")
            mode = request["mode"]
            publication_url = ""
            if mode == "local":
                destination = str(Path(request["destination"]).resolve(strict=True))
                repository = commands.git(
                    root, "rev-parse", "--path-format=absolute", "--git-common-dir"
                )
                target = commands.git(Path(destination), "rev-parse", "HEAD")
                number = None
                if (
                    commands.git(Path(destination), "symbolic-ref", "--short", "HEAD")
                    != request["target_branch"]
                ):
                    raise ValueError("requested destination does not contain the target branch")
            else:
                if kwargs.get("validated_pr_auto_create") is False:
                    raise ValueError("local-only publication cannot authorize GitHub PR merge")
                remote = commands.git(root, "remote", "get-url", "origin").removesuffix(".git")
                match = re.fullmatch(
                    r"(?:https://github.com/|git@github.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
                    remote,
                )
                if not match:
                    raise ValueError("GitHub repository identity is unavailable")
                repository = match[1]
                number = int((kwargs.get("context") or {}).get("pr_number", "0"))
                pr = commands.api(f"repos/{repository}/pulls/{number}")
                if (
                    pr["head"]["sha"] != source
                    or pr["head"]["ref"] != branch
                    or pr["base"]["ref"] != request["target_branch"]
                ):
                    raise ValueError("published PR differs from reviewed source/target")
                expected_url = f"https://github.com/{repository}/pull/{number}"
                publication_url = str(
                    (kwargs.get("context") or {}).get("pr_url")
                    or pr.get("html_url")
                    or expected_url
                ).strip()
                if publication_url != expected_url:
                    raise ValueError("published PR link differs from the reviewed PR identity")
                target = pr["base"]["sha"]
                destination = ""
            proposals, review_source = _proposals(state, binding, phase.issue_dir)
            proposal = ActionProposal(
                workflow_id=state.workflow_id,
                approval_step=binding.approval_step,
                approval_iteration=phase.iteration,
                repository=repository,
                source_oid=source,
                source_branch=branch,
                target_oid=target,
                target_branch=request["target_branch"],
                mode=mode,
                strategy=request["strategy"],
                destination=destination,
                pr_number=number,
                issue_repository=request.get("issue_repository", ""),
                proposals=proposals,
                review_source=review_source,
                reviewed_artifact=str(output.resolve().relative_to(phase.issue_dir.resolve())),
                reviewed_artifact_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            )
            from cafe.core.runtime_locales import render_text

            owner = SkillLoader(project_root=root).get_skill_dir(kwargs["step_def"]["skill"])
            locale = getattr(state, "conversation_locale", "en-US")
            shown = render_text(
                "human_task.cafe_pr.delivery_bundle",
                locale=locale,
                catalog_root=owner / "locales",
                repository=repository,
                source=f"{branch}@{source}",
                target=f"{proposal.target_branch}@{target}",
                strategy=proposal.strategy,
                destination=str(destination or number),
                issues=proposal.issue_repository or "∅",
            )
            if publication_url:
                shown += "\n\n" + render_text(
                    "human_task.cafe_pr.delivery_publication",
                    locale=locale,
                    catalog_root=owner / "locales",
                    url=publication_url,
                )
            if proposal.review_source:
                shown += "\n\n" + render_text(
                    "human_task.cafe_pr.delivery_review_source",
                    locale=locale,
                    catalog_root=owner / "locales",
                    **proposal.review_source.model_dump(),
                )
            for item in proposal.proposals:
                shown += "\n\n" + render_text(
                    "human_task.cafe_pr.delivery_draft",
                    locale=locale,
                    catalog_root=owner / "locales",
                    id=item.id,
                    title=item.title,
                    body=item.body,
                    evidence=item.evidence,
                    evidence_head=item.evidence_head,
                    impact=item.impact,
                    confidence=item.confidence,
                )
            shown += f"\n\nAction proposal SHA256: {proposal.digest}"
            task = _task(kwargs, shown)
            save_shown_proposal(phase.issue_dir, task, proposal)
            return HookResult(context_updates={"delivery_action_review_task": task.id})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            output.write_text(
                output.read_text() + f"\n\n## Delivery action details required\n\n{str(exc)[:1024]}\n"
            )
            _request_clarification(kwargs)
            return HookResult(
                continue_pipeline=False,
                override_status_code=PhaseStatusCode.NEED_CLARIFICATION,
                context_updates={"delivery_action_error": str(exc)[:1024]},
            )


class DevelopmentDeliveryExecutor(NoOpHook):
    name = "DevelopmentDeliveryExecutor"

    def run(self, **kwargs):
        if kwargs.get("stage") != "prepare_input":
            return HookResult()
        from cafe.core.capabilities import (
            default_capability_definition_dirs,
            load_capability_registry,
        )
        from cafe.delivery.service import execute_snapshot

        phase = kwargs["phase"]
        binding = _binding(kwargs)
        state = kwargs["blackboard_state"]
        root = Path(phase.git_ops.repo_path).resolve()
        try:
            snapshot = approved_snapshot(
                phase.issue_dir, workflow_id=state.workflow_id, binding=binding
            )
            actions_path = phase.issue_dir / "delivery" / snapshot.digest / "actions.json"
            atomic_write_bytes(actions_path, canonical_json(snapshot.model_dump(mode="json")))
            _register_artifact(
                phase=phase,
                blackboard_state=state,
                name=binding.actions_artifact,
                path=actions_path,
                updated_by=kwargs["step_name"],
            )
            report, path = execute_snapshot(
                root=root,
                issue_dir=phase.issue_dir,
                snapshot=snapshot,
                registry=load_capability_registry(default_capability_definition_dirs(root)),
                step=kwargs["step_name"],
                iteration=phase.iteration,
                output_file=kwargs["output_file"],
                timeout=120,
            )
            updates = {
                "delivery_receipts_file": str(path),
                "delivery_complete": str(report["complete"]).lower(),
                "delivery_actions_file": str(actions_path),
            }
            from cafe.core.runtime_locales import render_text

            owner = SkillLoader(project_root=root).get_skill_dir(kwargs["step_def"]["skill"])
            evidence_prompt = render_text(
                "human_task.delivery.runtime_evidence",
                locale=getattr(state, "conversation_locale", "en-US"),
                catalog_root=owner / "locales",
                actions=str(actions_path),
                receipts=str(path),
                complete=updates["delivery_complete"],
            )
            updates["continuation_prompt"] = "\n\n".join(
                filter(
                    None,
                    [(kwargs.get("context") or {}).get("continuation_prompt", ""), evidence_prompt],
                )
            )
            if report["pending_task"]:
                kwargs["output_file"].write_text(
                    "# Delivery awaits host approval\n\n" + json.dumps(report, indent=2)
                )
                return HookResult(
                    continue_pipeline=False,
                    override_status_code=PhaseStatusCode.NEED_PERMISSION,
                    context_updates=updates,
                    events=[
                        {"type": "capability_approval_pending", "task_id": report["pending_task"]}
                    ],
                )
            return HookResult(context_updates=updates)
        except (OSError, ValueError, KeyError) as exc:
            kwargs["output_file"].write_text(f"# Delivery recovery required\n\n{str(exc)[:1024]}\n")
            return HookResult(
                continue_pipeline=False, override_status_code=PhaseStatusCode.NEED_CLARIFICATION
            )


class DevelopmentDeliveryOutcome(NoOpHook):
    name = "DevelopmentDeliveryOutcome"

    def run(self, **kwargs):
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs["phase"]
        try:
            baton = json.loads((phase.issue_dir / "next_step.txt").read_text())
        except (OSError, ValueError):
            return HookResult()
        if baton.get("intent") != "confirm_output":
            return HookResult()
        try:
            binding = _binding(kwargs)
            snapshot = approved_snapshot(
                phase.issue_dir, workflow_id=kwargs["blackboard_state"].workflow_id, binding=binding
            )
            path = phase.issue_dir / "delivery" / snapshot.digest / "result.json"
            report = json.loads(path.read_text())
            validate_complete_report(phase.issue_dir, snapshot, report)
            _task(
                kwargs,
                f"Delivery result SHA256: {digest(report)}\n\n"
                + json.dumps(report, ensure_ascii=False, indent=2),
            )
            return HookResult(context_updates={"delivery_receipts_file": str(path)})
        except (OSError, ValueError, KeyError) as exc:
            kwargs["output_file"].write_text(f"# Delivery recovery required\n\n{str(exc)[:1024]}\n")
            _request_clarification(kwargs)
            return HookResult(
                continue_pipeline=False, override_status_code=PhaseStatusCode.NEED_CLARIFICATION
            )

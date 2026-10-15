"""Shared immutable delivery review and result presentation, without a phase invocation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import resolve_step_human_task
from cafe.delivery.contracts import ActionProposal, FollowUp, ReviewSource, digest
from cafe.delivery.operations import Commands
from cafe.delivery.selection import save_shown_proposal, approved_snapshot, validate_complete_report
from cafe.skills.loader import SkillLoader


@dataclass(frozen=True)
class DeliveryPresentation:
    issue_dir: Path
    root: Path
    playbook: dict
    state: object
    step: str
    iteration: int
    output: Path

    @classmethod
    def from_hook(cls, kwargs):
        phase = kwargs["phase"]
        return cls(
            phase.issue_dir,
            Path(phase.git_ops.repo_path).resolve(),
            phase.playbook,
            kwargs["blackboard_state"],
            kwargs["step_name"],
            phase.iteration,
            kwargs["output_file"],
        )

    @property
    def step_def(self):
        return self.playbook["steps"][self.step]


def materialize_task(ctx, prompt, *, trigger="confirm_output"):
    state = ctx.state
    step = ctx.step
    policy, binding = resolve_step_human_task(
        playbook_data=ctx.playbook,
        step_name=step,
        trigger=trigger,
        iteration=ctx.iteration,
        skill_loader=SkillLoader(project_root=ctx.root),
    )
    locale = getattr(state, "conversation_locale", "en-US")
    policy = policy.for_locale(locale)
    records = HumanTaskRecordStore(ctx.issue_dir)
    existing = [
        t
        for t in records.tasks()
        if t.workflow_id == state.workflow_id
        and t.step == step
        and t.iteration == ctx.iteration
        and t.policy_id == policy.id
        and t.status == HumanTaskStatus.PENDING
    ]
    shown_prompt = policy.prompt + "\n\n" + prompt
    superseded = []
    if existing:
        if existing[-1].prompt != shown_prompt:
            from cafe.delivery.closeout import read_result_contract

            reference = read_result_contract(ctx.issue_dir, state.workflow_id)
            # A user-reconfirmed split contract can supersede the old combined
            # acceptance while retaining its prompt and all integration receipts.
            prior = existing[-1]
            markers = re.findall(
                r"(?:Action snapshot|Delivery result) SHA256: [0-9a-f]{64}", prompt
            )
            if (
                trigger != "confirm_output"
                or reference is None
                or reference["step"] != step
                or reference["task_id"] != policy.id
                or "Closeout plan SHA256:" not in prior.prompt
                or len(markers) != 2
                or any(marker not in prior.prompt for marker in markers)
            ):
                raise ValueError("pending task no longer matches shown action evidence")
            superseded = [t.id for t in existing]
        else:
            return existing[-1]
    return records.materialize(
        workflow_id=state.workflow_id,
        step=step,
        iteration=ctx.iteration,
        trigger=trigger,
        policy_id=policy.id,
        prompt=shown_prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
        handoff_key=(
            f"delivery-result:{state.workflow_id}:{step}:{ctx.iteration}:{digest(shown_prompt)}"
            if superseded
            else None
        ),
        superseded_task_ids=superseded,
    )


def _proposal_table_cells(line):
    """Split Markdown separators while retaining escaped pipes in cell text."""
    cells, escaped = [""], False
    for char in line.strip():
        if char == "|" and not escaped:
            cells.append("")
        else:
            cells[-1] += char
        escaped = not escaped if char == "\\" else False
    if len(cells) == 1:
        return None
    if not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return [cell.strip().strip("`* ") for cell in cells]


def _open_proposal_evidence_ids(section):
    """Use explicit table status, not incidental narrative finding references."""
    lines = re.sub(r"```.*?```", "", section, flags=re.S).splitlines()
    statuses, has_status_table, index = {}, False, 0
    while index < len(lines):
        cells = _proposal_table_cells(lines[index])
        index += 1
        header = [cell.casefold() for cell in cells] if cells else []
        if "id" not in header or "status" not in header:
            continue
        if header.count("id") != 1 or header.count("status") != 1:
            raise ValueError("Review proposal table has ambiguous columns")
        separator = _proposal_table_cells(lines[index]) if index < len(lines) else None
        if (
            not separator
            or len(separator) != len(header)
            or any(not re.fullmatch(r":?-+:?", cell) for cell in separator)
        ):
            raise ValueError("Review proposal table separator is invalid")
        index += 1
        has_status_table = True
        while index < len(lines):
            row = _proposal_table_cells(lines[index])
            if row is None:
                break
            index += 1
            if len(row) != len(header):
                raise ValueError("Review proposal table has ambiguous columns")
            identities = set(re.findall(r"FUP-[0-9]{3}", row[header.index("id")]))
            if len(identities) != 1:
                raise ValueError("Review proposal identity is ambiguous")
            identity = identities.pop()
            status = row[header.index("status")].casefold()
            if status not in {"open", "fixed", "closed"} or identity in statuses:
                raise ValueError("Review proposal status is ambiguous")
            statuses[identity] = status
    if has_status_table:
        return {identity for identity, status in statuses.items() if status == "open"}
    # Preserve the existing JSON-only/legacy evidence contract.
    return set(re.findall(r"FUP-[0-9]{3}", section))


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
    if not section:
        return (), source
    evidence_ids = _open_proposal_evidence_ids(section[1])
    block = re.search(r"```json\s*(.*?)\s*```", section[1], re.S)
    if not block:
        if not evidence_ids and not re.search(r"FUP-[0-9]{3}", section[1]):
            return (), source
        raise ValueError("open proposals require the Review's original structured draft bundle")
    rows = json.loads(block[1])["proposals"]
    selected = tuple(FollowUp.model_validate(row) for row in rows)
    if evidence_ids != {p.id for p in selected}:
        raise ValueError("Review draft IDs do not match open proposal evidence")
    return selected, source


def present_action(
    ctx, binding, *, trigger="need_permission", context=None, validated_pr_auto_create=None
):
    state = ctx.state
    root = ctx.root
    output = ctx.output
    from cafe.delivery.prepared import prepared_request

    request = prepared_request(ctx.issue_dir, ctx.step)
    if request is None:
        request = json.loads((output.parent / "delivery_request.json").read_text())
    allowed = {
        "mode",
        "strategy",
        "target_branch",
        "destination",
        "issue_repository",
        "verification",
    }
    if set(request) - allowed:
        raise ValueError("action request contains unbound fields")
    from cafe.delivery.contracts import DeliveryVerification

    verification = DeliveryVerification.model_validate(request["verification"])
    reviewed = output
    if binding.publication_artifact:
        entry = state.artifacts.get(binding.publication_artifact)
        if entry is None:
            raise ValueError("declared PR publication artifact is missing")
        reviewed = Path(entry.path).resolve()
        if (
            not reviewed.is_relative_to(ctx.issue_dir.resolve())
            or reviewed.stat().st_size > 1024 * 1024
        ):
            raise ValueError("PR publication must be a bounded workflow artifact")
    commands = Commands(30)
    source = commands.git(root, "rev-parse", "HEAD")
    branch = commands.git(root, "symbolic-ref", "--short", "HEAD")
    mode = request["mode"]
    publication_url = ""
    if mode == "local":
        destination = str(Path(request["destination"]).resolve(strict=True))
        repository = commands.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        target = commands.git(Path(destination), "rev-parse", "HEAD")
        number = None
        if (
            commands.git(Path(destination), "symbolic-ref", "--short", "HEAD")
            != request["target_branch"]
        ):
            raise ValueError("requested destination does not contain the target branch")
    else:
        if validated_pr_auto_create is False:
            raise ValueError("local-only publication cannot authorize GitHub PR merge")
        remote = commands.git(root, "remote", "get-url", "origin").removesuffix(".git")
        match = re.fullmatch(
            r"(?:https://github.com/|git@github.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
            remote,
        )
        if not match:
            raise ValueError("GitHub repository identity is unavailable")
        repository = match[1]
        if binding.publication_artifact:
            from urllib.parse import urlencode

            query = urlencode(
                {
                    "state": "all",
                    "head": f"{repository.split('/')[0]}:{branch}",
                    "base": request["target_branch"],
                    "per_page": 100,
                }
            )
            matches = commands.api(f"repos/{repository}/pulls?{query}")
            if not isinstance(matches, list) or len(matches) >= 100:
                raise ValueError("published PR lookup is unavailable or truncated")
            matches = [
                row
                for row in matches
                if (
                    row["head"]["sha"] == source
                    and row["head"]["ref"] == branch
                    and row["base"]["ref"] == request["target_branch"]
                    and row["base"]["repo"]["full_name"] == repository
                )
            ]
            opened = [row for row in matches if row["state"] == "open"]
            matches = opened or [
                row for row in matches if row.get("merged_at") or row.get("merged")
            ]
            if len(matches) != 1:
                raise ValueError("published PR identity is missing or ambiguous")
            number = matches[0]["number"]
        else:
            number = int((context or {}).get("pr_number", "0"))
        pr = commands.api(f"repos/{repository}/pulls/{number}")
        if pr["state"] != "open" and pr.get("merged") is not True:
            raise ValueError("published PR is closed without integration")
        if (
            pr["head"]["sha"] != source
            or pr["head"]["ref"] != branch
            or pr["base"]["ref"] != request["target_branch"]
        ):
            raise ValueError("published PR differs from reviewed source/target")
        expected_url = f"https://github.com/{repository}/pull/{number}"
        publication_url = str(
            (context or {}).get("pr_url") or pr.get("html_url") or expected_url
        ).strip()
        if publication_url != expected_url:
            raise ValueError("published PR link differs from the reviewed PR identity")
        target = pr["base"]["sha"]
        destination = ""
    proposals, review_source = _proposals(state, binding, ctx.issue_dir)
    proposal = ActionProposal(
        workflow_id=state.workflow_id,
        approval_step=binding.approval_step,
        approval_iteration=ctx.iteration,
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
        reviewed_artifact=str(reviewed.resolve().relative_to(ctx.issue_dir.resolve())),
        reviewed_artifact_sha256=hashlib.sha256(reviewed.read_bytes()).hexdigest(),
        verification=verification,
    )
    from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
    from cafe.delivery.approvals import collect_review, review_text

    registry = load_capability_registry(default_capability_definition_dirs(root))
    proposal = proposal.model_copy(
        update={
            "capability_review": collect_review(proposal, ctx.issue_dir, registry),
        }
    )
    from cafe.core.runtime_locales import render_text

    owner = SkillLoader(project_root=root).get_skill_dir(ctx.step_def["skill"])
    locale = getattr(state, "conversation_locale", "en-US")
    prefix = "human_task.delivery" if binding.publication_artifact else "human_task.cafe_pr"
    shown = render_text(
        prefix + ".delivery_bundle",
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
            prefix + ".delivery_publication",
            locale=locale,
            catalog_root=owner / "locales",
            url=publication_url,
        )
    if proposal.review_source:
        shown += "\n\n" + render_text(
            prefix + ".delivery_review_source",
            locale=locale,
            catalog_root=owner / "locales",
            **proposal.review_source.model_dump(),
        )
    for item in proposal.proposals:
        shown += "\n\n" + render_text(
            prefix + ".delivery_draft",
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
    if proposal.capability_review is not None:
        shown += "\n\n" + review_text(proposal.capability_review)
    shown += "\n\nPost-integration verification:\n" + json.dumps(
        verification.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    shown += f"\n\nAction proposal SHA256: {proposal.digest}"
    task = materialize_task(ctx, shown, trigger=trigger)
    save_shown_proposal(ctx.issue_dir, task, proposal)
    return task


def present_outcome(ctx, binding):
    snapshot = approved_snapshot(ctx.issue_dir, workflow_id=ctx.state.workflow_id, binding=binding)
    path = ctx.issue_dir / "delivery" / snapshot.digest / "result.json"
    report = json.loads(path.read_text())
    validate_complete_report(ctx.issue_dir, snapshot, report)
    from cafe.core.runtime_locales import render_text

    owner = SkillLoader(project_root=ctx.root).get_skill_dir(ctx.step_def["skill"])
    summary = render_text(
        "human_task.delivery.result_summary",
        locale=getattr(ctx.state, "conversation_locale", "en-US"),
        catalog_root=owner / "locales",
        repository=snapshot.proposal.repository,
        source=snapshot.proposal.source_oid,
        target=snapshot.proposal.target_branch,
        strategy=snapshot.proposal.strategy,
        commit=report["actions"]["integration"]["commit"],
    )
    evidence = (
        f"Action snapshot SHA256: {snapshot.digest}\n"
        f"Delivery result SHA256: {digest(report)}\n\n"
        + summary
        + "\n\n"
        + json.dumps(report, ensure_ascii=False, indent=2)
    )
    # A pending combined legacy task owns its original display/schema.
    # Reuse it only while the exact result and terminal plan still match.
    from cafe.delivery.closeout import retained_combined_task
    from cafe.ui.human_tasks import _task_machine_contract
    from cafe.core.human_tasks import HumanTaskPolicy

    records = HumanTaskRecordStore(ctx.issue_dir)
    policy, task_binding = resolve_step_human_task(
        playbook_data=ctx.playbook,
        step_name=ctx.step,
        trigger="confirm_output",
        iteration=ctx.iteration,
        skill_loader=SkillLoader(project_root=ctx.root),
    )
    for task in reversed(records.tasks()):
        if (
            task.workflow_id != snapshot.proposal.workflow_id
            or task.step != ctx.step
            or task.status != HumanTaskStatus.PENDING
            or task.iteration != ctx.iteration
            or task.policy_id != binding.result_task
            or f"Action snapshot SHA256: {snapshot.digest}" not in task.prompt
            or f"Delivery result SHA256: {digest(report)}" not in task.prompt
        ):
            continue
        saved = HumanTaskPolicy.model_validate(task.expected_result)
        if (
            _task_machine_contract(saved) == _task_machine_contract(policy)
            and task.continuations == task_binding.outcomes
        ):
            return task  # A formatter upgrade cannot rewrite already shown acceptance.
        if retained_combined_task(
            ctx.issue_dir, ctx.step_def, task, saved, policy, _task_machine_contract
        ):
            return task
    task = materialize_task(ctx, evidence)
    return task

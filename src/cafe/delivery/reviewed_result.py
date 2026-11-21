"""Materialize the clean revision and source evidence bound to an independent review."""

from __future__ import annotations

import hashlib
import json

from cafe.core.automatic_steps import AutomaticExecutionResult
from cafe.core.blackboard import BlackboardStore
from cafe.core.packet_io import atomic_write_bytes
from cafe.core.workspace_artifact import WorkspaceArtifact, verify_workspace_artifact
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.delivery.automatic import _presentation, validate_inputs
from cafe.core.runtime_locales import render_text
from cafe.delivery.local_result import MAX_EVIDENCE_BYTES, read_bounded
from cafe.delivery.presentation import materialize_task
from cafe.utils.issue_paths import issue_project_root


def _document(issue, root, entry):
    path = (root / entry.path).resolve(strict=True)
    path.relative_to(issue.resolve())
    content = read_bounded(path)
    if not content.strip() or hashlib.sha256(content).hexdigest() != entry.content_sha256:
        raise ValueError(f"published evidence changed: {entry.name}")
    return content.decode("utf-8")


def materials(issue, state, step, definition, *, locale=None):
    root = issue_project_root(issue.resolve())
    binding = definition["local_review"]
    reviewer = binding["review_step"]
    audit = BlackboardStore(issue).audit
    edge = audit.latest_record(state.workflow_id, {"transition"}, bounded=True)
    if (
        not edge
        or edge["data"].get("from") != reviewer
        or edge["data"].get("to") != step
        or edge["data"].get("transition_intent") != "await_agent"
    ):
        raise ValueError("no passing independent review transition for this endpoint")
    started = audit.latest_record(state.workflow_id, {"step_started"}, step=reviewer, bounded=True)
    completed = audit.latest_record(
        state.workflow_id, {"step_completed", "single_step_completed"}, step=reviewer, bounded=True
    )
    if (
        not started
        or not completed
        or not started["sequence"] < completed["sequence"] < edge["sequence"]
        or not completed["data"].get("agent_invoked")
        or completed["data"].get("attempt") != started["data"].get("attempt")
    ):
        raise ValueError("independent review completion is unavailable")
    inputs = started["data"].get("consumed_inputs", {})
    author_name, review_name, workspace_name = definition["input_artifacts"]
    author = state.artifacts[author_name]
    review = state.artifacts[review_name]
    checkout = state.artifacts[workspace_name]
    published = edge["data"].get("source_artifact") or {}
    if (
        published.get("name") != review_name
        or published.get("version") != review.version
        or published.get("content_sha256") != review.content_sha256
        or review.updated_by != reviewer
        or published.get("updated_by") != reviewer
        or author.updated_by != binding["implementation_step"]
    ):
        raise ValueError("independent review or implementation evidence was superseded")
    for entry in (author, checkout):
        ref = inputs.get(entry.name)
        if ref != {
            "version": entry.version,
            "producer": entry.updated_by,
            "digest": entry.content_sha256,
            "head": entry.head_sha,
            "base": entry.base_sha,
        }:
            raise ValueError(f"review input changed: {entry.name}")
    path = (root / checkout.path).resolve(strict=True)
    path.relative_to(issue.resolve())
    content = read_bounded(path)
    if checkout.content_sha256 and hashlib.sha256(content).hexdigest() != checkout.content_sha256:
        raise ValueError("published workspace evidence changed")
    workspace = WorkspaceArtifact.from_dict(json.loads(content))
    if (
        workspace.head_sha != checkout.head_sha
        or workspace.base_sha != checkout.base_sha
        or workspace.producer_step != checkout.updated_by
        or workspace.name != checkout.name
        or workspace.version != checkout.version
    ):
        raise ValueError("reviewed workspace identity changed")
    verification = verify_workspace_artifact(workspace, repo=root)
    if not verification.valid:
        raise ValueError(", ".join(verification.reasons))
    def copy(key, **values):
        return render_text("reviewed_result." + key, locale=locale or state.conversation_locale, **values)

    lines = [
        "# " + copy("title"), "", copy("workflow", workflow=state.workflow_id), "",
        copy("revision", head=workspace.head_sha, base=workspace.base_sha),
        copy("review", event=edge["event_id"]), "", "## " + copy("files"), "",
    ]
    lines.extend("- " + json.dumps(item, ensure_ascii=False) for item in workspace.changed_files)
    for title, entry in (
        (copy("implementation"), author),
        (copy("limits"), review),
    ):
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                f"{entry.name} v{entry.version} · SHA-256 `{entry.content_sha256}`",
                "",
                _document(issue, root, entry).strip(),
            ]
        )
    return "\n".join(lines) + "\n"


def execute(inputs, *, context):
    validate_inputs(inputs)
    ctx, _ = _presentation(context)
    with workspace_execution_lock(ctx.root):
        if context.execution_guard:
            context.execution_guard()
        text = materials(ctx.issue_dir, ctx.state, ctx.step, ctx.step_def)
        key = f"reviewed-result:{ctx.state.workflow_id}:{ctx.step}:{ctx.iteration}"
        content = text.encode()
        if len(content) > MAX_EVIDENCE_BYTES:
            raise ValueError("local review materials exceed the evidence limit; shorten the source evidence and repeat review")
        task = materialize_task(ctx, text.rstrip(), handoff_key=key)
        atomic_write_bytes(ctx.output, content)
        return AutomaticExecutionResult(
            intent=task.trigger,
            task_id=task.id,
            artifacts={ctx.step_def["output_artifact"]: str(ctx.output)},
        )


def revalidate(issue, state, definition, task):
    if task is None:
        raise ValueError("missing local acceptance task")
    output = issue / task.step / f"iteration_{task.iteration:03}" / "output.md"
    saved = read_bounded(output)
    locale = "en-US" if saved.startswith(b"# Local review\n") else state.conversation_locale
    text = materials(issue, state, task.step, definition, locale=locale)
    entry = state.artifacts.get(definition["output_artifact"])
    root = issue_project_root(issue.resolve())
    if (
        entry is None
        or entry.updated_by != task.step
        or (root / entry.path).resolve() != output.resolve()
        or not task.prompt.endswith("\n\n" + text.rstrip())
        or saved != text.encode()
        or hashlib.sha256(text.encode()).hexdigest() != entry.content_sha256
    ):
        raise ValueError("shown local acceptance materials have changed")

def revalidate_current_selection(issue, state, task):
    from cafe.core.task_inbox import load_issue_task_playbook
    from cafe.utils.issue_config import read_issue_config_strict

    config = read_issue_config_strict(issue / "issue.yaml")
    if config.get("playbook_id", state.playbook_id) != state.playbook_id:
        raise ValueError("owning playbook changed")
    graph = load_issue_task_playbook(project_root=issue_project_root(issue.resolve()), issue_dir=issue,
                                     playbook_id=state.playbook_id)
    definition = graph["steps"].get(task.step, {})
    if (definition.get("automatic", {}).get("executor") != "local_review"
            or definition.get("local_review", {}).get("outcomes") != task.continuations
            or not any(b["trigger"] == task.trigger and b["task_id"] == task.policy_id
                       and b["outcomes"] == task.continuations for b in definition.get("human_tasks", []))):
        raise ValueError("local review endpoint or acceptance outcomes changed")
    revalidate(issue, state, definition, task)

"""Materialize the clean revision and source evidence bound to an independent review."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory

from cafe.core.automatic_steps import AutomaticExecutionResult
from cafe.core.blackboard import BlackboardStore
from cafe.core.packet_io import atomic_write_bytes
from cafe.core.workspace_artifact import WorkspaceArtifact, verify_workspace_artifact
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.delivery.automatic import _presentation, validate_inputs
from cafe.core.runtime_locales import render_text
from cafe.delivery.local_result import MAX_EVIDENCE_BYTES, read_bounded
from cafe.delivery.operations import Commands
from cafe.delivery.presentation import materialize_task
from cafe.utils.issue_paths import issue_project_root


def _document(issue, root, entry):
    path = (root / entry.path).resolve(strict=True)
    path.relative_to(issue.resolve())
    content = read_bounded(path)
    if not content.strip() or hashlib.sha256(content).hexdigest() != entry.content_sha256:
        raise ValueError(f"published evidence changed: {entry.name}")
    return content.decode("utf-8")


def _reviewed_patch(root, workspace, path, *, create):
    if create:
        with TemporaryDirectory(dir=path.parent) as directory:
            temporary = Path(directory) / path.name
            Commands(30).git(
                root, "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv",
                "--no-color", "--submodule=short", "--ignore-submodules=none",
                f"--output={temporary}", f"{workspace.base_sha}..{workspace.head_sha}", "--",
            )
            temporary.replace(path)

    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("reviewed patch must be a regular file")
        sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.lstat()
        if not os.path.samestat(before, after) or before.st_ctime_ns != after.st_ctime_ns:
            raise ValueError("reviewed patch changed while reading")
    return sha256


def materials(issue, state, step, definition, *, locale=None, retained=False,
              patch_path=None, create_patch=False):
    root = issue_project_root(issue.resolve())
    binding = definition["local_review"]
    reviewer = binding["review_step"]
    audit = BlackboardStore(issue).audit
    edge = audit.latest_record(state.workflow_id, {"transition"},
                               step=step if retained else None, bounded=True)
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
        copy("review", event=edge["event_id"]),
    ]
    if patch_path is not None:
        sha256 = _reviewed_patch(root, workspace, patch_path, create=create_patch)
        lines.extend(["", copy("diff", path=str(patch_path), sha256=sha256)])
    lines.extend(["", "## " + copy("files"), ""])
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
    return _present(inputs, context)


def prepare_integration(inputs, *, context):
    return _present(inputs, context, integration=True)


def _present(inputs, context, *, integration=False):
    validate_inputs(inputs)
    ctx, _ = _presentation(context)
    with workspace_execution_lock(ctx.root):
        if context.execution_guard:
            context.execution_guard()
        patch_path = None
        if integration:
            directory = ctx.output.parent.resolve()
            directory.relative_to(ctx.issue_dir.resolve())
            patch_path = directory / "reviewed.patch"
        text = materials(
            ctx.issue_dir, ctx.state, ctx.step, ctx.step_def,
            patch_path=patch_path,
            create_patch=integration and not ctx.output.exists(),
        )
        content = text.encode()
        if len(content) > MAX_EVIDENCE_BYTES:
            raise ValueError("local review materials exceed the evidence limit; shorten the source evidence and repeat review")
        if ctx.output.exists() and read_bounded(ctx.output) != content:
            raise ValueError("prepared review evidence has changed")
        task = None
        if not integration:
            key = f"reviewed-result:{ctx.state.workflow_id}:{ctx.step}:{ctx.iteration}"
            task = materialize_task(ctx, text.rstrip(), handoff_key=key)
        if not ctx.output.exists():
            atomic_write_bytes(ctx.output, content)
        return AutomaticExecutionResult(
            intent=task.trigger if task else "await_agent",
            task_id=task.id if task else None,
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

@contextmanager
def local_review_decision_lock(issue):
    from cafe.catalogs.resolver import global_catalog_lock
    from cafe.utils.config import get_global_cafe_dir
    from cafe.utils.issue_config import issue_config_lock

    with issue_config_lock(issue / "issue.yaml"), global_catalog_lock(get_global_cafe_dir()):
        yield


def revalidate_current_selection(issue, state, task):
    from cafe.core.human_tasks import resolve_step_human_task
    from cafe.core.task_inbox import load_issue_task_playbook
    from cafe.skills.loader import SkillLoader
    from cafe.utils.issue_config import read_issue_config_strict

    config = read_issue_config_strict(issue / "issue.yaml")
    if config.get("playbook_id", state.playbook_id) != state.playbook_id:
        raise ValueError("owning playbook changed")
    graph = load_issue_task_playbook(project_root=issue_project_root(issue.resolve()), issue_dir=issue,
                                     playbook_id=state.playbook_id)
    definition = graph["steps"].get(task.step, {})
    expected = "local_preparation" if task.handoff_key.startswith("local-preparation:") else "local_review"
    if definition.get("automatic", {}).get("executor") != expected:
        raise ValueError("local review endpoint changed")
    policy, binding = resolve_step_human_task(
        playbook_data=graph, step_name=task.step, trigger=task.trigger, iteration=task.iteration,
        skill_loader=SkillLoader(project_root=issue_project_root(issue.resolve())),
    )
    if (policy.for_locale(state.conversation_locale).model_dump(mode="json") != task.expected_result
            or binding.task_id != task.policy_id or binding.outcomes != task.continuations
            or (expected == "local_review" and definition["local_review"]["outcomes"] != task.continuations)):
        raise ValueError("local acceptance policy or outcomes changed")
    revalidate(issue, state, definition, task)


def integration_evidence(issue, state, entry):
    """Read the current tracked preparation and reconstruct its reviewed inputs."""
    from cafe.core.task_inbox import load_issue_task_playbook

    root = issue_project_root(issue.resolve())
    graph = load_issue_task_playbook(project_root=root, issue_dir=issue, playbook_id=state.playbook_id)
    definition = graph["steps"].get(entry.updated_by, {})
    if (definition.get("automatic", {}).get("executor") != "local_preparation"
            or definition.get("output_artifact") != entry.name):
        raise ValueError("prepared local integration is no longer selected")
    text = _document(issue, root, entry)
    output = (root / entry.path).resolve(strict=True)
    if text != materials(
        issue, state, entry.updated_by, definition, retained=True,
        patch_path=output.with_name("reviewed.patch"),
    ):
        raise ValueError("prepared local evidence no longer matches the independent Review")
    workspace = state.artifacts[definition["workspace_input_artifact"]]
    return text, workspace.head_sha

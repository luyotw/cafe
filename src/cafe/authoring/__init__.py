"""Mode-neutral source authoring API; preview never publishes or grants authority."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path

from .phase import patch, resource_path, scaffold
from .requests import Request, decode_request
from .results import Result
from .source_edits import dump, edit_yaml
from .transaction import commit, confined, locked, recover, storage
from .validation import digest, validate

__all__ = ["prepare", "apply", "decode_request", "Result", "request_schema"]


def request_schema():
    """Envelope plus runtime-owned schema projections, without a second workflow schema."""
    from cafe.core.playbook import PlaybookDefinition
    from cafe.skills.contracts import SkillWorkflowDeclaration

    return {
        "request": Request.model_json_schema(),
        "phase_workflow": SkillWorkflowDeclaration.model_json_schema(),
        "playbook": PlaybookDefinition.model_json_schema(),
    }


def prepare(request, *, root=None, _allow_pending=False):
    root = Path(root or Path.cwd()).absolute()
    result = Result(operation="prepare")
    try:
        if root.is_symlink() or root.resolve() != root:
            raise ValueError("Selected repository root must not traverse symlinks")
        if not _allow_pending and (storage(root) / "pending.json").exists():
            result.diagnose(
                "pending_recovery", "An interrupted transaction requires recovery on the next apply"
            )
            return result
        parsed = Request.model_validate(request)
        requests = [parsed, *parsed.companions]
        targets = [r.target for r in requests]
        if len(targets) != len(set(targets)) or len(targets) > 64:
            raise ValueError("Targets must be unique and transaction must contain at most 64 files")
        for item in requests:
            target = confined(root, item.target)
            phase = item.target.endswith("/SKILL.md")
            if phase:
                if target.parent.parent.name != "skills":
                    raise ValueError("Phase target must be skills/<name>/SKILL.md")
            elif target.parent.name != "playbooks" or target.suffix != ".yaml":
                raise ValueError("Playbook target must be playbooks/<id>.yaml")
            before = target.read_text() if target.exists() else None
            if item.mode == "create":
                content = scaffold(item) if phase else dump(item.declaration)
                if before is not None and before != content:
                    raise ValueError(f"Create would overwrite {item.target}; use a bounded patch")
            else:
                if before is None:
                    raise ValueError(f"Patch source does not exist: {item.target}")
                content = before
                seen = set()
                prior = []
                for operation in item.operations:
                    identity = tuple(operation.path) + (
                        (
                            json.dumps(operation.value.get(operation.key))
                            if operation.key and isinstance(operation.value, dict)
                            else json.dumps(operation.value)
                        ),
                    )
                    if any(
                        (
                            operation.path[: len(old.path)] == old.path
                            or old.path[: len(operation.path)] == operation.path
                        )
                        and (
                            operation.path != old.path
                            or operation.op == "replace"
                            or old.op == "replace"
                        )
                        for old in prior
                    ):
                        raise ValueError("Overlapping/conflicting structural operations")
                    prior.append(operation)
                    if identity in seen:
                        raise ValueError("Duplicate/conflicting operations")
                    seen.add(identity)
                    if phase and operation.path[0] == "references":
                        if (
                            len(operation.path) != 2
                            or operation.op != "replace"
                            or not isinstance(operation.value, str)
                        ):
                            raise ValueError("Reference edits require exact replacement content")
                        relative = str(
                            target.parent.relative_to(root) / resource_path(operation.path[1])
                        )
                        source = confined(root, relative)
                        old = source.read_text() if source.exists() else None
                        if old != operation.value and old != operation.expected:
                            raise ValueError("Reference expected content does not match")
                        result.files[relative] = operation.value
                    else:
                        content = (
                            patch(content, operation) if phase else edit_yaml(content, operation)
                        )
            result.files[item.target] = content
            for reference, text in item.references.items():
                if not phase:
                    raise ValueError("Only phases can declare reference resources")
                relative = str(target.parent.relative_to(root) / resource_path(reference))
                source = confined(root, relative)
                if source.exists() and source.read_text() != text:
                    raise ValueError("Create would replace an existing reference")
                result.files[relative] = text
        if len(result.files) > 64:
            raise ValueError("Transaction write set exceeds 64 files")
        for relative, after in sorted(result.files.items()):
            target = confined(root, relative)
            before = target.read_text() if target.exists() else None
            result.dependencies[str(target)] = digest(target)
            if before != after:
                result.changes.append(
                    {
                        "target": relative,
                        "origin": (
                            "mixed"
                            if relative.endswith("/SKILL.md") and before is None
                            else "author_declared"
                        ),
                        "generated": (
                            ["canonical_sections", "role_line"]
                            if relative.endswith("/SKILL.md") and before is None
                            else []
                        ),
                        "kind": "create" if before is None else "patch",
                    }
                )
                result.diff += "".join(
                    difflib.unified_diff(
                        (before or "").splitlines(True),
                        after.splitlines(True),
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                    )
                )
        validate(root, result, requests)
    except Exception as error:
        result.diagnose("invalid_request", str(error))
    result.diagnostics.sort(key=lambda d: json.dumps(d, sort_keys=True))
    fingerprint = {"request": request, "files": result.files, "dependencies": result.dependencies}
    result.change_digest = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, default=str).encode()
    ).hexdigest()
    return result


def apply(request, *, root=None, expect_change=None):
    root = Path(root or Path.cwd()).absolute()
    preview = prepare(request, root=root)
    preview.operation = "apply"
    # Invalid requests must never create source catalogs or lock files.
    if preview.status == "rejected" and not any(
        d["code"] == "pending_recovery" for d in preview.diagnostics
    ):
        return preview
    if expect_change and preview.status != "rejected" and expect_change != preview.change_digest:
        preview.diagnose("stale_preview", "Prepared change digest differs from reviewed preview")
        return preview
    try:
        with locked(root):
            journal = storage(root) / "pending.json"
            if journal.exists():
                recover(root, journal)
            result = prepare(request, root=root, _allow_pending=True)
            result.operation = "apply"
            if result.status == "rejected":
                return result
            if expect_change and expect_change != result.change_digest:
                result.diagnose("stale_preview", "Dependencies changed; create a fresh preview")
                return result
            if result.change_digest != preview.change_digest and preview.status != "rejected":
                result.diagnose(
                    "stale_source", "Sources changed while acquiring the authoring lock"
                )
                return result
            for path, expected in result.dependencies.items():
                if digest(Path(path)) != expected:
                    result.diagnose(
                        "stale_source", "Source dependency changed", target=str(Path(path))
                    )
                    return result
            if not result.changes:
                result.status = "noop"
                return result

            def final_check():
                checked = prepare(request, root=root, _allow_pending=True)
                if checked.status == "rejected" or checked.changes:
                    raise ValueError("Published candidate failed final validation")

            commit(root, result, final_check)
            result.status = "applied"
            return result
    except Exception as error:
        preview.operation = "apply"
        preview.diagnose(
            "apply_failed",
            str(error),
            remedy="Inspect retained recovery evidence and preview again",
        )
        return preview

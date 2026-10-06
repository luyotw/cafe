"""Versioned delivery facts, owned by the Manager and never read by workflow core."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from cafe.core.packet_io import canonical_json
from cafe.core.git_delivery import git_text as _git, remote_identity as _remote_identity

MAX_CLOSEOUT_EVIDENCE_BYTES = 256 * 1024
# Linux pathname limit is 4096 bytes. JSON can expand each byte sixfold.
_MAX_ENCODED_WORKTREE_PATH = 4096 * 6 + 1


def closeout_evidence_record(
    plan: dict[str, Any],
    *,
    issue_name: str,
    workflow_id: str,
    contract_sha256: str,
    worktree: str,
) -> dict[str, Any]:
    """Use the same ordered, exact-argv projection for validation and persistence."""
    return {
        "version": 1,
        "issue_name": issue_name,
        "workflow_id": workflow_id,
        "contract_sha256": contract_sha256,
        "worktree": worktree,
        "commands": {
            stage: [
                {"argv": item["argv"], "status": "not_started", "returncode": None}
                for item in plan[stage]
            ]
            for stage in ("deliver", "cleanup")
        },
    }


def maximum_closeout_evidence_size(plan: dict[str, Any]) -> int:
    """Bound every accepted plan before confirmation, including path JSON expansion."""
    record = closeout_evidence_record(
        plan,
        issue_name="x" * 255,
        workflow_id="x" * 255,
        contract_sha256="0" * 64,
        worktree="/" + "x" * (_MAX_ENCODED_WORKTREE_PATH - 1),
    )
    return len(canonical_json(record))


class DeliveryConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    architecture: list[str]
    dependencies: list[str]
    compatibility: list[str]
    quality: list[str]
    permissions: list[str]
    external_side_effects: list[str]
    cost: list[str]

    @field_validator("*")
    @classmethod
    def _distinct_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("constraints must contain distinct non-empty statements")
        return values


class _DeliveryContractBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    schema_version: StrictInt
    outcome: str = Field(min_length=1)
    motivation: str = Field(min_length=1)
    in_scope: list[str] = Field(min_length=1)
    out_of_scope: list[str]
    acceptance_invariants: list[str] = Field(min_length=1)
    required_evidence: list[str] = Field(min_length=1)
    implementation_direction: str = Field(min_length=1)
    constraints: DeliveryConstraints
    allowed_variations: list[str]
    deviation_triggers: list[str] = Field(min_length=1)

    @field_validator(
        "in_scope",
        "out_of_scope",
        "acceptance_invariants",
        "required_evidence",
        "allowed_variations",
        "deviation_triggers",
    )
    @classmethod
    def _distinct_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("delivery lists must contain distinct non-empty statements")
        return values


class CloseoutCommand(BaseModel):
    """One exact host-side command approved as part of a closeout plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    argv: list[str] = Field(min_length=1)

    @field_validator("argv")
    @classmethod
    def _literal_nonempty_argv(cls, values: list[str]) -> list[str]:
        if not values[0]:
            raise ValueError("closeout argv executable must not be empty")
        for value in values:
            if "{{" in value or "${" in value or (value.startswith("<") and value.endswith(">")):
                raise ValueError("closeout argv must not contain unresolved placeholders")
        return values


class DeliveryCloseoutPlan(BaseModel):
    """Exact commands confirmed with the complete Delivery Contract."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    deliver: list[CloseoutCommand]
    cleanup: list[CloseoutCommand]

    @field_validator("deliver", "cleanup")
    @classmethod
    def _distinct_commands(cls, values: list[CloseoutCommand]) -> list[CloseoutCommand]:
        commands = [tuple(command.argv) for command in values]
        if len(set(commands)) != len(commands):
            raise ValueError("closeout commands must be distinct within each stage")
        return values

    @model_validator(mode="after")
    def _bounded_execution_evidence(self) -> DeliveryCloseoutPlan:
        if (
            maximum_closeout_evidence_size(self.model_dump(mode="json"))
            > MAX_CLOSEOUT_EVIDENCE_BYTES
        ):
            raise ValueError("closeout plan exceeds durable execution evidence capacity")
        return self


class DeliveryContractV1(_DeliveryContractBase):
    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported Delivery Contract version")
        return value


class DeliveryContractV2(_DeliveryContractBase):
    closeout_plan: DeliveryCloseoutPlan

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 2:
            raise ValueError("unsupported Delivery Contract version")
        return value


class DeliveryContractV3(BaseModel):
    """Compact confirmed outcome and its task-specific authority boundaries."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    schema_version: StrictInt
    outcome: str = Field(min_length=1)
    in_scope: list[str] = Field(min_length=1)
    out_of_scope: list[str]
    acceptance_invariants: list[str] = Field(min_length=1)
    implementation_direction: str = Field(min_length=1)
    permissions: list[str]
    constraints: list[str]
    closeout_plan: DeliveryCloseoutPlan

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 3:
            raise ValueError("unsupported Delivery Contract version")
        return value

    @field_validator(
        "in_scope",
        "out_of_scope",
        "acceptance_invariants",
        "permissions",
        "constraints",
    )
    @classmethod
    def _distinct_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("delivery lists must contain distinct non-empty statements")
        return values


def normalize_delivery_contract(value: Any) -> dict[str, Any]:
    """Validate contract structure; activation supplies confirmation authority."""
    if not isinstance(value, dict):
        raise ValueError("Delivery Contract must be a mapping")
    version = value.get("schema_version")
    if version == 4:
        return validate_compact_delivery(value)
    if version == 1:
        return DeliveryContractV1.model_validate(value).model_dump(mode="json")
    if version == 2:
        return DeliveryContractV2.model_validate(value).model_dump(mode="json")
    if version == 3:
        return DeliveryContractV3.model_validate(value).model_dump(mode="json")
    raise ValueError("unsupported Delivery Contract version")


def validate_compact_delivery(value):
    """Literal compact endpoint and effects, with no full outcome questionnaire."""
    route = value.get("route")
    endpoint = {"source_branch", "target_branch"} if route == "pr" else {"branch"}
    required = {"schema_version", "route", "remote", "effects"} | endpoint
    if route not in {"pr", "direct"} or not required <= set(value) or not set(value) <= required | {"closeout_plan", "remote_identity"}:
        raise ValueError("compact delivery endpoint is incomplete")
    for field in endpoint | {"remote"}:
        token = value[field]
        if (not isinstance(token, str) or not token or token.startswith("-") or
                any(c.isspace() for c in token) or ".." in token or any(c in token for c in "~^:?*[\\")):
            raise ValueError("compact delivery requires literal remote and branches")
    effects = ["create_pr"] if route == "pr" else ["commit", "push"]
    if value["effects"] != effects:
        raise ValueError("compact delivery effects do not match its route")
    result = dict(value)
    if "remote_identity" in value:
        import re
        if not isinstance(value["remote_identity"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["remote_identity"]):
            raise ValueError("remote endpoint identity is invalid")
    if "closeout_plan" in value:
        result["closeout_plan"] = DeliveryCloseoutPlan.model_validate(value["closeout_plan"]).model_dump(mode="json")
        validate_closeout_plan_policy(result["closeout_plan"], allow_squash=False)
        if result["closeout_plan"]["cleanup"]:
            raise ValueError("compact delivery grants no cleanup authority")
        commands = [c["argv"] for c in result["closeout_plan"]["deliver"]]
        if route == "pr" and commands:
            raise ValueError("PR delivery uses the capability rather than closeout commands")
        if route == "direct" and not (
            len(commands) == 2 and len(commands[0]) == 5
            and commands[0][:4] == ["git", "commit", "--allow-empty", "-m"]
            and commands[0][4] and commands[1] == ["git", "push", value["remote"],
                f"HEAD:refs/heads/{value['branch']}"]
        ):
            raise ValueError("direct delivery requires exact commit and push argv")
    return result


def validate_closeout_plan_policy(
    closeout_plan: dict[str, Any], *, allow_squash: bool | None
) -> None:
    """Validate lifecycle-command placement and mode before closeout execution."""
    plan = DeliveryCloseoutPlan.model_validate(closeout_plan)
    for stage in ("deliver", "cleanup"):
        commands = plan.deliver if stage == "deliver" else plan.cleanup
        for index, command in enumerate(commands):
            argv = command.argv
            if len(argv) < 2 or argv[1] != "close" or Path(argv[0]).name.lower() != "cafe":
                continue
            if argv[0] != "cafe":
                raise ValueError("cafe close must use the literal `cafe` executable")
            if stage != "cleanup" or index != len(commands) - 1:
                raise ValueError("cafe close is allowed only as the final cleanup command")

            squash = False
            message = False
            argument_index = 2
            while argument_index < len(argv):
                argument = argv[argument_index]
                if argument == "--squash" and not squash:
                    squash = True
                    argument_index += 1
                    continue
                if argument in {"-m", "--message"} and not message:
                    if argument_index + 1 >= len(argv) or not argv[argument_index + 1]:
                        raise ValueError("cafe close message option requires a non-empty value")
                    message = True
                    argument_index += 2
                    continue
                raise ValueError("cafe close has unsupported or duplicate options")
            if message and not squash:
                raise ValueError("cafe close message option requires --squash")
            if squash and allow_squash is False:
                raise ValueError("cafe close --squash is unavailable in create-PR mode")


def prepare_compact_delivery(root: Path, value: dict, *, issue_name: str) -> dict:
    """Resolve internal endpoint identity and literal closeout before confirmation."""
    endpoint = validate_compact_delivery(value)
    endpoint["remote_identity"] = _remote_identity(root, endpoint["remote"], endpoint["route"])
    if endpoint["route"] == "direct":
        endpoint["closeout_plan"] = {"deliver": [
            {"argv": ["git", "commit", "--allow-empty", "-m", f"Deliver {issue_name}"]},
            {"argv": ["git", "push", endpoint["remote"], f"HEAD:refs/heads/{endpoint['branch']}"]},
        ], "cleanup": []}
    return endpoint


def validate_compact_action(issue_dir: Path, root: Path) -> dict:
    """Re-resolve authority and current review immediately before an exact action."""
    from cafe.core.blackboard import BlackboardStore, HandoffOwner, HandoffIntent
    from cafe.core.execution_checkpoints import (
        checkpoint, require_checkpoint, load_review_evidence, require_verified_review,
    )
    from cafe.manager.file_scope import execution_scope_projection
    context = execution_scope_projection(issue_dir, root)
    if context is None:
        raise ValueError("compact delivery requires confirmed authority")
    board = BlackboardStore(issue_dir).load_read_only()
    baton = board.handoff_contract
    if (board.current_step != "done" or board.workflow_id != context["identity"]["workflow_id"]
            or baton is None or baton.to_owner != HandoffOwner.DONE
            or baton.intent != HandoffIntent.WORKFLOW_COMPLETE):
        raise ValueError("delivery requires terminal workflow readiness and quiescence")
    endpoint = context["delivery_endpoint"]
    if endpoint.get("remote_identity") != _remote_identity(root, endpoint["remote"], endpoint["route"]):
        raise ValueError("confirmed remote endpoint changed")
    branch = endpoint["source_branch"] if endpoint["route"] == "pr" else endpoint["branch"]
    if _git(root, "symbolic-ref", "--short", "HEAD") != branch:
        raise ValueError("current branch differs from the confirmed delivery branch")
    readiness = load_review_evidence(issue_dir / "execution_delivery.json")
    if (readiness.get("authority_digest") != context["authority_digest"]
            or readiness.get("endpoint") != endpoint):
        raise ValueError("delivery readiness no longer matches confirmed authority")
    if context.get("review_policy") == "single_native":
        require_verified_review(context, load_review_evidence(issue_dir / "execution_review.json"))
    require_checkpoint(context, readiness.get("checkpoint"), "before_delivery")
    fresh = checkpoint(context, "before_delivery", round_id="delivery-action", parent_id="manager")
    require_checkpoint(context, fresh, "before_delivery")
    from cafe.core.packet_io import atomic_write_bytes
    atomic_write_bytes(issue_dir / "delivery_action_checkpoint.json", canonical_json(fresh))
    return context


def run_compact_closeout_command(issue_dir: Path, root: Path, argv: list[str]):
    """Run exact approved closeout using existing worker lock and a private Git index."""
    import os
    import subprocess
    import tempfile
    from cafe.workflow_execution.workflow_hosting import WorkflowHost
    from cafe.core.workspace_artifact import inspect_workspace

    def action():
        context = validate_compact_action(issue_dir, root)
        endpoint = context["delivery_endpoint"]
        commands = endpoint.get("closeout_plan", {}).get("deliver", [])
        if endpoint["route"] != "direct" or argv not in [c["argv"] for c in commands]:
            raise ValueError("closeout command differs from the approved direct effect")
        if argv[1] != "commit":
            from cafe.core.file_scope import require_committed_content
            from cafe.core.git_delivery import (
                delivery_target, require_delivery_target, pinned_remote_command,
            )
            target = delivery_target(context)
            require_committed_content(root, context["paths"], revision=target["head_oid"])
            require_delivery_target(context, target)
            ref = f"refs/heads/{target['source_branch']}"
            # The approved argv defines intent; resolved immutable operands keep
            # executable filters and normal hooks from redirecting that effect.
            result = subprocess.run(pinned_remote_command(
                "push", target["push_url"], f"{target['head_oid']}:{ref}"),
                cwd=root, check=False, timeout=240)
            if result.returncode == 0:
                observed = subprocess.run(pinned_remote_command(
                    "ls-remote", target["push_url"], ref), cwd=root, check=True,
                    capture_output=True, text=True, timeout=20).stdout.strip()
                if observed.split()[0:1] != [target["head_oid"]]:
                    raise ValueError("push outcome is unverified; inspect read-only before recovery")
                if validate_compact_action(issue_dir, root)["authority_digest"] != context["authority_digest"]:
                    raise ValueError("delivery authority changed during push")
                require_delivery_target(context, target)
                from cafe.core.packet_io import atomic_write_bytes
                atomic_write_bytes(issue_dir / "delivery_result.json", canonical_json({
                    "delivered": True, "route": "direct", "commit": target["head_oid"],
                    "branch": endpoint["branch"], "remote_identity": endpoint["remote_identity"],
                    "authority_digest": context["authority_digest"]}))
            return result
        approved = set(context["paths"])
        paths = sorted({change[key] for change in inspect_workspace(root).changes
                        for key in ("path", "old_path") if key in change and change[key] in approved})
        index_dir = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-dir"))
        with tempfile.TemporaryDirectory(prefix="cafe-delivery-", dir=index_dir) as temp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(temp) / "index")}
            subprocess.run(["git", "read-tree", "HEAD"], cwd=root, env=env, check=True, timeout=20)
            if paths:
                subprocess.run(["git", "--literal-pathspecs", "add", "-A", "--", *paths],
                               cwd=root, env=env, check=True, timeout=20)
            # Git hooks run normally against this exact staged change.
            result = subprocess.run(argv, cwd=root, env=env, check=False, timeout=240)
            if result.returncode == 0:
                from cafe.core.file_scope import require_committed_content
                require_committed_content(root, context["paths"])
                validate_compact_action(issue_dir, root)
            if result.returncode == 0 and paths:
                subprocess.run(["git", "--literal-pathspecs", "reset", "-q", "HEAD", "--", *paths],
                               cwd=root, check=True, timeout=20)
            return result
    return WorkflowHost(issue_dir).run(action, hosting="foreground").result


def publish_compact_pr(issue_dir: Path, root: Path, output: Path, *, registry=None, approval_task_id=None, correlation_id=None) -> dict:
    """Resolve Manager authority and delegate publication to its capability owner."""
    from cafe.core.pr_delivery import publish_resolved_pr
    from cafe.workflow_execution.workflow_hosting import WorkflowHost

    def action():
        context = validate_compact_action(issue_dir, root)
        def before_dispatch():
            if validate_compact_action(issue_dir, root)["authority_digest"] != context["authority_digest"]:
                raise ValueError("publication authority changed during delivery")
        return publish_resolved_pr(issue_dir, root, output, context=context,
            before_dispatch=before_dispatch,
            registry=registry, approval_task_id=approval_task_id,
            correlation_id=correlation_id)
    return WorkflowHost(issue_dir).run(action, hosting="foreground").result

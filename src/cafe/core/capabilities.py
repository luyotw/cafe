"""File-backed capability registry and host-side PR publish execution."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from cafe.core.execution_boundary import redact
from cafe.utils.github import GitHubOps

CAPABILITY_PR_PUBLISH_ID = "cafe.pr.publish"
CAPABILITY_BROWSER_OPEN_ID = "cafe.browser.open"
CAPABILITY_ISSUE_COMMENT_ID = "cafe.github.issue_comment"
CAPABILITY_SLACK_HUMAN_TASK_ID = "cafe.slack.human_task"

# Failure categories surfaced on capability receipts (distinct from validation_error).
SCRIPT_EXIT_ERROR = "script_exit_error"
OUTPUT_CONTRACT_ERROR = "output_contract_error"
TIMEOUT_ERROR = "timeout_error"
VALIDATION_ERROR = "validation_error"


class CapabilityRegistryError(ValueError):
    """Raised when capability definitions cannot be loaded."""


class StrictCapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ValueSchema(StrictCapabilityModel):
    type: Literal["string", "integer", "boolean"]
    enum: Optional[Tuple[Any, ...]] = None

    @field_validator("enum", mode="before")
    @classmethod
    def freeze_enum(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def enum_values_match_declared_type(self) -> "ValueSchema":
        if self.enum is None:
            return self
        python_types = {"string": str, "integer": int, "boolean": bool}
        expected = python_types[self.type]
        if any(type(value) is not expected for value in self.enum):
            raise ValueError("enum values must match the declared type")
        return self


class ObjectSchema(StrictCapabilityModel):
    required: Tuple[str, ...]
    properties: Mapping[str, ValueSchema]

    @field_validator("required", mode="before")
    @classmethod
    def freeze_required(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def required_fields_are_declared(self) -> "ObjectSchema":
        if not set(self.required).issubset(self.properties):
            raise ValueError("required fields must be declared in properties")
        object.__setattr__(self, "properties", MappingProxyType(dict(self.properties)))
        return self

    @field_serializer("properties")
    def serialize_properties(self, value: Mapping[str, ValueSchema]) -> Dict[str, ValueSchema]:
        return dict(value)


class CapabilityEffects(StrictCapabilityModel):
    writes: Tuple[str, ...]
    network_destinations: Tuple[str, ...]
    browser_open: Tuple[str, ...] = ()

    @field_validator("writes", "network_destinations", "browser_open", mode="before")
    @classmethod
    def freeze_effects(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class CapabilityManifest(StrictCapabilityModel):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    implementation: Literal[
        "sync_pr",
        "open_current_pr",
        "sync_issue_comment",
        "notify_slack_human_task",
    ]
    arguments: ObjectSchema
    outputs: ObjectSchema
    effects: CapabilityEffects
    credentials: Tuple[str, ...]
    permissions: Mapping[str, Tuple[str, ...]]
    idempotency: Literal["safe", "update_in_place", "unsafe"]
    risk: Literal["low", "medium", "high"]
    approval: Literal["not_required", "required"]
    policy: Literal["allow", "deny"]

    @field_validator("credentials", mode="before")
    @classmethod
    def freeze_credentials(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("permissions", mode="before")
    @classmethod
    def freeze_permissions(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        return {
            key: tuple(items) if isinstance(items, list) else items for key, items in value.items()
        }

    @model_validator(mode="after")
    def reject_contradictory_policy(self) -> "CapabilityManifest":
        if self.approval == "required" and self.policy == "deny":
            raise ValueError("approval-required capability cannot also be policy denied")
        object.__setattr__(self, "permissions", MappingProxyType(dict(self.permissions)))
        return self

    @field_serializer("permissions")
    def serialize_permissions(
        self, value: Mapping[str, Tuple[str, ...]]
    ) -> Dict[str, Tuple[str, ...]]:
        return dict(value)


class ExecutionRequest(StrictCapabilityModel):
    capability: str = Field(min_length=1)
    args: Mapping[str, Any]
    effects: CapabilityEffects
    credentials: Tuple[str, ...]
    permissions: Mapping[str, Tuple[str, ...]]
    expires_at: Optional[str] = None

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("expires_at must be a timezone-aware ISO 8601 timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expires_at must be a timezone-aware ISO 8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value

    @field_validator("credentials", mode="before")
    @classmethod
    def freeze_credentials(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("permissions", mode="before")
    @classmethod
    def freeze_permissions(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        return {
            key: tuple(items) if isinstance(items, list) else items for key, items in value.items()
        }


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class CapabilityEvaluation:
    request: ExecutionRequest
    manifest: CapabilityManifest
    fingerprint: str
    decision: PolicyDecision
    reason_code: str
    explanation: str
    allowed_effects: CapabilityEffects


def canonical_request_fingerprint(request: ExecutionRequest) -> str:
    """Hash the canonical security-relevant request boundary."""
    payload = request.model_dump(mode="json")
    effects = payload["effects"]
    for key in ("writes", "network_destinations", "browser_open"):
        effects[key] = sorted(effects[key])
    payload["credentials"] = sorted(payload["credentials"])
    payload["permissions"] = {
        key: sorted(values) for key, values in sorted(payload["permissions"].items())
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_request_arguments(
    request: ExecutionRequest, manifest: CapabilityManifest
) -> Optional[str]:
    declared = manifest.arguments.properties
    if not set(manifest.arguments.required).issubset(request.args):
        return "missing_argument"
    if not set(request.args).issubset(declared):
        return "argument_not_allowed"
    python_types = {"string": str, "integer": int, "boolean": bool}
    for key, value in request.args.items():
        field = declared[key]
        expected = python_types[field.type]
        if type(value) is not expected:
            return "argument_type_invalid"
        if field.enum is not None and value not in field.enum:
            return "argument_not_allowed"
    return None


def _matches_effect_boundary(requested: CapabilityEffects, allowed: CapabilityEffects) -> bool:
    return all(
        set(getattr(requested, field)) == set(getattr(allowed, field))
        for field in ("writes", "network_destinations", "browser_open")
    )


def _resolve_boundary_tokens(
    manifest: CapabilityManifest, request: ExecutionRequest
) -> tuple[CapabilityEffects, Mapping[str, Tuple[str, ...]]]:
    replacements: Dict[str, str] = {}
    if manifest.id == CAPABILITY_PR_PUBLISH_ID:
        output = str(request.args.get("output") or "")
        output_path = Path(output)
        if output_path.is_absolute() or ".." in output_path.parts:
            return CapabilityEffects(writes=(), network_destinations=(), browser_open=()), {}
        replacements["request_output"] = output
        parts = output_path.parts
        try:
            issue_index = parts.index("issues")
            replacements["issue_dir"] = str(Path(*parts[: issue_index + 2]))
        except (ValueError, IndexError):
            replacements["issue_dir"] = ""
    elif manifest.id == CAPABILITY_ISSUE_COMMENT_ID:
        issue_id = str(request.args.get("issue_id") or "").strip()
        if issue_id:
            replacements["request_issue_comment"] = f"github_issue_comment:{issue_id}"

    def expand(values: Sequence[str]) -> Tuple[str, ...]:
        return tuple(
            replacements.get(value, value) for value in values if replacements.get(value, value)
        )

    effects = CapabilityEffects(
        writes=expand(manifest.effects.writes),
        network_destinations=manifest.effects.network_destinations,
        browser_open=manifest.effects.browser_open,
    )
    permissions = {key: expand(values) for key, values in manifest.permissions.items()}
    return effects, permissions


def evaluate_capability_request(
    registry: Mapping[str, CapabilityManifest], raw_request: Mapping[str, Any]
) -> CapabilityEvaluation:
    """Return one explicit, fail-closed policy decision for a parsed request."""
    request = ExecutionRequest.model_validate(raw_request)
    manifest = registry.get(request.capability)
    if not isinstance(manifest, CapabilityManifest):
        raise CapabilityRegistryError(f"Unknown capability {request.capability!r}")
    fingerprint = canonical_request_fingerprint(request)
    allowed_effects, allowed_permissions = _resolve_boundary_tokens(manifest, request)

    reason = _validate_request_arguments(request, manifest)
    if reason is None and not _matches_effect_boundary(request.effects, allowed_effects):
        reason = "effect_not_allowed"
    if reason is None and set(request.credentials) != set(manifest.credentials):
        reason = "credential_not_allowed"
    if reason is None:
        if set(request.permissions) != set(allowed_permissions):
            reason = "permission_not_allowed"
        else:
            for permission, values in request.permissions.items():
                if set(values) != set(allowed_permissions[permission]):
                    reason = "permission_not_allowed"
                    break

    if reason is not None:
        decision = PolicyDecision.DENY
        explanation = "The request exceeds its registered capability boundary."
    elif manifest.policy == "deny":
        decision = PolicyDecision.DENY
        reason = "policy_denied"
        explanation = "The registered policy denies this capability."
    elif manifest.approval == "required":
        decision = PolicyDecision.REQUIRE_APPROVAL
        reason = "approval_required"
        explanation = "The registered policy requires approval before execution."
    else:
        decision = PolicyDecision.ALLOW
        reason = "policy_allowed"
        explanation = "The request is within the registered capability boundary."

    return CapabilityEvaluation(
        request=request,
        manifest=manifest,
        fingerprint=fingerprint,
        decision=decision,
        reason_code=reason,
        explanation=explanation,
        allowed_effects=allowed_effects,
    )


def _package_capabilities_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "capabilities"


def default_capability_definition_dirs(repo_root: Path) -> List[Path]:
    """Return only immutable package-owned capability definitions."""
    del repo_root
    return [_package_capabilities_dir()]


def load_capability_registry(
    capabilities_dirs: Sequence[Path],
) -> Mapping[str, CapabilityManifest]:
    """Load all *.yaml / *.yml / *.json capability definitions.

    Duplicate ``id`` values across files are rejected with both paths listed.
    """
    merged: Dict[str, Tuple[CapabilityManifest, Path]] = {}
    for base in capabilities_dirs:
        if not base.is_dir():
            continue
        paths = (
            sorted(base.glob("*.yaml")) + sorted(base.glob("*.yml")) + sorted(base.glob("*.json"))
        )
        for path in paths:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise CapabilityRegistryError(f"Cannot read capability file {path}") from exc
            try:
                if path.suffix.lower() in {".yaml", ".yml"}:
                    data = yaml.safe_load(raw)
                else:
                    data = json.loads(raw)
            except (yaml.YAMLError, json.JSONDecodeError) as exc:
                raise CapabilityRegistryError(f"Invalid capability document {path}") from exc
            if not isinstance(data, dict):
                raise CapabilityRegistryError(f"Capability root must be a mapping: {path}")
            try:
                manifest = CapabilityManifest.model_validate(data)
            except ValidationError as exc:
                raise CapabilityRegistryError(f"Invalid capability manifest {path}: {exc}") from exc
            cap_id = manifest.id
            if cap_id in merged:
                raise CapabilityRegistryError(
                    f"Duplicate capability id {cap_id!r}: {merged[cap_id][1]} and {path}"
                )
            merged[cap_id] = (manifest, path)
    return MappingProxyType({key: value[0] for key, value in merged.items()})


def _schema_required(schema: Mapping[str, Any], *, label: str) -> List[str]:
    required = schema.get("required")
    if required is None:
        return []
    if not isinstance(required, list):
        raise CapabilityRegistryError(f"{label}.required must be a list")
    return [str(item) for item in required]


def _validate_string_args(args: Mapping[str, Any], required: Sequence[str]) -> Optional[str]:
    for key in required:
        val = args.get(key)
        if val is None or str(val).strip() == "":
            return f"missing_args:{key}"
        if not isinstance(val, str):
            return f"arg_type:{key}"
    for key, val in args.items():
        if val is None:
            continue
        if not isinstance(val, str):
            return f"arg_type:{key}"
    return None


def _receipt_inputs(raw_args: Any) -> Dict[str, Any]:
    return dict(raw_args) if isinstance(raw_args, Mapping) else {}


def _validate_outputs(payload: Mapping[str, Any], schema: Mapping[str, Any]) -> Optional[str]:
    required = _schema_required(schema, label="expected_outputs")
    for key in required:
        if key not in payload or str(payload.get(key) or "").strip() == "":
            return f"missing_output:{key}"
    return None


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    """Return True when a git ref can be resolved locally."""
    try:
        return (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", ref],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
        )
    except (FileNotFoundError, OSError):
        return False


def _resolve_publish_base(base_arg: str, *, repo_root: Path) -> str:
    """Return a publish base ref that resolves to a known GitHub branch.

    Keep legacy behavior for valid remote base refs, but avoid passing local-only
    branch names that GitHub cannot use as PR bases.
    """
    base = str(base_arg or "").strip()
    if not base:
        return ""

    candidates = [base, base.removeprefix("refs/heads/"), base.removeprefix("refs/remotes/origin/")]
    if base.startswith("refs/remotes/"):
        first_slash = base.find("/")
        if first_slash >= 0:
            candidates.append(base[first_slash + 1 :])
    elif "/" in base:
        candidates.append(base.rsplit("/", 1)[-1])

    for candidate in dict.fromkeys(candidates):
        if not candidate:
            continue
        if _git_ref_exists(repo_root, f"refs/remotes/origin/{candidate}"):
            return candidate
    return ""


def resolve_repo_relative_path(*, repo_root: Path, raw_path: str, field_name: str) -> Path:
    if not str(raw_path).strip():
        raise ValueError(f"missing_{field_name}")
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def resolve_sync_pr_script(repo_root: Path) -> Path:
    """Resolve the immutable package-owned ``sync_pr.sh`` adapter."""
    del repo_root
    script_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "skills"
        / "cafe-pr"
        / "scripts"
        / "sync_pr.sh"
    )
    if script_path.is_file() and not script_path.is_symlink():
        return script_path
    raise FileNotFoundError(f"PR sync script not found: {script_path}")


def resolve_script_for_ref(script_ref: str, repo_root: Path) -> Path:
    """Map registry ``script_ref`` to a concrete path (allow-list only)."""
    ref = str(script_ref or "").strip()
    if ref == "sync_pr":
        return resolve_sync_pr_script(repo_root)
    raise ValueError(f"unsupported_script_ref:{ref}")


def parse_capability_stdout_json(stdout: str) -> Dict[str, Any]:
    """Parse last JSON object line from script stdout (sync_pr contract)."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("script_did_not_emit_json")


@dataclass
class PrPublishRun:
    """Outcome of attempting ``cafe.pr.publish``."""

    receipt: Dict[str, Any]
    pr_synced_event: Optional[Dict[str, Any]]
    error_message: Optional[str]


def _base_receipt(
    *,
    correlation_id: str,
    capability: str,
    success: bool,
    category: Optional[str],
    code: Optional[str],
    inputs: Dict[str, Any],
    outputs: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "capability": capability,
        "correlation_id": correlation_id,
        "success": success,
        "category": category,
        "code": code,
        "inputs": redact(inputs),
        "outputs": redact(outputs),
        "finished_at": datetime.now().astimezone().isoformat(),
    }


def run_pr_publish_capability(
    *,
    repo_root: Path,
    registry: Mapping[str, Any],
    publish_request: Mapping[str, Any],
    pr_markdown_file: Path,
    timeout_sec: float = 600.0,
) -> PrPublishRun:
    """Validate registry + request, run trusted script, validate stdout JSON.

    Always returns a ``receipt`` dict suitable for ``BlackboardStore.append_capability_receipt``.
    """
    correlation_id = uuid.uuid4().hex[:20]
    cap_id = str(publish_request.get("capability") or "").strip()
    definition = registry.get(cap_id)
    if definition is None:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id or CAPABILITY_PR_PUBLISH_ID,
            success=False,
            category=VALIDATION_ERROR,
            code="unknown_capability",
            inputs=_receipt_inputs(publish_request.get("args")),
            outputs={},
        )
        return PrPublishRun(
            receipt=receipt, pr_synced_event=None, error_message="unknown_capability"
        )

    args = publish_request.get("args")
    if not isinstance(args, dict):
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=VALIDATION_ERROR,
            code="missing_args_object",
            inputs={},
            outputs={},
        )
        return PrPublishRun(
            receipt=receipt, pr_synced_event=None, error_message="missing_args_object"
        )

    args_schema = definition.get("args_schema") or {}
    if not isinstance(args_schema, dict):
        raise CapabilityRegistryError("args_schema must be a mapping")
    required_args = _schema_required(args_schema, label="args_schema")
    arg_err = _validate_string_args(args, required_args)
    if arg_err:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=VALIDATION_ERROR,
            code=arg_err,
            inputs=dict(args),
            outputs={},
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message=arg_err)

    try:
        output_arg = resolve_repo_relative_path(
            repo_root=repo_root,
            raw_path=str(args.get("output") or ""),
            field_name="output",
        )
    except ValueError as exc:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=VALIDATION_ERROR,
            code="bad_output_path",
            inputs=dict(args),
            outputs={},
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message=str(exc))

    if output_arg != pr_markdown_file.resolve():
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=VALIDATION_ERROR,
            code="output_mismatch",
            inputs=dict(args),
            outputs={},
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message="output_mismatch")

    script_ref = str(definition.get("script_ref") or "").strip()
    try:
        script_path = resolve_script_for_ref(script_ref, repo_root)
    except (OSError, ValueError) as exc:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=VALIDATION_ERROR,
            code="bad_script_ref",
            inputs=dict(args),
            outputs={},
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message=str(exc))

    cmd: List[str] = ["/bin/bash", str(script_path), "--output", str(output_arg)]
    base_arg = _resolve_publish_base(
        str(args.get("base") or ""),
        repo_root=repo_root,
    )
    if base_arg:
        cmd.extend(["--base", base_arg])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=TIMEOUT_ERROR,
            code="timeout",
            inputs=dict(args),
            outputs={},
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message="timeout")

    if result.returncode != 0:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=SCRIPT_EXIT_ERROR,
            code=f"exit_{result.returncode}",
            inputs=dict(args),
            outputs={
                "stderr": (result.stderr or "")[:4000],
                "stdout": (result.stdout or "")[:4000],
            },
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message="script_failed")

    try:
        payload = parse_capability_stdout_json(result.stdout or "")
    except ValueError:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=OUTPUT_CONTRACT_ERROR,
            code="invalid_stdout_json",
            inputs=dict(args),
            outputs={"stdout": (result.stdout or "")[:4000]},
        )
        return PrPublishRun(
            receipt=receipt, pr_synced_event=None, error_message="invalid_stdout_json"
        )

    out_schema = definition.get("expected_outputs") or {}
    if not isinstance(out_schema, dict):
        raise CapabilityRegistryError("expected_outputs must be a mapping")
    out_err = _validate_outputs(payload, out_schema)
    if out_err:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=cap_id,
            success=False,
            category=OUTPUT_CONTRACT_ERROR,
            code=out_err,
            inputs=dict(args),
            outputs=dict(payload),
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message=out_err)

    pr_url = str(payload.get("pr_url") or "").strip()
    pr_number = str(payload.get("pr_number") or "").strip()
    action = str(payload.get("action") or "synced").strip()
    receipt = _base_receipt(
        correlation_id=correlation_id,
        capability=cap_id,
        success=True,
        category=None,
        code=None,
        inputs=dict(args),
        outputs={
            "pr_url": pr_url,
            "pr_number": pr_number,
            "action": action,
        },
    )
    pr_synced = {
        "type": "pr_synced",
        "url": pr_url,
        "pr_number": pr_number,
        "action": action,
        "source": "capability",
        "display": {
            "style": "green",
            "lines": ["PR synced", f"  URL: {pr_url}"],
        },
    }
    return PrPublishRun(receipt=receipt, pr_synced_event=pr_synced, error_message=None)


class CapabilityExecutionError(RuntimeError):
    def __init__(self, category: str, code: str, outputs: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(code)
        self.category = category
        self.code = code
        self.outputs = outputs or {}


def _normalize_legacy_pr_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = dict(request)
    if str(normalized.get("capability") or "") != CAPABILITY_PR_PUBLISH_ID:
        return normalized
    if "effects" not in normalized:
        raw_args = normalized.get("args")
        args = raw_args if isinstance(raw_args, Mapping) else {}
        output = str(args.get("output") or "")
        output_parts = Path(output).parts
        try:
            issue_index = output_parts.index("issues")
            issue_dir = str(Path(*output_parts[: issue_index + 2]))
        except (ValueError, IndexError):
            issue_dir = ""
        writes = [value for value in (output, ".git", issue_dir) if value]
        network = ["github.com", "api.github.com"]
        normalized["effects"] = {
            "browser_open": [],
            "writes": writes,
            "network_destinations": network,
        }
        normalized["credentials"] = ["gh"]
        normalized["permissions"] = {"network": network, "writes": writes}
    return normalized


def _manifest_digest(manifest: CapabilityManifest) -> str:
    payload = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _audited_receipt(
    *,
    correlation_id: str,
    evaluation: CapabilityEvaluation,
    success: bool,
    category: Optional[str],
    code: Optional[str],
    outputs: Dict[str, Any],
    outcome: str,
) -> Dict[str, Any]:
    receipt = _base_receipt(
        correlation_id=correlation_id,
        capability=evaluation.request.capability,
        success=success,
        category=category,
        code=code,
        inputs=dict(evaluation.request.args),
        outputs=outputs,
    )
    receipt.update(
        {
            "execution_class": "capability",
            "trust_source": "package_registry",
            "request_fingerprint": evaluation.fingerprint,
            "manifest": {
                "id": evaluation.manifest.id,
                "version": evaluation.manifest.version,
                "digest": _manifest_digest(evaluation.manifest),
            },
            "requested_effects": evaluation.request.effects.model_dump(mode="json"),
            "allowed_effects": evaluation.allowed_effects.model_dump(mode="json"),
            "decision": {
                "outcome": evaluation.decision.value,
                "reason_code": evaluation.reason_code,
                "explanation": evaluation.explanation,
            },
            "outcome": outcome,
        }
    )
    return receipt


def validation_rejection_receipt(
    *,
    capability: str,
    code: str,
    raw_request: Optional[Mapping[str, Any]] = None,
    rejected_value: Any = None,
    rejection_source: Optional[Mapping[str, Any]] = None,
    error_detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a correlated fail-closed receipt when typed evaluation cannot start."""
    request_boundary: Dict[str, Any] = dict(raw_request) if raw_request is not None else {}
    fingerprint_payload = {
        "capability": capability,
        "request": request_boundary,
        "rejected_value": rejected_value,
        "rejection_source": dict(rejection_source) if rejection_source is not None else None,
    }
    canonical = json.dumps(
        fingerprint_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    requested_effects = request_boundary.get("effects")
    receipt = _base_receipt(
        correlation_id=uuid.uuid4().hex[:20],
        capability=capability,
        success=False,
        category=VALIDATION_ERROR,
        code=code,
        inputs=_receipt_inputs(request_boundary.get("args")),
        outputs={},
    )
    receipt.update(
        {
            "request_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "manifest": None,
            "requested_effects": (
                dict(requested_effects) if isinstance(requested_effects, Mapping) else {}
            ),
            "allowed_effects": {},
            "decision": {
                "outcome": PolicyDecision.DENY.value,
                "reason_code": code,
                "explanation": "The request could not be authorized.",
            },
            "outcome": "validation_rejection",
            "rejection": {
                "source": dict(rejection_source) if rejection_source is not None else {},
                "rejected_value": rejected_value,
                "error_detail": error_detail or code,
            },
        }
    )
    return receipt


def _non_dispatch_run(
    *,
    correlation_id: str,
    capability: str,
    fingerprint: str,
    code: str,
    decision: PolicyDecision,
    outcome: str,
    inputs: Dict[str, Any],
    evaluation: Optional[CapabilityEvaluation] = None,
) -> PrPublishRun:
    if evaluation is not None:
        receipt = _audited_receipt(
            correlation_id=correlation_id,
            evaluation=evaluation,
            success=False,
            category=VALIDATION_ERROR,
            code=code,
            outputs={},
            outcome=outcome,
        )
    else:
        receipt = _base_receipt(
            correlation_id=correlation_id,
            capability=capability,
            success=False,
            category=VALIDATION_ERROR,
            code=code,
            inputs=inputs,
            outputs={},
        )
        receipt.update(
            {
                "request_fingerprint": fingerprint,
                "manifest": None,
                "requested_effects": {},
                "allowed_effects": {},
                "decision": {
                    "outcome": decision.value,
                    "reason_code": code,
                    "explanation": "The request could not be authorized.",
                },
                "outcome": outcome,
            }
        )
    return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message=code)


def _validate_typed_outputs(outputs: Mapping[str, Any], schema: ObjectSchema) -> Optional[str]:
    if not set(schema.required).issubset(outputs):
        return "missing_output"
    if not set(outputs).issubset(schema.properties):
        return "undeclared_output"
    python_types = {"string": str, "integer": int, "boolean": bool}
    for key, value in outputs.items():
        field = schema.properties[key]
        if type(value) is not python_types[field.type]:
            return "output_type_invalid"
        if field.enum is not None and value not in field.enum:
            return "output_value_invalid"
    return None


def _sync_pr_adapter(
    *,
    repo_root: Path,
    request: ExecutionRequest,
    manifest: CapabilityManifest,
    output_file: Path,
    timeout_sec: float,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    legacy_definition = {
        "id": manifest.id,
        "script_ref": manifest.implementation,
        "args_schema": manifest.arguments.model_dump(mode="json"),
        "expected_outputs": manifest.outputs.model_dump(mode="json"),
    }
    run = run_pr_publish_capability(
        repo_root=repo_root,
        registry={manifest.id: legacy_definition},
        publish_request={"capability": manifest.id, "args": dict(request.args)},
        pr_markdown_file=output_file,
        timeout_sec=timeout_sec,
    )
    if not run.receipt.get("success"):
        raise CapabilityExecutionError(
            str(run.receipt.get("category") or VALIDATION_ERROR),
            str(run.receipt.get("code") or "execution_failed"),
            dict(run.receipt.get("outputs") or {}),
        )
    return dict(run.receipt.get("outputs") or {}), run.pr_synced_event


def _current_repo_slug(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise CapabilityExecutionError(VALIDATION_ERROR, "repo_resolution_failed") from exc
    if result.returncode != 0:
        raise CapabilityExecutionError(VALIDATION_ERROR, "repo_resolution_failed")
    remote = str(result.stdout or "").strip()
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?",
        remote,
    )
    if match is None:
        raise CapabilityExecutionError(VALIDATION_ERROR, "repo_resolution_failed")
    return match.group(1)


def _canonical_current_pr_url(url: str, repo_slug: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        return False
    expected = repo_slug.split("/")
    parts = [part for part in parsed.path.split("/") if part]
    return (
        len(expected) == 2
        and len(parts) == 4
        and parts[:2] == expected
        and parts[2] == "pull"
        and parts[3].isdigit()
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _open_current_pr_adapter(
    *,
    repo_root: Path,
    request: ExecutionRequest,
    manifest: CapabilityManifest,
    output_file: Path,
    timeout_sec: float,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    del manifest, output_file, timeout_sec
    if request.args != {"target_ref": "current_pr"}:
        raise CapabilityExecutionError(VALIDATION_ERROR, "target_not_allowed")
    try:
        resolved_url = GitHubOps().get_current_pr_url()
        repo_slug = _current_repo_slug(repo_root)
    except Exception as exc:
        if isinstance(exc, CapabilityExecutionError):
            raise
        raise CapabilityExecutionError(VALIDATION_ERROR, "target_resolution_failed") from exc
    if not _canonical_current_pr_url(resolved_url, repo_slug):
        raise CapabilityExecutionError(VALIDATION_ERROR, "resolved_target_not_allowed")
    if not sys.stdin.isatty():
        return {"opened": False}, None
    try:
        webbrowser.open(resolved_url)
    except Exception as exc:
        raise CapabilityExecutionError("adapter_error", "browser_open_failed") from exc
    return {"opened": True, "url": resolved_url}, {
        "type": "pr_link_opened",
        "url": resolved_url,
    }


def _sync_issue_comment_adapter(
    *,
    repo_root: Path,
    request: ExecutionRequest,
    manifest: CapabilityManifest,
    output_file: Path,
    timeout_sec: float,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    del manifest, timeout_sec
    phase = str(request.args.get("phase") or "")
    if phase not in {"spec", "plan"}:
        raise CapabilityExecutionError(VALIDATION_ERROR, "phase_not_allowed")
    try:
        output = resolve_repo_relative_path(
            repo_root=repo_root, raw_path=str(request.args.get("output") or ""), field_name="output"
        )
        output.relative_to(repo_root.resolve())
        if output.resolve() != output_file.resolve():
            raise ValueError("output_mismatch")
        issue_dir = output.parents[2]
        issue_config = yaml.safe_load((issue_dir / "issue.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, IndexError, yaml.YAMLError) as exc:
        raise CapabilityExecutionError(VALIDATION_ERROR, "issue_context_invalid") from exc
    phase_config = issue_config.get(phase) or {}
    issue_id = str((issue_config.get("spec") or {}).get("issue_id") or "")
    if not phase_config.get("sync_github"):
        return {"action": "skipped"}, None
    if not issue_id:
        raise CapabilityExecutionError(VALIDATION_ERROR, "issue_id_missing")
    if str(request.args.get("issue_id") or "") != issue_id:
        raise CapabilityExecutionError(VALIDATION_ERROR, "issue_destination_mismatch")
    artifact_bytes = output.read_bytes()
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    if str(request.args.get("artifact_sha256") or "") != artifact_sha256:
        raise CapabilityExecutionError(VALIDATION_ERROR, "artifact_identity_mismatch")
    header = (
        "### 📋 Requirements Specification (Confirmed)"
        if phase == "spec"
        else "### 📝 Implementation Plan (Confirmed)"
    )
    try:
        GitHubOps().add_issue_comment(issue_id, f"{header}\n\n{artifact_bytes.decode('utf-8')}")
    except Exception as exc:
        raise CapabilityExecutionError("adapter_error", "issue_comment_failed") from exc
    return {"action": "commented", "issue_id": issue_id, "phase": phase}, {
        "type": "issue_comment_synced",
        "issue_id": issue_id,
        "phase": phase,
    }


def _notify_slack_human_task_adapter(
    *,
    repo_root: Path,
    request: ExecutionRequest,
    manifest: CapabilityManifest,
    output_file: Path,
    timeout_sec: float,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Deliver one package-owned HumanTask notification without exposing credentials."""
    from cafe.core.human_task_notifications import (
        SlackNotificationError,
        build_human_task_message,
        load_slack_webhook_url,
        post_slack_notification,
    )

    del repo_root, manifest, output_file
    message = build_human_task_message(
        repository=str(request.args["repository"]),
        workflow_id=str(request.args["workflow_id"]),
        task_id=str(request.args["task_id"]),
        step=str(request.args["step"]),
        task_type=str(request.args["task_type"]),
    )
    try:
        webhook_url = load_slack_webhook_url()
        post_slack_notification(webhook_url, message, timeout_sec=timeout_sec)
    except SlackNotificationError as exc:
        raise CapabilityExecutionError(exc.category, exc.code) from exc
    return {
        "delivered": True,
        "workflow_id": message.workflow_id,
        "task_id": message.task_id,
    }, None


HOST_CAPABILITY_ADAPTERS: Mapping[str, Any] = {
    "sync_pr": _sync_pr_adapter,
    "open_current_pr": _open_current_pr_adapter,
    "sync_issue_comment": _sync_issue_comment_adapter,
    "notify_slack_human_task": _notify_slack_human_task_adapter,
}


def run_capability_request(
    *,
    repo_root: Path,
    registry: Mapping[str, Any],
    capability_request: Mapping[str, Any],
    output_file: Path,
    timeout_sec: float = 600.0,
    trusted_human_task_notification: bool = False,
) -> PrPublishRun:
    """Evaluate and dispatch one request through the host-owned adapter allow-list.

    Slack HumanTask delivery is an internal consequence of durable task
    materialization, rather than a generic project-declared capability.
    """
    correlation_id = uuid.uuid4().hex[:20]
    raw_request = _normalize_legacy_pr_request(capability_request)
    cap_id = str(raw_request.get("capability") or "").strip()
    raw_fingerprint = hashlib.sha256(
        json.dumps(raw_request, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    if cap_id == CAPABILITY_SLACK_HUMAN_TASK_ID and not trusted_human_task_notification:
        return _non_dispatch_run(
            correlation_id=correlation_id,
            capability=cap_id,
            fingerprint=raw_fingerprint,
            code="human_task_notification_not_workflow_owned",
            decision=PolicyDecision.DENY,
            outcome="policy_denied",
            inputs={},
        )

    raw_manifest = registry.get(cap_id)
    if raw_manifest is None:
        return _non_dispatch_run(
            correlation_id=correlation_id,
            capability=cap_id or "unknown",
            fingerprint=raw_fingerprint,
            code="unknown_capability",
            decision=PolicyDecision.DENY,
            outcome="validation_rejection",
            inputs=_receipt_inputs(raw_request.get("args")),
        )
    if not isinstance(raw_manifest, CapabilityManifest):
        return _non_dispatch_run(
            correlation_id=correlation_id,
            capability=cap_id or "unknown",
            fingerprint=raw_fingerprint,
            code="unsupported_capability",
            decision=PolicyDecision.DENY,
            outcome="validation_rejection",
            inputs=_receipt_inputs(raw_request.get("args")),
        )

    try:
        request = ExecutionRequest.model_validate(raw_request)
    except ValidationError:
        return _non_dispatch_run(
            correlation_id=correlation_id,
            capability=cap_id or "unknown",
            fingerprint=raw_fingerprint,
            code="malformed_request",
            decision=PolicyDecision.DENY,
            outcome="validation_rejection",
            inputs=_receipt_inputs(raw_request.get("args")),
        )

    evaluation = evaluate_capability_request(registry, raw_request)
    if evaluation.decision != PolicyDecision.ALLOW:
        outcome = (
            "approval_required"
            if evaluation.decision == PolicyDecision.REQUIRE_APPROVAL
            else "policy_denied"
        )
        return _non_dispatch_run(
            correlation_id=correlation_id,
            capability=request.capability,
            fingerprint=evaluation.fingerprint,
            code=evaluation.reason_code,
            decision=evaluation.decision,
            outcome=outcome,
            inputs=dict(request.args),
            evaluation=evaluation,
        )

    return dispatch_revalidated_capability_request(
        repo_root=repo_root,
        evaluation=evaluation,
        output_file=output_file,
        timeout_sec=timeout_sec,
        correlation_id=correlation_id,
    )


def dispatch_revalidated_capability_request(
    *,
    repo_root: Path,
    evaluation: CapabilityEvaluation,
    output_file: Path,
    timeout_sec: float = 600.0,
    correlation_id: Optional[str] = None,
) -> PrPublishRun:
    """Dispatch one exact evaluation after its caller has established authorization."""
    correlation_id = correlation_id or uuid.uuid4().hex[:20]
    request = evaluation.request
    manifest = evaluation.manifest
    if evaluation.decision not in {PolicyDecision.ALLOW, PolicyDecision.REQUIRE_APPROVAL}:
        return _non_dispatch_run(
            correlation_id=correlation_id,
            capability=request.capability,
            fingerprint=evaluation.fingerprint,
            code=evaluation.reason_code,
            decision=evaluation.decision,
            outcome="policy_denied",
            inputs=dict(request.args),
            evaluation=evaluation,
        )

    adapter = HOST_CAPABILITY_ADAPTERS.get(manifest.implementation)
    if adapter is None:
        return _non_dispatch_run(
            correlation_id=correlation_id,
            capability=request.capability,
            fingerprint=evaluation.fingerprint,
            code="unsupported_implementation",
            decision=PolicyDecision.DENY,
            outcome="validation_rejection",
            inputs=dict(request.args),
            evaluation=evaluation,
        )

    try:
        outputs, event = adapter(
            repo_root=repo_root,
            request=request,
            manifest=manifest,
            output_file=output_file,
            timeout_sec=timeout_sec,
        )
    except CapabilityExecutionError as exc:
        receipt = _audited_receipt(
            correlation_id=correlation_id,
            evaluation=evaluation,
            success=False,
            category=exc.category,
            code=exc.code,
            outputs=exc.outputs,
            outcome="execution_failure",
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message=exc.code)
    output_error = _validate_typed_outputs(outputs, manifest.outputs)
    if output_error:
        receipt = _audited_receipt(
            correlation_id=correlation_id,
            evaluation=evaluation,
            success=False,
            category=OUTPUT_CONTRACT_ERROR,
            code=output_error,
            outputs=outputs,
            outcome="execution_failure",
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message=output_error)

    receipt = _audited_receipt(
        correlation_id=correlation_id,
        evaluation=evaluation,
        success=True,
        category=None,
        code=None,
        outputs=outputs,
        outcome="success",
    )
    return PrPublishRun(receipt=receipt, pr_synced_event=event, error_message=None)


def capability_receipt_hook_event(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    """Slim event payload for ``StepExecutionResult.events`` (workflow runtime gate)."""
    return {
        "type": "capability_receipt",
        "capability": receipt.get("capability"),
        "success": bool(receipt.get("success")),
        "correlation_id": receipt.get("correlation_id"),
        "category": receipt.get("category"),
        "code": receipt.get("code"),
    }

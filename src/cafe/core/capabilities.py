"""File-backed capability registry and host-side PR publish execution."""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml

from cafe.skills.loader import SkillLoader

CAPABILITY_PR_PUBLISH_ID = "cafe.pr.publish"

# Failure categories surfaced on capability receipts (distinct from validation_error).
SCRIPT_EXIT_ERROR = "script_exit_error"
OUTPUT_CONTRACT_ERROR = "output_contract_error"
TIMEOUT_ERROR = "timeout_error"
VALIDATION_ERROR = "validation_error"


class CapabilityRegistryError(ValueError):
    """Raised when capability definitions cannot be loaded."""


def _package_capabilities_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "capabilities"


def default_capability_definition_dirs(repo_root: Path) -> List[Path]:
    """Repository overrides first, then packaged defaults."""
    return [repo_root / "data" / "capabilities", _package_capabilities_dir()]


def load_capability_registry(capabilities_dirs: Sequence[Path]) -> Dict[str, Dict[str, Any]]:
    """Load all *.yaml / *.yml / *.json capability definitions.

    Duplicate ``id`` values across files are rejected with both paths listed.
    """
    merged: Dict[str, Tuple[Dict[str, Any], Path]] = {}
    for base in capabilities_dirs:
        if not base.is_dir():
            continue
        paths = sorted(base.glob("*.yaml")) + sorted(base.glob("*.yml")) + sorted(base.glob("*.json"))
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
            cap_id = str(data.get("id") or "").strip()
            if not cap_id:
                raise CapabilityRegistryError(f"Capability missing id: {path}")
            if cap_id in merged:
                raise CapabilityRegistryError(
                    f"Duplicate capability id {cap_id!r}: {merged[cap_id][1]} and {path}"
                )
            merged[cap_id] = (data, path)
    return {key: value[0] for key, value in merged.items()}


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


def _validate_outputs(payload: Mapping[str, Any], schema: Mapping[str, Any]) -> Optional[str]:
    required = _schema_required(schema, label="expected_outputs")
    for key in required:
        if key not in payload or str(payload.get(key) or "").strip() == "":
            return f"missing_output:{key}"
    return None


def resolve_repo_relative_path(*, repo_root: Path, raw_path: str, field_name: str) -> Path:
    if not str(raw_path).strip():
        raise ValueError(f"missing_{field_name}")
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def resolve_sync_pr_script(repo_root: Path) -> Path:
    """Resolve packaged ``sync_pr.sh`` (same search order as GitHubPRCreator)."""
    loader = SkillLoader(project_root=repo_root)
    skill_dir = loader.get_skill_dir("pr")
    script_path = skill_dir / "scripts" / "sync_pr.sh"
    if script_path.exists():
        return script_path
    fallback = Path(__file__).resolve().parents[1] / "data" / "skills" / "pr" / "scripts" / "sync_pr.sh"
    if fallback.exists():
        return fallback
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
        "inputs": inputs,
        "outputs": outputs,
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
            inputs=dict(publish_request.get("args") or {}),
            outputs={},
        )
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message="unknown_capability")

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
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message="missing_args_object")

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
    base_arg = str(args.get("base") or "").strip()
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
            outputs={"stderr": (result.stderr or "")[:4000], "stdout": (result.stdout or "")[:4000]},
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
        return PrPublishRun(receipt=receipt, pr_synced_event=None, error_message="invalid_stdout_json")

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
    }
    return PrPublishRun(receipt=receipt, pr_synced_event=pr_synced, error_message=None)


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

"""Staged preparation inputs and the existing formatter adapter boundary."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _kickoff_store import VersionedJsonStore, repository_identity

_ALLOWED_FIELDS = {
    "playbook_id", "project_root", "issue_name", "delivery_contract", "deliver", "cleanup",
    "deliver_description", "cleanup_description", "update_preflight", "catalog_preflight",
    "manager_mode", "poll_interval_seconds", "event_manager", "phase_chain", "phase_config",
    "effective_locale", "locale_source", "repository_content_locale", "capability_choice",
    "user_required", "manager_confirmable", "worktree", "current_checkout",
    "task_user_required", "task_manager_confirmable", "need_permission", "need_clarification",
    "alignment_checkpoint", "proactive_review_decision",
}
_REQUIRED_FIELDS = {
    "playbook_id", "issue_name", "delivery_contract", "deliver", "cleanup",
    "update_preflight", "catalog_preflight", "repository_content_locale",
}


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def normalize_formatter_inputs(values: dict[str, Any]) -> dict[str, Any]:
    """Validate normalized fields without collapsing absent, false, or empty values."""
    if not isinstance(values, dict):
        return {"status": "invalid", "diagnostics": ["formatter_inputs_must_be_an_object"], "missing": []}
    prohibited = {
        "activate", "activate_confirmed", "workflow_id", "confirmed_by", "confirmed_at",
        "issue_dir", "shell", "shell_command", "argv_override",
    }
    found_prohibited = sorted(prohibited.intersection(values))
    unknown = sorted(set(values) - _ALLOWED_FIELDS - prohibited)
    if found_prohibited or unknown:
        return {
            "status": "invalid",
            "diagnostics": [
                *(f"activation_or_authority_field_rejected:{key}" for key in found_prohibited),
                *(f"unknown_formatter_field:{key}" for key in unknown),
            ],
            "missing": [],
        }
    missing = sorted(key for key in _REQUIRED_FIELDS if key not in values or values[key] is None)
    if "worktree" not in values and values.get("current_checkout") is not True:
        missing.append("checkout")
    diagnostics: list[str] = []
    if "playbook_id" in values and (not isinstance(values["playbook_id"], str) or not values["playbook_id"].strip()):
        diagnostics.append("playbook_id_must_be_nonempty")
    if "issue_name" in values and (not isinstance(values["issue_name"], str) or not values["issue_name"].strip()):
        diagnostics.append("issue_name_must_be_nonempty")
    if "delivery_contract" in values and not isinstance(values["delivery_contract"], dict):
        diagnostics.append("delivery_contract_must_be_an_object")
    for key in ("update_preflight", "catalog_preflight"):
        if key in values and not isinstance(values[key], dict):
            diagnostics.append(f"{key}_must_be_an_object")
    for key in ("deliver", "cleanup"):
        value = values.get(key)
        if value is not None and (
            not isinstance(value, list)
            or any(not _is_string_list(argv) or not argv for argv in value)
        ):
            diagnostics.append(f"{key}_must_be_an_array_of_nonempty_argv_arrays")
    for key in (
        "deliver_description", "cleanup_description", "event_manager", "phase_chain",
        "capability_choice", "user_required", "manager_confirmable", "task_user_required",
        "task_manager_confirmable", "proactive_review_decision",
    ):
        if key in values and not _is_string_list(values[key]):
            diagnostics.append(f"{key}_must_be_a_string_array")
    if "current_checkout" in values and type(values["current_checkout"]) is not bool:
        diagnostics.append("current_checkout_must_be_boolean")
    if "worktree" in values and (not isinstance(values["worktree"], str) or not values["worktree"].strip()):
        diagnostics.append("worktree_must_be_nonempty")
    try:
        safe_values = json.loads(json.dumps(values, ensure_ascii=False))
    except (TypeError, ValueError):
        safe_values = {}
        diagnostics.append("formatter_inputs_must_be_json_serializable")
    return {
        "status": "invalid" if diagnostics else "incomplete" if missing else "ready",
        "values": safe_values,
        "missing": sorted(set(missing)),
        "diagnostics": diagnostics,
    }


def formatter_argv(values: dict[str, Any]) -> list[str]:
    normalized = normalize_formatter_inputs(values)
    if normalized["status"] != "ready":
        raise ValueError("formatter inputs are incomplete or invalid")
    data = normalized["values"]
    args = [data["playbook_id"], "--issue-name", data["issue_name"]]
    if data.get("project_root") is not None:
        args.extend(["--project-root", str(data["project_root"])])
    json_flags = {
        "delivery_contract": "--delivery-contract", "deliver": "--deliver", "cleanup": "--cleanup",
        "update_preflight": "--update-preflight", "catalog_preflight": "--catalog-preflight",
    }
    for key, flag in json_flags.items():
        args.extend([flag, json.dumps(data[key], ensure_ascii=False, separators=(",", ":"))])
    repeated_flags = {
        "deliver_description": "--deliver-description", "cleanup_description": "--cleanup-description",
        "event_manager": "--event-manager", "phase_chain": "--phase-chain",
        "capability_choice": "--capability-choice", "task_user_required": "--task-user-required",
        "task_manager_confirmable": "--task-manager-confirmable",
        "proactive_review_decision": "--proactive-review-decision",
    }
    for key, flag in repeated_flags.items():
        for value in data.get(key, []):
            args.extend([flag, value])
    list_flags = {
        "user_required": "--user-required", "manager_confirmable": "--manager-confirmable",
    }
    for key, flag in list_flags.items():
        if key in data:
            args.append(flag)
            args.extend(data[key])
    scalar_flags = {
        "manager_mode": "--manager-mode", "poll_interval_seconds": "--poll-interval-seconds",
        "phase_config": "--phase-config", "effective_locale": "--effective-locale",
        "locale_source": "--locale-source", "repository_content_locale": "--repository-content-locale",
        "need_permission": "--need-permission", "need_clarification": "--need-clarification",
        "alignment_checkpoint": "--alignment-checkpoint",
    }
    for key, flag in scalar_flags.items():
        value = data.get(key)
        if value is not None:
            args.extend([flag, str(value)])
    if data.get("current_checkout") is True:
        args.append("--current-checkout")
    elif data.get("worktree") is not None:
        args.extend(["--worktree", data["worktree"]])
    return args


def _load_local_module(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"kickoff_inputs_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Kickoff component is unavailable: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _default_config_root() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".config"
    return base / "cafe" / "kickoff"


def _default_cache_root() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return base / "cafe" / "kickoff" / "v1"


def discover_kickoff(
    request: dict[str, Any], *, config_dir: Path | None = None, cache_dir: Path | None = None
) -> dict[str, Any]:
    """Gather reusable inputs while leaving issue assessment and selection to Manager."""
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        return {"stage": "discovery", "status": "invalid", "diagnostics": ["unsupported_request_schema"]}
    project_root = Path(request.get("project_root", Path.cwd())).expanduser().resolve()
    issue_name = request.get("issue_name")
    if not isinstance(issue_name, str) or not issue_name.strip():
        return {"stage": "discovery", "status": "invalid", "diagnostics": ["issue_name_required"]}
    from cafe.catalogs.resolver import CatalogResolver

    resolver = CatalogResolver(project_root=project_root)
    catalog = _load_local_module("kickoff_catalog").discover_index(
        project_root=project_root,
        global_root=resolver.global_root,
        builtin_root=resolver.builtin_root,
        cache_file=(cache_dir or _default_cache_root()) / "catalog-v1.json",
    )
    preference_store = _load_local_module("kickoff_preferences").PreferenceStore(
        config_dir or _default_config_root(), repository_root=project_root
    )
    current = request.get("current_explicit_inputs", {})
    current = current if isinstance(current, dict) else {}
    preferences = {}
    for key, input_key in {
        "manager.mode": "manager_mode", "conversation.locale": "effective_locale",
        "worktree.convention": "worktree",
    }.items():
        resolved = preference_store.effective(key, explicit=current.get(input_key))
        preferences[key] = {"value": resolved.value, "scope": resolved.scope, "origin": resolved.origin}
    delivery_module = _load_local_module("kickoff_delivery")
    now = datetime.now(timezone.utc)
    delivery_cache = VersionedJsonStore(
        (cache_dir or _default_cache_root()) / "delivery-v1.json",
        schema_version=1,
        collection="evidence",
    ).read()
    delivery_record = request.get("delivery_evidence") or delivery_cache.get(
        repository_identity(project_root)
    )
    delivery = (
        delivery_module.assess_delivery(delivery_record, project_root=project_root, now=now)
        if isinstance(delivery_record, dict)
        else {
            "status": "miss", "diagnostics": ["delivery_evidence_missing"], "discovery_gap": True,
            "manifest": delivery_module.discover_delivery_manifest(project_root),
        }
    )
    model_module = _load_local_module("kickoff_models")
    model_cache = VersionedJsonStore(
        (cache_dir or _default_cache_root()) / "models-v1.json",
        schema_version=1,
        collection="evidence",
    ).read()
    model_records = request.get("model_assessments", [])
    if not model_records:
        model_records = list(model_cache.values())
    models = [
        model_module.assess_model_evidence(
            assessment, now=now, current_sources=request.get("current_model_sources"),
            contradictions=request.get("model_contradictions"),
        )
        for assessment in model_records
        if isinstance(assessment, dict)
    ]
    selected = request.get("playbook_id")
    selected_candidate = next(
        (item for item in catalog.get("candidates", []) if item.get("id") == selected), None
    ) if isinstance(selected, str) else None
    return {
        "stage": "discovery", "status": "partial" if catalog.get("diagnostics") else "ready",
        "preferences": preferences, "catalog": catalog, "selected_candidate": selected_candidate,
        "delivery": delivery, "models": models, "diagnostics": list(catalog.get("diagnostics", [])),
    }

def assemble_kickoff(
    request: dict[str, Any], *, preference_store: Any = None,
    discovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        return {"status": "invalid", "selected_playbook": None, "diagnostics": ["unsupported_request_schema"], "missing_decisions": [], "formatter_inputs": None}
    selected = request.get("playbook_id")
    if not isinstance(selected, str) or not selected.strip():
        selected = None
    missing: list[dict[str, str]] = []
    if selected is None:
        missing.append({"owner": "manager_decision", "requirement": "select an effective playbook"})
    decisions = request.get("manager_decisions", {})
    if not isinstance(decisions, dict):
        decisions = {}
    for requirement in request.get("required_decisions", []):
        if isinstance(requirement, str) and requirement not in decisions:
            missing.append({"owner": "manager_decision", "requirement": requirement})
    if discovery is not None and selected is not None:
        catalog = discovery.get("catalog", {})
        candidates = catalog.get("candidates", []) if isinstance(catalog, dict) else []
        selected_candidate = next((item for item in candidates if item.get("id") == selected), None)
        if selected_candidate is None or not selected_candidate.get("eligible"):
            missing.append({"owner": "manager_decision", "requirement": "resolve an eligible selected playbook"})
    raw_inputs = request.get("formatter_inputs")
    if isinstance(raw_inputs, dict):
        raw_inputs = dict(raw_inputs)
    preference_report: dict[str, Any] = {}
    if preference_store is not None and isinstance(raw_inputs, dict):
        preference_mapping = {
            "manager.mode": "manager_mode",
            "conversation.locale": "effective_locale",
        }
        explicit = request.get("current_explicit_inputs", {})
        explicit = explicit if isinstance(explicit, dict) else {}
        for preference_key, input_key in preference_mapping.items():
            if input_key in raw_inputs:
                continue
            resolved = preference_store.effective(preference_key, explicit=explicit.get(input_key))
            preference_report[preference_key] = {
                "value": resolved.value,
                "scope": resolved.scope,
                "origin": resolved.origin,
            }
            if resolved.value is not None:
                raw_inputs[input_key] = resolved.value
    preflight_files = request.get("preflight_files", {})
    if isinstance(preflight_files, dict) and isinstance(raw_inputs, dict):
        for file_key, input_key in (("update", "update_preflight"), ("catalog", "catalog_preflight")):
            reference = preflight_files.get(file_key)
            if input_key not in raw_inputs and isinstance(reference, str):
                try:
                    raw_inputs[input_key] = json.loads(Path(reference).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    missing.append({"owner": "manager_research", "requirement": f"read {file_key} preflight report"})
    normalized = normalize_formatter_inputs(raw_inputs) if isinstance(raw_inputs, dict) else None
    if selected is not None and isinstance(raw_inputs, dict) and raw_inputs.get("playbook_id") not in {None, selected}:
        return {
            "status": "invalid", "selected_playbook": selected,
            "diagnostics": ["formatter_playbook_does_not_match_selected_graph"],
            "missing_decisions": missing, "formatter_inputs": None,
        }
    if normalized is not None and normalized["status"] != "ready":
        missing.extend(
            {"owner": "manager_decision", "requirement": f"formatter input: {key}"}
            for key in normalized.get("missing", [])
        )
    complete = selected is not None and not missing and normalized is not None and normalized["status"] == "ready"
    return {
        "status": "ready" if complete else "incomplete",
        "selected_playbook": selected,
        "selected_candidate": None if discovery is None else discovery.get("selected_candidate"),
        "preferences": preference_report,
        "diagnostics": [] if normalized is None else normalized.get("diagnostics", []),
        "missing_decisions": missing,
        "formatter_inputs": normalized["values"] if complete else None,
    }


def render_kickoff(values: dict[str, Any]) -> dict[str, Any]:
    normalized = values if isinstance(values, dict) and values.get("status") in {"ready", "incomplete", "invalid"} else normalize_formatter_inputs(values)
    if normalized.get("status") != "ready":
        return {
            "status": normalized.get("status", "invalid"),
            "missing": normalized.get("missing", []),
            "diagnostics": normalized.get("diagnostics", []),
        }
    formatter_path = Path(__file__).with_name("format_kickoff_contract.py")
    spec = importlib.util.spec_from_file_location("kickoff_existing_formatter", formatter_path)
    if spec is None or spec.loader is None:
        return {"status": "invalid", "diagnostics": ["existing_formatter_unavailable"]}
    formatter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = formatter
    spec.loader.exec_module(formatter)
    try:
        args = formatter._parser().parse_args(formatter_argv(normalized["values"]))
        proposal = formatter.build_confirmed_proposal(args)
        output = formatter.render(args, confirmed_proposal=proposal)
    except (SystemExit, OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "invalid", "diagnostics": [type(exc).__name__]}
    return {"status": "rendered", "proposal": proposal, "output": output}

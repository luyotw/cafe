"""Staged preparation inputs and the existing formatter adapter boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
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


_JSON_FLAGS = {
    "delivery_contract": "--delivery-contract", "deliver": "--deliver", "cleanup": "--cleanup",
    "update_preflight": "--update-preflight", "catalog_preflight": "--catalog-preflight",
}
_REPEATED_FLAGS = {
    "deliver_description": "--deliver-description", "cleanup_description": "--cleanup-description",
    "event_manager": "--event-manager", "phase_chain": "--phase-chain",
    "capability_choice": "--capability-choice", "task_user_required": "--task-user-required",
    "task_manager_confirmable": "--task-manager-confirmable",
    "proactive_review_decision": "--proactive-review-decision",
}
_LIST_FLAGS = {
    "user_required": "--user-required", "manager_confirmable": "--manager-confirmable",
}
_SCALAR_FLAGS = {
    "manager_mode": "--manager-mode", "poll_interval_seconds": "--poll-interval-seconds",
    "phase_config": "--phase-config", "effective_locale": "--effective-locale",
    "locale_source": "--locale-source", "repository_content_locale": "--repository-content-locale",
    "need_permission": "--need-permission", "need_clarification": "--need-clarification",
    "alignment_checkpoint": "--alignment-checkpoint",
}

def formatter_field_schema() -> dict[str, Any]:
    """Project the adapter's encoding and existing parser choices for public callers."""
    owner = {action.dest: action for action in _load_local_module("format_kickoff_contract")._parser()._actions}
    properties = {}
    for field in sorted(_ALLOWED_FIELDS):
        if field in _REPEATED_FLAGS or field in _LIST_FLAGS:
            shape = {"type": "array", "items": {"type": "string"}}
        elif field in ("deliver", "cleanup"):
            shape = {"type": "array", "items": {"type": "array", "minItems": 1, "items": {"type": "string"}}}
        elif field in _JSON_FLAGS:
            shape = {"type": "object"}
        elif field == "current_checkout":
            shape = {"type": "boolean"}
        elif field == "poll_interval_seconds":
            shape = {"type": "integer", "minimum": 1}
        else:
            shape = {"type": "string"}
        action = owner[field]
        if action.choices is not None:
            shape["enum"] = list(action.choices)
        properties[field] = shape
    return properties


def request_schema() -> dict[str, Any]:
    """Describe the public adapter without creating a second formatter schema."""
    from cafe.manager.delivery import DeliveryContractV3, validate_closeout_plan_policy

    contract_schema = DeliveryContractV3.model_json_schema()
    contract_schema["properties"].pop("closeout_plan")
    contract_schema["required"].remove("closeout_plan")
    contract_schema.pop("$defs", None)
    contract_template = {
        key: 3 if key == "schema_version" else [] if field.get("type") == "array" else ""
        for key, field in contract_schema["properties"].items()
    }
    closeout_examples = []
    for argv in (["cafe", "close"], ["cafe", "close", "--archive-only"], ["cafe", "close", "--squash"]):
        example = {"argv": argv, "valid_in_pr_mode": True}
        try:
            validate_closeout_plan_policy({"deliver": [], "cleanup": [{"argv": argv}]}, allow_squash=False)
        except ValueError as exc:
            example.update(valid_in_pr_mode=False, diagnostic=str(exc))
        closeout_examples.append(example)
    return {
        "schema_version": 1,
        "delivery_contract": contract_schema,
        "input_template": {"delivery_contract": contract_template, "deliver": None, "cleanup": None,
                           "phase_chain": [], "capability_choice": []},
        "closeout_examples": closeout_examples,
        "action_input_examples": {
            "described_action": {"actions": [["<executable>", "<literal argument>"]],
                                 "descriptions": ["<current purpose of this command>"]},
            "no_actions": {"actions": [], "descriptions": []},
        },
        # Adapter examples only: _preflight_reports remains the validation owner.
        # None marks absent evidence, never a fabricated token, time or success.
        "preflight_report_examples": {
            "update": {"checked_at": None, "status": None, "installed_version": None,
                       "latest_version": None, "decision": None, "comparison_token": None,
                       "post_change_evidence": None},
            "catalog": {"checked_at": None, "status": None, "comparison_token": None,
                        "effective_digests": {"playbook": None, "phase": None, "agent": None},
                        "decision": None, "post_change_evidence": None},
        },
        "preflight_capture": {
            "command": "prepare_kickoff.py capture-report --request-file <draft.json> --kind <update|catalog> --report-output <report.json> --checked-at <actual timezone-qualified observation time>",
            "stdin": "Complete original check JSON, piped from its first execution; capture executes no check and invents no timestamp.",
            "result": "Original bytes saved; draft references the file and actual time. decision/post_change_evidence stay null until Manager resolves them. Read captured files for status; do not rerun merely to recover output.",
        },
        "preflight_file_adapter": {
            "files": "preflight_files.update/catalog accept full original check JSON or existing formatter-ready reports.",
            "metadata": {"checked_at": None, "decision": None, "post_change_evidence": None},
            "use": "For raw check JSON supply preflight_metadata.update/catalog with exactly the actual checked_at and current decision/post_change_evidence. The helper maps update token and catalog_check, retains complete source fields and rejects conflicting/extra metadata. It never executes checks or invents evidence. Existing formatter validation remains authoritative.",
        },
        "preflight_example_use": "These are field shapes, not valid evidence or defaults. Retain the full original report including optional diagnostics/mismatch IDs. Supply the actual check timestamp and current decision; copy source tokens/digests without invention. Explicit null can record an actually unavailable source value, not a successful check. Existing formatter validation remains authoritative.",
        "template_rules": {
            "null": "Unresolved: replace with a deliberate value; never rendered as a default.",
            "delivery_contract": "Fill all product decisions, including intentionally empty lists. closeout_plan is added by the formatter.",
            "actions": "deliver/cleanup are literal argv arrays, not command objects or shell strings. Empty arrays require an explicit current decision.",
            "action_descriptions": "deliver_description/cleanup_description are string arrays with exactly one nonempty explanation per command. An empty action array requires an empty description array; put resource-retention rationale in delivery_contract.constraints instead. Action examples describe shapes, never permission.",
            "closeout": "Examples validate syntax only, never recommend or authorize an action. cafe close must be last cleanup; archive-only is a separate terminal action, not a closeout_plan command.",
            "preflight": "Pass full existing reports through preflight_files. Missing tokens/dates/decisions must be resolved through their owner, never synthesized.",
        },
        "formatter_fields": sorted(_ALLOWED_FIELDS),
        "formatter_field_schema": formatter_field_schema(),
        "required_formatter_fields": sorted(_REQUIRED_FIELDS),
        "checkout_choice": ["worktree", "current_checkout=true"],
        "request_example": {
            "schema_version": 1, "project_root": "/work/project", "issue_name": "new-issue",
            "playbook_id": "<current selection>",
            "preflight_files": {"update": "/tmp/update.json", "catalog": "/tmp/catalog.json"},
            "formatter_inputs": {"effective_locale": "zh-TW", "locale_source": "explicit", "repository_content_locale": "en-US"},
        },
        "issue_id": "An explicit positive numeric issue ID supplies issue<id> when issue_name is absent. It is never inferred from an issue-like name.",
        "manager_cli": "Current calling Manager CLI; context, not a model choice. Codex sessions are also recognized by CODEX_THREAD_ID.",
        "generated_inputs": "Helper-owned provenance map retained in editable requests: field -> origin, dependency and value_fingerprint. Preserve it when editing formatter_inputs; unchanged source-backed values are revalidated at render. For deliberate same-value reassessment after invalidation, see kickoff_input_reference.md. It grants no authority.",
        "decision_examples": {
            "phase_chain": ["develop=codex:<exact-model>"],
            "capability_choice": ["pr.auto_create=true"],
            "deliver": [["<executable>", "<literal argument>"]],
            "cleanup": [],
        },
        "guidance": "Start with draft and edit existing formatter_inputs fields in place, without duplicating them in another input map. After assemble --draft-output, continue with that updated draft. Set effective_locale and its accurate locale_source together (explicit or inferred). Preserve generated_inputs and preflight references. kickoff_inputs.md documents preparation; kickoff.md owns delivery and authority rules. Examples are placeholders, never approved decisions.",
        "render_output": "Default JSON: render.output; --output PATH writes the complete text and returns status/output_file only.",
    }


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def normalize_request_identity(request: dict[str, Any]) -> dict[str, Any]:
    """An explicit numeric issue ID can supply the conventional local name."""
    result = dict(request)
    if "issue_id" in result:
        issue_id = result["issue_id"]
        if isinstance(issue_id, bool) or not re.fullmatch(r"[1-9][0-9]*", str(issue_id)):
            raise ValueError("issue_id must be a positive integer")
        result["issue_id"] = str(issue_id)
        result.setdefault("issue_name", "issue" + str(issue_id))
    return result


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
    # Keep the editable values even on failure. Invalid fields still block argv
    # construction/rendering; they must never erase unrelated prefilled values.
    diagnostics: list[str] = [
        *(f"activation_or_authority_field_rejected:{key}" for key in found_prohibited),
        *(f"unknown_formatter_field:{key}" for key in unknown),
    ]
    missing = sorted(key for key in _REQUIRED_FIELDS if key not in values or values[key] is None)
    if "worktree" not in values and values.get("current_checkout") is not True:
        missing.append("checkout")
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

    for key, flag in _JSON_FLAGS.items():
        args.extend([flag, json.dumps(data[key], ensure_ascii=False, separators=(",", ":"))])

    for key, flag in _REPEATED_FLAGS.items():
        for value in data.get(key, []):
            args.extend([flag, value])

    for key, flag in _LIST_FLAGS.items():
        if key in data:
            args.append(flag)
            args.extend(data[key])

    for key, flag in _SCALAR_FLAGS.items():
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


def _age_seconds(value: Any, *, now: datetime) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return round((now - parsed.astimezone(timezone.utc)).total_seconds(), 3)


def _compact_candidate(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {"eligible": False, "diagnostics": ["candidate_record_invalid"]}
    step_fields = (
        "type", "role", "skill", "on", "human_tasks", "input_artifacts", "output_artifact"
    )
    steps: dict[str, Any] = {}
    omitted_step_fields: dict[str, list[str]] = {}
    raw_steps = candidate.get("steps", {})
    if isinstance(raw_steps, dict):
        for step_id, step in raw_steps.items():
            if not isinstance(step, dict):
                steps[step_id] = {"diagnostics": ["step_record_invalid"]}
                omitted_step_fields[step_id] = []
                continue
            steps[step_id] = {key: step[key] for key in step_fields if key in step}
            omitted_step_fields[step_id] = sorted(set(step) - set(step_fields))
    candidate_fields = (
        "id", "contract_mode", "eligible", "source", "fingerprint", "applicability", "behavior", "roles",
        "profiles", "skills", "native_subagent_steps", "confirmation_gates", "mandatory_confirmation_gates",
        "capability_requirements", "capability_setup", "diagnostics",
    )
    summary = {key: candidate[key] for key in candidate_fields if key in candidate}
    summary["steps"] = steps
    summary["omitted_detail_fields"] = sorted(set(candidate) - set(candidate_fields) - {"steps"})
    summary["omitted_step_detail_fields"] = omitted_step_fields
    return summary


def compact_discovery_summary(
    discovery: dict[str, Any],
    *,
    selected_only: bool = False,
    inspect_references: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project reusable discovery facts without dropping candidates or diagnostics silently."""
    instant = now or datetime.now(timezone.utc)
    raw_catalog = discovery.get("catalog", {})
    raw_catalog = raw_catalog if isinstance(raw_catalog, dict) else {}
    raw_candidates = raw_catalog.get("candidates", [])
    raw_candidates = raw_candidates if isinstance(raw_candidates, list) else []
    candidates = [_compact_candidate(candidate) for candidate in raw_candidates]
    selected = discovery.get("selected_candidate")
    selected_id = selected.get("id") if isinstance(selected, dict) else None
    selected_summary = next(
        (item for item in candidates if item.get("id") == selected_id), None
    )
    if selected_summary is None and isinstance(selected, dict):
        selected_summary = _compact_candidate(selected)

    invalid_diagnostics = [
        {"id": item.get("id"), "diagnostics": item.get("diagnostics", [])}
        for item in candidates
        if item.get("eligible") is not True
    ]
    eligible_count = sum(item.get("eligible") is True for item in candidates)
    ineligible_count = len(candidates) - eligible_count
    listed_candidates = [] if selected_only else candidates
    omitted_count = len(candidates) - len(listed_candidates)

    refs = inspect_references or {}
    delivery = discovery.get("delivery", {})
    delivery = delivery if isinstance(delivery, dict) else {}
    manifest = delivery.get("manifest", {})
    manifest = manifest if isinstance(manifest, dict) else {}
    observations = []
    for observation in delivery.get("current_observations", []):
        if isinstance(observation, dict):
            observations.append(
                {**observation, "age_seconds": _age_seconds(observation.get("observed_at"), now=instant)}
            )
        else:
            observations.append(observation)

    models = []
    for model in discovery.get("models", []):
        if not isinstance(model, dict):
            models.append({"status": "miss", "diagnostics": ["model_report_invalid"]})
            continue
        models.append({
            **model,
            "inspect_reference": refs.get("models"),
        })

    result = {
        "schema_version": 1,
        "stage": "discovery_summary",
        "status": discovery.get("status", "partial"),
        "selected_playbook": selected_id,
        "preferences": discovery.get("preferences", {}),
        "catalog": {
            "candidate_count": len(raw_candidates),
            "eligible_candidate_count": eligible_count,
            "ineligible_candidate_count": ineligible_count,
            "invalid_candidate_count": ineligible_count,
            "listed_candidate_count": len(listed_candidates),
            "unlisted_candidate_count": omitted_count,
            "selected_candidate_count": 1 if selected_summary is not None else 0,
            "candidates": listed_candidates,
            "candidate_overview": [
                {**{key: item.get(key) for key in (
                    "id", "eligible", "applicability", "roles", "source", "fingerprint", "diagnostics"
                )}, "steps": list(item["steps"])}
                for item in candidates
            ] if selected_only else [],
            "ineligible_candidate_diagnostics": invalid_diagnostics,
            "diagnostics": raw_catalog.get("diagnostics", []),
            "reuse": raw_catalog.get("reuse", {}),
            "inspect_reference": refs.get("catalog"),
        },
        "delivery": {
            "status": delivery.get("status", "miss"),
            "diagnostics": delivery.get("diagnostics", []),
            "discovery_gap": delivery.get("discovery_gap"),
            "stable_conventions": delivery.get("stable_conventions", []),
            "delivery_template": delivery.get("delivery_template"),
            "sources": delivery.get("sources", []),
            "current_observations": observations,
            "manifest": {
                "repository": manifest.get("repository"),
                "inventory_count": len(manifest.get("inventory", []))
                if isinstance(manifest.get("inventory", []), list) else None,
                "source_count": len(manifest.get("sources", []))
                if isinstance(manifest.get("sources", []), list) else None,
                "sources": manifest.get("sources", []),
                "watched": manifest.get("watched", []),
            },
            "observation_age_seconds": None if not observations else observations[0].get("age_seconds"),
            "inspect_reference": refs.get("delivery"),
        },
        "models": models,
        "diagnostics": discovery.get("diagnostics", []),
        "inspect_references": refs,
    }
    if selected_only:
        result["stage"] = "assembly_summary"
        result["selected_graph"] = selected_summary
    return result


def discover_kickoff(
    request: dict[str, Any], *, config_dir: Path | None = None, cache_dir: Path | None = None,
    include_provenance: bool = False,
) -> dict[str, Any]:
    """Gather reusable inputs while leaving issue assessment and selection to Manager."""
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        return {"stage": "discovery", "status": "invalid", "diagnostics": ["unsupported_request_schema"]}
    request = normalize_request_identity(request)
    project_root = Path(request.get("project_root", Path.cwd())).expanduser().resolve()
    issue_name = request.get("issue_name")
    if not isinstance(issue_name, str) or not issue_name.strip():
        return {"stage": "discovery", "status": "invalid", "diagnostics": ["issue_name_required"]}
    from cafe.catalogs.resolver import CatalogResolver

    resolver = CatalogResolver(project_root=project_root)
    catalog_owner = _load_local_module("kickoff_catalog")
    catalog_args = dict(
        project_root=project_root,
        global_root=resolver.global_root,
        builtin_root=resolver.builtin_root,
        cache_file=(cache_dir or _default_cache_root()) / "catalog-v1.json",
    )
    from cafe.manager.api import confirmed_contract_snapshot

    confirmed = confirmed_contract_snapshot(project_root / ".cafe/issues" / issue_name)
    if confirmed and confirmed.get("contract_mode") == "compact":
        request = {**request, "playbook_id": confirmed["execution"]["playbook_id"]}
    early_catalog = catalog_owner.discover_index(
        **catalog_args, lightweight=True, selected_id=request.get("playbook_id"))
    early_selected = next((item for item in early_catalog["candidates"]
                           if item["id"] == request.get("playbook_id")), None)
    mode = (confirmed.get("contract_mode", "full") if confirmed else
            early_selected.get("contract_mode", "full") if early_selected else "full")
    if mode == "compact":
        return _load_local_module("compact_kickoff").discover(
            request, catalog_args=catalog_args, early_catalog=early_catalog,
            confirmed=confirmed, config_dir=config_dir, cache_dir=cache_dir)
    catalog = catalog_owner.discover_index(**catalog_args) if early_selected else early_catalog
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
    models = []
    for assessment in model_records:
        if not isinstance(assessment, dict):
            continue
        report = model_module.assess_model_evidence(
            assessment, now=now, current_sources=request.get("current_model_sources"),
            contradictions=request.get("model_contradictions"),
        )
        if include_provenance:
            raw_sources = assessment.get("sources", [])
            raw_sources = raw_sources if isinstance(raw_sources, list) else []
            report["provenance"] = {
                "assessed_at": assessment.get("assessed_at"),
                "age_seconds": _age_seconds(assessment.get("assessed_at"), now=now),
                "sources": [
                    {
                        **source,
                        "age_seconds": _age_seconds(source.get("retrieved_at"), now=now),
                    }
                    if isinstance(source, dict) else {"source": source, "age_seconds": None}
                    for source in raw_sources
                ],
            }
        models.append(report)
    selected = request.get("playbook_id")
    selected_candidate = next(
        (item for item in catalog.get("candidates", []) if item.get("id") == selected), None
    ) if isinstance(selected, str) else None
    return {
        "contract_mode": mode,
        "stage": "discovery", "status": "partial" if catalog.get("diagnostics") else "ready",
        "preferences": preferences, "catalog": catalog, "selected_candidate": selected_candidate,
        "delivery": delivery, "models": models, "diagnostics": list(catalog.get("diagnostics", [])),
    }

def preparation_template(request: dict[str, Any], draft: dict[str, Any],
                         generated: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return an editable request; placeholders never become resolved assembly inputs."""
    template = request_schema()["input_template"]
    template.update(draft)
    result = json.loads(json.dumps(request))
    result["formatter_inputs"] = template
    if generated:
        result["generated_inputs"] = {**result.get("generated_inputs", {}), **generated}
    # Explicit values already have an editable home. Do not duplicate a typo
    # (or a valid override) and force the caller to correct two copies.
    for field in result.get("current_explicit_inputs", {}):
        result["formatter_inputs"].pop(field, None)
    # Retain report references, not another hand-copied report or new metadata.
    for file_key, field in (("update", "update_preflight"), ("catalog", "catalog_preflight")):
        if file_key in request.get("preflight_files", {}) and field not in request.get("formatter_inputs", {}) and field not in request.get("current_explicit_inputs", {}):
            result["formatter_inputs"].pop(field, None)
    return result


def _preflight_file_report(reference: str, kind: str, metadata: Any = None) -> dict[str, Any]:
    """Map complete check output to formatter fields without executing checks."""
    report = json.loads(Path(reference).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("preflight report must be an object")
    if metadata is None:
        return report  # Existing formatter-ready file compatibility.
    required = {"checked_at", "decision", "post_change_evidence"}
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise ValueError("raw preflight metadata requires only checked_at, decision, post_change_evidence")
    if metadata["decision"] is None:
        raise ValueError("raw preflight metadata requires the current Manager decision")
    if not isinstance(metadata["checked_at"], str) or not metadata["checked_at"].strip():
        raise ValueError("raw preflight metadata requires the actual check timestamp")
    if kind == "catalog" and "catalog_check" in report:
        check = report["catalog_check"]
        if not isinstance(check, dict):
            raise ValueError("catalog_check must be an object")
        if any(key in report and report[key] != value for key, value in check.items()):
            raise ValueError("conflicting catalog report fields")
        report = {**report, **check}
    if kind == "update" and "token" in report:
        if "comparison_token" in report and report["comparison_token"] != report["token"]:
            raise ValueError("conflicting update comparison tokens")
        report["comparison_token"] = report["token"]
    if any(key in report and report[key] != value for key, value in metadata.items()):
        raise ValueError("metadata conflicts with source report")
    return {**report, **metadata}


def _input_fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _delivery_dependency(discovery: dict[str, Any] | None, values: dict[str, Any], request: dict[str, Any]) -> str | None:
    delivery = (discovery or {}).get("delivery", {})
    if delivery.get("status") != "hit":
        return None
    return _input_fingerprint({
        "sources": delivery.get("sources"), "template": delivery.get("delivery_template"),
        "observations": delivery.get("current_observations"),
        "repository": delivery.get("manifest", {}).get("repository"),
        "context": {key: values.get(key) for key in ("project_root", "issue_name", "worktree", "current_checkout")},
        "issue_id": request.get("issue_id"),
    })


def _prefill_checkout(values: dict[str, Any], *, project_root: Path, sources: dict[str, str],
                      blocked: frozenset[str] | set[str] = frozenset()) -> None:
    from cafe.utils.git_utils import get_repo_root

    if not blocked.intersection({"worktree", "current_checkout"}) and "worktree" not in values and "current_checkout" not in values:
        name = values.get("issue_name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError("a safe issue_name is required for the default worktree")
        try:
            root = get_repo_root(project_root)
        except ValueError:
            values["current_checkout"] = True
            sources["current_checkout"] = "first task in a non-Git folder"
        else:
            values["worktree"] = str(root / ".cafe" / "worktrees" / name)
            sources["worktree"] = "issue worktree convention"


def _prefill_saved_inputs(values: dict[str, Any], *, store: Any, request: dict[str, Any],
                         report: dict[str, Any], missing: list[dict[str, str]], sources: dict[str, str]) -> set[str]:
    """Apply scoped proposal conventions using the existing formatter owners."""
    root = Path(request.get("project_root", Path.cwd())).resolve()
    owner = _load_local_module("format_kickoff_contract")
    model = owner.PlaybookLoader(project_root=root).load_model(values["playbook_id"]).model if values.get("playbook_id") else None
    phases = {name: step for name, step in model.steps.items() if step.assignee_type in {"agent", "hybrid"}} if model else {}

    blocked: set[str] = set()

    def unresolved(key):
        return key not in values or (values[key] is None and key not in request.get("current_explicit_inputs", {}))

    def apply(key, fields, needed, callback):
        if not needed:
            return
        resolved = store.effective(key)
        report[key] = {"value": resolved.value, "scope": resolved.scope, "origin": resolved.origin}
        if resolved.value is None:
            return
        try:
            updates = callback(resolved.value)
            values.update(updates)
            sources.update({field: "explicit reusable preference: " + key for field in updates})
        except (ValueError, TypeError, KeyError) as exc:
            report[key]["diagnostic"] = str(exc)
            missing.append({"owner": "manager_decision", "requirement": f"resolve preference {key}: {exc}"})
            # Never persist a lower-priority fallback as if it resolved this
            # choice. The next draft reader must encounter the same gap.
            blocked.update(fields)

    delivery = _load_local_module("kickoff_delivery")
    def context():
        return {"issue_name": values["issue_name"], "issue_id": request.get("issue_id", ""),
                "project_root": str(root), "worktree": values.get("worktree") or (str(root) if values.get("current_checkout") is True else "")}

    def checkout(value):
        if value == {"current_checkout": True}:
            return value
        rendered = delivery.render_delivery_template({"deliver": [["path", value]], "deliver_description": ["Checkout"]}, context())
        return {"worktree": rendered["deliver"][0][1]}
    apply("worktree.convention", {"worktree", "current_checkout"}, "worktree" not in values and "current_checkout" not in values, checkout)

    def chains(value):
        if model is None:
            raise ValueError("select a graph before applying step/role chains")
        if not isinstance(value, dict) or not value or set(value) - {"steps", "roles"}:
            raise ValueError("expected steps/roles mappings of ordered CLI:MODEL chains")
        steps, roles = value.get("steps", {}), value.get("roles", {})
        if not isinstance(steps, dict) or not isinstance(roles, dict) or set(steps) - set(phases) or set(roles) - {s.role for s in phases.values()}:
            raise ValueError("saved selectors do not match the selected graph")
        current = values.get("phase_chain", [])
        if not _is_string_list(current):
            raise ValueError("current phase_chain must remain explicitly correctable")
        overrides = owner._parse_phase_chains(current, step_names=set(model.steps))
        result = list(current)
        for name, step in phases.items():
            candidate = steps.get(name, roles.get(step.role))
            if name in overrides or candidate is None:
                continue
            if not _is_string_list(candidate) or not candidate:
                raise ValueError("saved chain must be a nonempty string array")
            entry = name + "=" + ",".join(candidate)
            owner._parse_phase_chains([entry], step_names=set(model.steps))
            result.append(entry)
        return {"phase_chain": result}
    explicit_chain = values.get("phase_chain")
    covered = {item.split("=", 1)[0] for item in explicit_chain} if _is_string_list(explicit_chain) else set()
    apply("phase.chains", {"phase_chain"}, not phases or not set(phases) <= covered, chains)

    def assignments(value):
        if model is None or not isinstance(value, dict) or set(value) != {"user_required", "manager_confirmable"}:
            raise ValueError("expected user_required and manager_confirmable arrays for assignable gates only")
        if not all(_is_string_list(v) for v in value.values()):
            raise ValueError("confirmation assignments must be string arrays")
        user, manager = owner._resolve_partition(candidates=owner.confirmation_gate_steps(model),
            user_values=value["user_required"], manager_values=value["manager_confirmable"])
        return {"user_required": user, "manager_confirmable": manager}
    apply("confirmation.assignments", {"user_required", "manager_confirmable"}, "user_required" not in values and "manager_confirmable" not in values, assignments)

    def reviews(value):
        if model is None or not isinstance(value, dict):
            raise ValueError("select a graph and supply phase-to-decision mappings")
        if set(value) - set(phases):
            raise ValueError("saved review selectors do not match the selected graph")
        rows = owner._proactive_review_decisions([f"{k}={value[k]}" for k in phases if k in value], agent_phases=list(phases),
            eligible_phases=set(owner.confirmation_gate_steps(model)) | set(owner.mandatory_confirmation_gate_steps(model)))
        return {"proactive_review_decision": [f"{r['phase']}={r['decision']}" for r in rows]}
    apply("review.decisions", {"proactive_review_decision"}, "proactive_review_decision" not in values, reviews)

    _prefill_checkout(values, project_root=root, sources=sources, blocked=blocked)

    def convention(stage, value):
        if not isinstance(value, dict) or set(value) != {stage, stage + "_description"}:
            raise ValueError("expected only action template and matching descriptions")
        rendered = delivery.render_delivery_template({"deliver": value[stage], "deliver_description": value[stage + "_description"]}, context())
        updates = {stage: rendered["deliver"], stage + "_description": rendered["deliver_description"]}
        return {key: value for key, value in updates.items() if unresolved(key)}
    for stage in ("deliver", "cleanup"):
        apply(stage.replace("deliver", "delivery") + ".convention", {stage, stage + "_description"}, unresolved(stage),
              lambda value, stage=stage: convention(stage, value))
    return blocked


def _prefill_configured_inputs(values: dict[str, Any], *, project_root: Path, sources: dict[str, str],
                              request: dict[str, Any], discovery: dict[str, Any] | None,
                              blocked: frozenset[str] | set[str] = frozenset()) -> None:
    """Materialize defaults and reusable routes in a proposal without execution."""
    from cafe.core.strategic_context import load_strategic_context

    owner = _load_local_module("format_kickoff_contract")
    parser = owner._parser()

    def fill(key: str, value: Any, source: str) -> None:
        if key not in blocked and (key not in values or action_slot(key)):
            values[key] = value
            sources[key] = source

    def action_slot(key: str) -> bool:
        return (key in {"deliver", "cleanup"} and values.get(key) is None
                and key not in request.get("current_explicit_inputs", {}))

    from cafe.utils.git_utils import get_github_repo_name

    _prefill_checkout(values, project_root=project_root, sources=sources, blocked=blocked)
    if values.get("manager_mode") == "event-driven" and "event_manager" not in values:
        cli = request.get("manager_cli") or ("codex" if os.environ.get("CODEX_THREAD_ID") else None)
        if cli:
            fill("event_manager", [cli], "current Manager CLI")
    if "cleanup" not in values or action_slot("cleanup"):
        commands, descriptions = [], []
        issue_id = request.get("issue_id")
        if issue_id:
            try:
                repository = get_github_repo_name(project_root)
            except (OSError, ValueError):
                repository = None
            if repository:
                commands.append(["gh", "issue", "close", issue_id, "--repo", repository])
                descriptions.append(f"Close GitHub issue {repository}#{issue_id}.")
        commands.append(["cafe", "close"])
        descriptions.append("Archive this CAFE issue and remove its managed worktree and branch.")
        fill("cleanup", commands, "default issue cleanup proposal")
        fill("cleanup_description", descriptions, "default issue cleanup proposal")
    delivery = (discovery or {}).get("delivery", {})
    if "deliver" not in blocked and ("deliver" not in values or action_slot("deliver")) and delivery.get("status") == "hit" and delivery.get("delivery_template") is not None:
        rendered = _load_local_module("kickoff_delivery").render_delivery_template(
            delivery["delivery_template"], {"issue_name": values["issue_name"],
                "issue_id": request.get("issue_id", ""), "project_root": str(project_root),
                "worktree": values.get("worktree") or (str(project_root) if values.get("current_checkout") is True else "")},
        )
        for key, value in rendered.items():
            fill(key, value, "validated repository delivery template")

    for key in ("need_permission", "need_clarification", "alignment_checkpoint"):
        fill(key, parser.get_default(key), "formatter default")
    if "repository_content_locale" not in values:
        context = load_strategic_context(project_root)
        fill("repository_content_locale", context.content_locale, "repository language policy")
    for stage in ("deliver", "cleanup"):
        if values.get(stage) == []:
            fill(stage + "_description", [], "explicit empty action plan")

    selected = values.get("playbook_id")
    if not selected:
        return
    model = owner.PlaybookLoader(project_root=project_root).load_model(selected).model
    if "effective_locale" not in values and "locale_source" not in values:
        snapshot = owner.contract_locale_snapshot(
            project_root / ".cafe" / "issues" / values["issue_name"],
            playbook_id=selected, playbook_locale=model.playbook.conversation_locale,
        )
        if snapshot["value"] and snapshot["value"] != "auto":
            fill("effective_locale", snapshot["value"], snapshot["source"])
            fill("locale_source", snapshot["source"], "conversation locale owner")

    gates = owner.confirmation_gate_steps(model)
    if "user_required" not in values and "manager_confirmable" not in values:
        user, manager = owner._resolve_partition(candidates=gates, user_values=None, manager_values=None)
        fill("user_required", user, "formatter confirmation default")
        fill("manager_confirmable", manager, "formatter confirmation default")
    phases = {name: step for name, step in model.steps.items() if step.assignee_type in {"agent", "hybrid"}}
    if "proactive_review_decision" not in values:
        reviews = owner._proactive_review_decisions(
            [], agent_phases=list(phases),
            eligible_phases=set(gates) | set(owner.mandatory_confirmation_gate_steps(model)),
        )
        fill("proactive_review_decision", [f"{r['phase']}={r['decision']}" for r in reviews],
             "formatter review default")
    # The formatter already resolves omitted chains from phase configuration.
    # Put those exact values in the draft so the caller reviews instead of copies.
    supplied_chains = values.get("phase_chain", [])
    if "phase_chain" not in blocked and _is_string_list(supplied_chains):
        overrides = owner._parse_phase_chains(supplied_chains, step_names=set(model.steps))
        config = owner._project_path(Path(values.get("phase_config", parser.get_default("phase_config"))), project_root)
        chains = list(supplied_chains)
        for name, step in phases.items():
            if name in overrides:
                continue
            chain, _ = owner._resolve_configured_chain(step_name=name, role=step.role, phase_config=config)
            chains.append(name + "=" + ",".join(f"{cli}:{model_name}" for cli, model_name in chain))
        if chains != supplied_chains:
            values["phase_chain"] = chains
            sources["phase_chain"] = "configured phase chains; explicit overrides retained; suitability still requires assessment"


def assemble_kickoff(
    request: dict[str, Any], *, preference_store: Any = None,
    discovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        return {"status": "invalid", "selected_playbook": None, "diagnostics": ["unsupported_request_schema"], "missing_decisions": [], "formatter_inputs": None}
    request = normalize_request_identity(request)
    if discovery and discovery.get("contract_mode") == "compact":
        return _load_local_module("compact_kickoff").assemble(request, discovery=discovery)
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
    # Explicit inputs precede preferences, configured defaults, and cached routes.
    # The resulting proposal still requires confirmation before execution.
    supplied = request.get("formatter_inputs", {})
    explicit = request.get("current_explicit_inputs", {})
    if not isinstance(supplied, dict) or not isinstance(explicit, dict):
        return {"status": "invalid", "selected_playbook": selected,
                "diagnostics": ["input_fields_must_be_objects"],
                "missing_decisions": missing, "formatter_inputs": None}
    raw_inputs = {key: request[key] for key in ("project_root", "issue_name", "playbook_id") if key in request}
    raw_inputs.update(explicit)
    conflicts = sorted(key for key in supplied if key in explicit and supplied[key] != explicit[key])
    if conflicts:
        return {"status": "invalid", "selected_playbook": selected,
                "diagnostics": [f"conflicting_explicit_input:{key}" for key in conflicts],
                "missing_decisions": missing, "formatter_inputs": None}
    raw_inputs.update(supplied)
    for key in ("project_root", "issue_name"):
        if key in request and raw_inputs.get(key) != request[key]:
            return {"status": "invalid", "selected_playbook": selected,
                    "diagnostics": [f"request_identity_conflict:{key}"],
                    "missing_decisions": missing, "formatter_inputs": None}
    preference_report: dict[str, Any] = {}
    prefilled: dict[str, str] = {}
    root = Path(request.get("project_root", Path.cwd())).resolve()
    existing_locale = (root / ".cafe" / "issues" / str(request.get("issue_name")) / "blackboard.json").exists()
    if existing_locale:
        snapshot = _load_local_module("format_kickoff_contract").contract_locale_snapshot(
            root / ".cafe" / "issues" / request["issue_name"], playbook_id=selected)
        raw_inputs.update(effective_locale=snapshot["value"], locale_source=snapshot["source"])
    if preference_store is not None and isinstance(raw_inputs, dict):
        preference_mapping = {
            "manager.mode": "manager_mode",
            "conversation.locale": "effective_locale",
        }
        explicit = request.get("current_explicit_inputs", {})
        explicit = explicit if isinstance(explicit, dict) else {}
        for preference_key, input_key in preference_mapping.items():
            inferred_locale = input_key == "effective_locale" and raw_inputs.get("locale_source") == "inferred"
            if input_key in raw_inputs and not inferred_locale or input_key == "effective_locale" and existing_locale:
                continue
            resolved = preference_store.effective(preference_key, explicit=None if inferred_locale else explicit.get(input_key))
            preference_report[preference_key] = {
                "value": resolved.value,
                "scope": resolved.scope,
                "origin": resolved.origin,
            }
            if resolved.value is not None:
                raw_inputs[input_key] = resolved.value
                if input_key == "effective_locale":
                    raw_inputs["locale_source"] = resolved.origin
    if "manager_mode" not in raw_inputs:
        raw_inputs["manager_mode"] = "event-driven"
        prefilled["manager_mode"] = "default Manager mode"
    if preference_store is not None:
        # Resolve the applicable dependency after the preference/default mode.
        mode = raw_inputs.get("manager_mode")
        dependent = ({"attached": ("manager.poll_interval_seconds", "poll_interval_seconds"),
                      "event-driven": ("manager.event_manager", "event_manager")}.get(mode)
                     if isinstance(mode, str) else None)
        if dependent and dependent[1] not in raw_inputs:
            resolved = preference_store.effective(dependent[0])
            preference_report[dependent[0]] = {"value": resolved.value, "scope": resolved.scope, "origin": resolved.origin}
            if resolved.value is not None:
                raw_inputs[dependent[1]] = resolved.value
    try:
        blocked = set()
        if preference_store is not None:
            blocked = _prefill_saved_inputs(raw_inputs, store=preference_store, request=request,
                                           report=preference_report, missing=missing, sources=prefilled)
        _prefill_configured_inputs(raw_inputs, project_root=Path(request.get("project_root", Path.cwd())).resolve(),
                                  sources=prefilled, request=request, discovery=discovery, blocked=blocked)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        missing.append({"owner": "manager_research", "requirement": f"resolve configured inputs: {exc}"})
    generated = {}
    bindings = request.get("generated_inputs", {})
    if not isinstance(bindings, dict):
        missing.append({"owner": "manager_research", "requirement": "invalid generated input provenance"})
        bindings = {}
    dependency = _delivery_dependency(discovery, raw_inputs, request)
    for field in ("deliver", "deliver_description"):
        binding = bindings.get(field)
        if binding is not None and field not in explicit:
            if not isinstance(binding, dict):
                missing.append({"owner": "manager_research", "requirement": f"invalid provenance for {field}"})
            elif binding.get("value_fingerprint") == _input_fingerprint(raw_inputs.get(field)):
                if dependency is None or binding.get("dependency") != dependency:
                    missing.append({"owner": "manager_decision", "requirement": f"reassess source-backed {field}; evidence or target changed"})
        if prefilled.get(field) == "validated repository delivery template":
            generated[field] = {"origin": "delivery_evidence", "dependency": dependency,
                                "value_fingerprint": _input_fingerprint(raw_inputs[field])}
    preflight_files = request.get("preflight_files", {})
    if isinstance(preflight_files, dict) and isinstance(raw_inputs, dict):
        for file_key, input_key in (("update", "update_preflight"), ("catalog", "catalog_preflight")):
            reference = preflight_files.get(file_key)
            if input_key not in raw_inputs and isinstance(reference, str):
                try:
                    metadata = request.get("preflight_metadata", {})
                    if not isinstance(metadata, dict):
                        raise ValueError("preflight_metadata must be an object")
                    raw_inputs[input_key] = _preflight_file_report(reference, file_key, metadata.get(file_key))
                except (OSError, ValueError) as exc:
                    missing.append({"owner": "manager_research", "requirement": f"read {file_key} preflight report: {exc}"})
    normalized = normalize_formatter_inputs(raw_inputs) if isinstance(raw_inputs, dict) else None
    if normalized is not None and normalized["status"] == "ready":
        from argparse import Namespace

        descriptions = {f"{stage}_description": raw_inputs.get(f"{stage}_description", [])
                        for stage in ("deliver", "cleanup")}
        try:
            # Assemble through the existing owner before advertising render readiness.
            # Low-level argv encoding remains compatible with partial formatter data.
            _load_local_module("format_kickoff_contract")._closeout_descriptions(
                Namespace(**descriptions), {stage: raw_inputs[stage] for stage in ("deliver", "cleanup")}
            )
        except ValueError as exc:
            normalized["status"] = "invalid"
            normalized["diagnostics"].append(str(exc))
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
        "prefilled": prefilled,
        "generated_inputs": generated,
        "diagnostics": [] if normalized is None else normalized.get("diagnostics", []),
        "missing_decisions": missing,
        "formatter_inputs": normalized["values"] if complete else None,
        "formatter_draft": normalized.get("values") if normalized is not None else None,
    }


def render_kickoff(values: dict[str, Any], *, preference_store=None,
                   preference_templates=None, issue_id="") -> dict[str, Any]:
    if isinstance(values, dict) and values.get("contract_mode") == "compact":
        return _load_local_module("compact_kickoff").render(values)
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
        model = formatter.PlaybookLoader(project_root=args.project_root).load_model(args.playbook_id).model
        offer = formatter.build_offer(args, proposal, model, store=preference_store,
                                      templates=preference_templates, issue_id=issue_id)
        output = formatter.render(args, confirmed_proposal=proposal, preference_offer=offer)
    except (SystemExit, OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "invalid", "diagnostics": [type(exc).__name__], "validation_error": str(exc)}
    return {"status": "rendered", "proposal": proposal, "output": output, "preference_offer": offer}

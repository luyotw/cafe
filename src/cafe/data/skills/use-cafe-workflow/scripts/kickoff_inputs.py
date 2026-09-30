"""Staged preparation inputs and the existing formatter adapter boundary."""

from __future__ import annotations

import hashlib
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
        "input_template": {"delivery_contract": contract_template, "deliver": None, "cleanup": None},
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
            "current_explicit_inputs": {"effective_locale": "zh-TW", "locale_source": "explicit", "repository_content_locale": "en-US"},
            "preflight_files": {"update": "/tmp/update.json", "catalog": "/tmp/catalog.json"},
            "formatter_inputs": {},
        },
        "decision_examples": {
            "phase_chain": ["develop=codex:<exact-model>"],
            "capability_choice": ["pr.auto_create=true"],
            "deliver": [["<executable>", "<literal argument>"]],
            "cleanup": [],
        },
        "guidance": "kickoff_inputs.md documents staged requests; kickoff.md owns the delivery contract and authority rules. Examples are placeholders, never approved decisions.",
        "render_output": "Default JSON: render.output; --output PATH writes the complete text and returns status/output_file only.",
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
        "id", "eligible", "source", "fingerprint", "applicability", "behavior", "roles",
        "profiles", "skills", "confirmation_gates", "mandatory_confirmation_gates",
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
        "stage": "discovery", "status": "partial" if catalog.get("diagnostics") else "ready",
        "preferences": preferences, "catalog": catalog, "selected_candidate": selected_candidate,
        "delivery": delivery, "models": models, "diagnostics": list(catalog.get("diagnostics", [])),
    }

def kickoff_guidance(
    request: dict[str, Any] | None = None, summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read current owner sections once; this projection owns no policy or saved state."""
    selections = {
        "kickoff.md": ["## Conversation locale checklist", "## Repository content locale checklist",
                       "## Repository-informed deliver and cleanup plan", "## Kickoff contract: first blocking gate",
                       "### Complete runtime and catalog preflight", "### Derive confirmation gates",
                       "### Delivery facts to confirm", "### Checkout and existing contracts",
                       "### Render the proposal"],
        "strategic_context.md": None,
        "playbook_selection.md": None,
        "model_selection.md": ["# Issue Assessment And Model Selection", "## Assess before proposing models",
                               "## Resolve phase execution requirements", "## Keep model ownership outside phase agents",
                               "## Classify the required capability band", "## Select exact chains",
                               "## Model and fallback preflight", "### Reuse successful preflight evidence"],
        "project_global_skill_sync.md": ["# Runtime And Catalog Preflight", "## Route the check results",
                                         "## Manager-managed runtime-update decision"],
        "workflow_progress.md": ["## Initial kickoff presentation"],
    }
    # A valid current selection ends candidate selection, not scope assessment,
    # independent QA judgment, model suitability or confirmation. No cached
    # repository-wide selection is accepted here. No-argument inspection is full.
    if request and summary and (summary.get("selected_graph") or {}).get("eligible"):
        if request.get("playbook_id") == summary["selected_graph"].get("id"):
            selections["playbook_selection.md"] = [
                "# Playbook Selection", "## Resolve authoritative selections first",
                "## Independent QA decision", "## Record and reconfirm"]
    result = []
    for filename, headings in selections.items():
        path = Path(__file__).resolve().parent.parent / "references" / filename
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        lines = text.splitlines(keepends=True)
        starts = []
        fenced = False
        for index, line in enumerate(lines):
            if line.startswith(("```", "~~~")):
                fenced = not fenced
            if not fenced and line.startswith("#") and " " in line:
                prefix = line.split(" ", 1)[0]
                if set(prefix) == {"#"}:
                    starts.append((index, line.strip()))
        found = set()
        for i, (start, heading) in enumerate(starts):
            if headings is not None and heading not in headings:
                continue
            found.add(heading)
            end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
            result.append({"file": filename, "heading": heading,
                           "sha256": hashlib.sha256(raw).hexdigest(), "text": "".join(lines[start:end]),
                           "path": str(path), "start_line": start + 1, "end_line": end})
        if headings is not None and set(headings) - found:
            raise ValueError(f"kickoff guidance owner headings changed: {filename}")
    return result


def preparation_template(request: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """Return an editable request; placeholders never become resolved assembly inputs."""
    template = request_schema()["input_template"]
    template.update(draft)
    result = json.loads(json.dumps(request))
    result["formatter_inputs"] = template
    # Retain report references, not another hand-copied report or new metadata.
    for file_key, field in (("update", "update_preflight"), ("catalog", "catalog_preflight")):
        if file_key in request.get("preflight_files", {}) and field not in request.get("formatter_inputs", {}) and field not in request.get("current_explicit_inputs", {}):
            result["formatter_inputs"].pop(field, None)
    return result


def decision_brief(
    request: dict[str, Any], missing: list[dict[str, str]],
    *, summary: dict[str, Any] | None = None, assembled: dict[str, Any] | None = None,
    indexed: bool = False,
) -> dict[str, Any]:
    """Join current gaps to existing owner sections; never decide applicability or authority."""
    summary, assembled = summary or {}, assembled or {}
    graph = summary.get("selected_graph") or {}
    delivery = summary.get("delivery") or {}
    models = summary.get("models", [])
    # These questions route judgments to their existing owners. They are not a
    # completeness/authorization gate, and remain present for a fully filled draft.
    definitions = [
        ("scope", "Does this issue fit the current mandate, strategy and selected graph?",
         ["request_text", "selected_graph.applicability", "catalog.candidate_overview"],
         ["strategic_context.md", "playbook_selection.md"]),
        ("models", "Which exact chains meet each selected phase's workload, reasoning and risks?",
         ["selected_graph.profiles", "models[].assessment", "models[].provenance"], ["model_selection.md"]),
        ("actions", "What exact delivery/cleanup targets and effects are proposed, and what is currently authorized?",
         ["delivery.stable_conventions", "delivery.current_observations", "delivery.sources", "selected_graph.capability_setup"],
         ["## Repository-informed deliver and cleanup plan", "### Delivery facts to confirm", "### Checkout and existing contracts"]),
        ("confirmation", "Who owns each assignable decision and mandatory stop, and what still needs full confirmation?",
         ["selected_graph.confirmation_gates", "selected_graph.mandatory_confirmation_gates", "selected_graph.steps"],
         ["## Kickoff contract: first blocking gate", "### Derive confirmation gates"]),
        ("locale", "Are conversation and repository content locales resolved with their actual provenance?",
         ["preferences", "current_explicit_inputs"],
         ["## Conversation locale checklist", "## Repository content locale checklist"]),
        ("preflight", "What do the complete current check reports require before this proposal can proceed?",
         ["preflight_files", "preflight_metadata"],
         ["### Complete runtime and catalog preflight", "project_global_skill_sync.md"]),
        ("presentation", "How will the complete rendered proposal, literal actions and confirmation be presented faithfully?",
         ["render_command", "draft_output"], ["### Render the proposal", "workflow_progress.md"]),
    ]
    questions = [{"id": key, "question": question, "available": refs,
                  "requires_current_judgment": True, "evidence_gaps": [], "policy_sections": []}
                 for key, question, refs, _ in definitions]
    by_id = {item["id"]: item for item in questions}
    reading = {}
    for section in kickoff_guidance(request, summary):
        consumers = [key for key, _, _, selectors in definitions
                     if section["file"] in selectors or section["heading"] in selectors]
        if not consumers:
            raise ValueError(f"kickoff section has no decision owner: {section['heading']}")
        section_id = f"s{sum(len(v['sections']) for v in reading.values())}" if indexed else section["file"] + ":" + section["heading"]
        entry = reading.setdefault(section["path"], {
            "path": section["path"], "sha256": section["sha256"], "sections": []})
        entry["sections"].append({"id": section_id, "heading": section["heading"],
                                  "start_line": section["start_line"], "end_line": section["end_line"],
                                  "questions": consumers})
        for key in consumers:
            by_id[key]["policy_sections"].append(section_id)
    for entry in reading.values():
        # One argv per file reads the disjoint required sections, rather than
        # reading a whole concatenated guide and later recovering its slices.
        ranges = ";".join(f"{s['start_line']},{s['end_line']}p" for s in entry["sections"])
        if indexed:
            for section in entry["sections"]:
                section["line_range"] = f"{section['start_line']},{section['end_line']}p"
        else:
            entry["read_argv"] = ["sed", "-n", ranges, entry["path"]]

    if delivery.get("status") != "hit" or delivery.get("discovery_gap"):
        by_id["actions"]["evidence_gaps"].append({
            "question": "Which missing or changed repository sources establish the delivery route?",
            "diagnostics": delivery.get("diagnostics", []), "inspect_reference": delivery.get("inspect_reference")})
    if not models:
        by_id["models"]["evidence_gaps"].append({"question": "Where is dated primary-source evidence for the proposed exact models?"})
    for index, model in enumerate(models):
        if model.get("status") != "hit":
            by_id["models"]["evidence_gaps"].append({"question": "What fresh evidence resolves this model miss?",
                "reference": f"#/models/{index}", "diagnostics": model.get("diagnostics", []),
                "inspect_reference": model.get("inspect_reference")})
    # Literal workload coverage is evidence, not a ranking or suitability decision.
    # Unknown transfers remain questions for Manager; general is not a wildcard.
    coverage = {}
    for phase, profile in graph.get("profiles", {}).items():
        for workload in profile.get("workloads", []):
            references = [f"#/models/{i}/assessment" for i, model in enumerate(models)
                          if model.get("status") == "hit" and workload in model.get("assessment", {}).get("workloads", [])]
            coverage.setdefault(phase, {})[workload] = references
            if not references:
                by_id["models"]["evidence_gaps"].append({"question": "Which exact-model evidence covers this phase workload?",
                    "phase": phase, "workload": workload, "available": "#/models", "missing": "workload assessment"})
    fixed = {key: {"value": request[key], "source": "request"}
             for key in ("project_root", "issue_name", "playbook_id") if key in request}
    for source in ("current_explicit_inputs", "formatter_inputs"):
        supplied = request.get(source, {})
        for key, value in (supplied.items() if isinstance(supplied, dict) else []):
            if value is None:
                continue
            fixed[key] = ({"draft_reference": "/formatter_inputs/" + key, "source": source}
                          if indexed and source == "formatter_inputs" else {"value": value, "source": source})
    for key, value in assembled.get("preferences", {}).items():
        field = {"conversation.locale": "effective_locale", "manager.mode": "manager_mode"}.get(key)
        if field and value.get("value") is not None and field not in fixed:
            fixed[field] = {**value, "source": "preferences." + key}
    # The generic strategy owner resolves document metadata, not this projection.
    # Do not duplicate its authority table or treat delivery facts as current mandate.
    from cafe.core.strategic_context import load_strategic_context

    root = Path(request.get("project_root", Path.cwd()))
    repository_reads = {}
    try:
        documents = load_strategic_context(root, request.get("issue_name")).documents
    except (ValueError, TypeError, OSError) as exc:
        documents = {}
        by_id["scope"]["evidence_gaps"].append({"question": "How is the current strategic context repaired?",
                                               "diagnostics": [str(exc)]})
    for category, document in documents.items():
        if not document.exists or document.status not in {"exists", "draft"}:
            by_id["scope"]["evidence_gaps"].append({"question": "What confirmed grounds cover this strategic category?",
                                                   "category": category, "source": document.to_dict()})
        if document.path:
            path = str((root / document.path).resolve())
            item = repository_reads.setdefault(path, {"path": path, "sha256": document.sha256,
                "exists": document.exists, "categories": [], "questions": ["scope", "models"]})
            item["categories"].append({"category": category, "status": document.status})
    field_owners = {"delivery_contract": "scope", "checkout": "actions", "deliver": "actions", "cleanup": "actions",
                    "catalog_preflight": "preflight", "update_preflight": "preflight", "repository_content_locale": "locale"}
    missing_fields = []
    for item in missing:
        field = item["requirement"].removeprefix("formatter input: ")
        question = by_id[field_owners.get(field, "scope")]
        if indexed:
            missing_fields.append({**item, "question_id": question["id"], "missing": field,
                "field_reference": "#/decision_brief/field_shapes/formatter_field_schema/" + field})
            continue
        missing_fields.append({**item, "question": "What current value resolves " + item["requirement"] + "?",
                               "available": question["available"], "missing": field,
                               "policy_sections": question["policy_sections"]})
    schema = request_schema()
    return {
        "request_text": request.get("request_text"),
        "graph_reference": "#/selected_graph",
        "evidence_references": {"delivery": "#/delivery", "models": "#/models"},
        "fixed_inputs": fixed,
        "questions": questions,
        "reading_list": list(reading.values()),
        "read_command_template": ["sed", "-n", "<section.line_range>", "<source.path>"],
        "repository_reading_candidates": list(repository_reads.values()),
        "current_mandate_path": str((root / ".cafe/strategic_context.yaml").resolve()),
        "workload_evidence": coverage,
        "missing_fields": missing_fields,
        "field_shapes": {key: schema[key] for key in ("formatter_field_schema", "delivery_contract", "template_rules",
            "action_input_examples", "closeout_examples", "preflight_capture", "preflight_file_adapter")},
        "missing_decisions_reference": "#/missing_decisions",
        "missing_decision_count": len(missing),
        "evidence_use": "Apply the source-backed payloads already in this response. A hit supplies neither current authority nor model suitability. Read each policy range once for all its questions. Inspect raw evidence only for a named gap, contradiction or invalidation.",
    }


def index_summary_sources(summary: dict[str, Any]) -> None:
    """Intern repeated provenance only in the normal draft projection; retain all facts."""
    sources: dict[str, Any] = {}
    identities: dict[str, str] = {}

    def references(values: list[Any]) -> list[dict[str, str]]:
        result = []
        for value in values:
            identity = json.dumps(value, sort_keys=True, ensure_ascii=False)
            key = identities.get(identity)
            if key is None:
                key = f"source{len(sources)}"
                identities[identity] = key
                sources[key] = value
            result.append({"$ref": "#/source_index/" + key})
        return result

    delivery = summary.get("delivery", {})
    for holder in [delivery, delivery.get("manifest", {})]:
        if isinstance(holder, dict) and isinstance(holder.get("sources"), list):
            holder["sources"] = references(holder["sources"])
    for model in summary.get("models", []):
        for holder in [model.get("assessment", {}), model.get("provenance", {})]:
            if isinstance(holder, dict) and isinstance(holder.get("sources"), list):
                holder["sources"] = references(holder["sources"])
    for section in [summary.get("catalog", {}), delivery, *summary.get("models", [])]:
        reference = section.get("inspect_reference")
        for key, command in summary.get("inspect_references", {}).items():
            if reference == command:
                section["inspect_reference"] = {"$ref": "#/inspect_references/" + key}
                break
    summary["source_index"] = sources



def current_decision_view(summary: dict[str, Any], request: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """Present current decisions; defer only inspectable detail, never validation.

    The legacy summary remains the input contract. Source fingerprints and all
    validators run before this presentation projection; no policy is cached here.
    """
    view = json.loads(json.dumps(summary))
    view["presentation"] = "decisions"
    view["deferred_details"] = {
        "candidate_roles_and_dependencies": {"inspect_reference": {"$ref": "#/inspect_references/catalog"},
            "contents": "Complete candidates, role defaults, skills, artifact declarations and discovery dependencies."},
        "discovery_dependencies": {"inspect_reference": {"$ref": "#/inspect_references/catalog"},
            "contents": "Delivery manifest source inventory and watched paths; validation already used the complete manifest."},
        "unused_fields_and_examples": {"inspect_reference": {"$ref": "#/schema_reference"},
            "contents": "All optional formatter fields, action/closeout examples and raw-report adapter documentation."},
    }
    catalog = view["catalog"]
    for candidate in catalog.get("candidate_overview", []):
        # Roles and phase names alone do not establish suitability. The selected
        # profiles stay below; full alternative graphs are available for a gap.
        for key in ("roles", "steps"):
            candidate.pop(key, None)
    graph = view.get("selected_graph")
    if graph:
        for key in ("omitted_detail_fields", "omitted_step_detail_fields"):
            graph.pop(key, None)
        for step in graph.get("steps", {}).values():
            for key in ("input_artifacts", "output_artifact"):
                step.pop(key, None)
    manifest = view["delivery"]["manifest"]
    for key in ("sources", "watched"):
        manifest.pop(key, None)
    # Keep only source records used by actual conclusions/provenance. The full
    # inventory remains inspectable, including when discovery reports a miss.
    sources = view.pop("source_index", {})
    referenced: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/source_index/"):
                referenced.add(ref.rsplit("/", 1)[1])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(view)
    view["source_index"] = {key: value for key, value in sources.items() if key in referenced}
    brief = view["decision_brief"]
    # Preserve the seven judgments and their exact owner sections. Remove only
    # reverse links and redundant numeric ranges, not policy text or questions.
    for source in brief["reading_list"]:
        for section in source["sections"]:
            for key in ("questions", "start_line", "end_line"):
                section.pop(key, None)
    fields = draft.get("formatter_inputs", {})
    shapes = brief["field_shapes"]
    field_schema = shapes["formatter_field_schema"]
    missing = {item["missing"] for item in brief["missing_fields"]}
    unresolved = {key for key, value in fields.items() if value is None}
    # Parser-optional values are still editable current judgments, not implied
    # acceptance of defaults. Include their types until explicitly supplied.
    judgment_fields = {"phase_chain", "capability_choice", "user_required", "manager_confirmable",
        "manager_mode", "need_clarification", "need_permission", "proactive_review_decision",
        "worktree", "current_checkout", "deliver", "cleanup"}
    explicit_fields = set(request.get("formatter_inputs", {})) | set(request.get("current_explicit_inputs", {}))
    needed = missing | unresolved | (judgment_fields - explicit_fields)
    # Paired action descriptions must remain visible when actions need a choice.
    for key in ("deliver", "cleanup"):
        if key in needed:
            needed.add(key + "_description")
    if "checkout" in missing:
        needed.update(("worktree", "current_checkout"))
        for item in brief["missing_fields"]:
            if item["missing"] == "checkout":
                item.pop("field_reference", None)
                item["field_references"] = ["#/decision_brief/field_shapes/formatter_field_schema/" + key
                                            for key in ("worktree", "current_checkout")]
    shapes["formatter_field_schema"] = {key: value for key, value in field_schema.items() if key in needed}
    for key in ("action_input_examples", "closeout_examples", "preflight_capture", "preflight_file_adapter"):
        shapes.pop(key, None)
    if "delivery_contract" not in needed:
        shapes.pop("delivery_contract", None)
    # Explicit values remain visible rather than requiring a second draft read
    # just to recover what the caller already supplied. Reports retain file refs.
    for key, value in brief["fixed_inputs"].items():
        if "draft_reference" in value:
            value["value"] = request.get("formatter_inputs", {}).get(key)
            value.pop("draft_reference")
    view.pop("missing_decisions", None)  # Same complete list is indexed in brief.
    brief.pop("missing_decisions_reference", None)
    return view

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
    # Prefill only current, explicit facts. Suitability and action decisions
    # remain with the caller; no cached model or delivery fact grants authority.
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
                if input_key == "effective_locale":
                    raw_inputs.setdefault("locale_source", resolved.origin)
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
        "diagnostics": [] if normalized is None else normalized.get("diagnostics", []),
        "missing_decisions": missing,
        "formatter_inputs": normalized["values"] if complete else None,
        "formatter_draft": normalized.get("values") if normalized is not None else None,
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
        return {"status": "invalid", "diagnostics": [type(exc).__name__], "validation_error": str(exc)}
    return {"status": "rendered", "proposal": proposal, "output": output}

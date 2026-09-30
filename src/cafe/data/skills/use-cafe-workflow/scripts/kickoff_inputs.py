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

def kickoff_guidance() -> list[dict[str, str]]:
    """Read current owner sections once; this projection owns no policy or saved state."""
    selections = {
        "kickoff.md": ["## Conversation locale checklist", "## Repository content locale checklist",
                       "## Repository-informed deliver and cleanup plan", "## Kickoff contract: first blocking gate",
                       "### Complete runtime and catalog preflight", "### Derive confirmation gates",
                       "### Delivery facts to confirm"],
        "strategic_context.md": None,
        "playbook_selection.md": None,
        "model_selection.md": ["# Issue Assessment And Model Selection", "## Assess before proposing models",
                               "## Resolve phase execution requirements", "## Keep model ownership outside phase agents",
                               "## Classify the required capability band", "## Select exact chains",
                               "## Model and fallback preflight", "### Reuse successful preflight evidence"],
        "project_global_skill_sync.md": ["# Runtime And Catalog Preflight", "## Route the check results",
                                         "## Manager-managed runtime-update decision"],
        "workflow_progress.md": None,
    }
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
                           "sha256": hashlib.sha256(raw).hexdigest(), "text": "".join(lines[start:end])})
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


def decision_brief(request: dict[str, Any], missing: list[dict[str, str]]) -> dict[str, Any]:
    """Point current judgments to the supplied graph/evidence, without duplicating reports."""
    return {
        "request_text": request.get("request_text"),
        "graph_reference": "#/selected_graph",
        "evidence_references": {"delivery": "#/delivery", "models": "#/models"},
        "judgments": [
            {"decision": "scope and strategy", "inputs": ["request_text", "selected_graph.applicability"], "owner": "references/strategic_context.md"},
            {"decision": "phase suitability", "inputs": ["selected_graph.profiles", "models[].assessment"], "owner": "references/model_selection.md"},
            {"decision": "exact actions and current authority", "inputs": ["delivery.stable_conventions", "delivery.current_observations", "selected_graph.capability_setup"], "owner": "references/kickoff.md#repository-informed-deliver-and-cleanup-plan"},
            {"decision": "confirmation ownership", "inputs": ["selected_graph.confirmation_gates", "selected_graph.mandatory_confirmation_gates", "selected_graph.steps"], "owner": "references/kickoff.md#derive-confirmation-gates"},
        ],
        "missing_decisions_reference": "#/missing_decisions",
        "missing_decision_count": len(missing),
        "evidence_use": "Use sufficient valid assessments and their sources directly. Inspect raw evidence only for a specific gap, contradiction or invalidation. A hit grants no action authority or model suitability decision.",
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
                    raw_inputs[input_key] = json.loads(Path(reference).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    missing.append({"owner": "manager_research", "requirement": f"read {file_key} preflight report"})
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

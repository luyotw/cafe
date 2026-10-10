"""Selected compact preparation; no full proposal or questionnaire construction."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cafe.core.playbook import (
    confirmation_gate_steps,
    mandatory_confirmation_gate_steps,
    load_playbook_file,
    execution_graph_digest,
)
from cafe.skills.loader import SkillLoader
from _kickoff_store import VersionedJsonStore
from kickoff_preferences import PreferenceStore
from kickoff_models import assess_model_evidence


def discover(
    request, *, catalog_args, early_catalog, confirmed=None, config_dir=None, cache_dir=None
):
    selected = request.get("playbook_id")
    candidate = next((c for c in early_catalog["candidates"] if c["id"] == selected), None)
    diagnostics = list(early_catalog["diagnostics"])
    if candidate is None:
        diagnostics.append("selected_playbook_missing")
        return {"contract_mode": "compact", "status": "invalid", "diagnostics": diagnostics}
    # Lazy skill lookup validates only declarations belonging to this graph.
    loader = SkillLoader(
        **{k: catalog_args[k] for k in ("project_root", "global_root", "builtin_root")}
    )
    try:
        loaded = load_playbook_file(
            Path(candidate["path"]), source=candidate["source"], skill_loader=loader, strict=True
        )
    except (OSError, ValueError) as exc:
        return {"contract_mode": "compact", "status": "invalid",
                "diagnostics": ["selected_graph_invalid: " + str(exc)[:500]]}
    model = loaded.model
    graph = model.model_dump(mode="json")
    graph_digest = execution_graph_digest(graph)
    selected_candidate = {
        **candidate,
        "steps": graph["steps"],
        "confirmation_gates": list(confirmation_gate_steps(model)),
        "mandatory_confirmation_gates": list(mandatory_confirmation_gate_steps(model)),
    }
    inputs = dict(request.get("compact_inputs", {}))
    preferences = PreferenceStore(
        config_dir or Path.home() / ".config/cafe/kickoff",
        repository_root=catalog_args["project_root"],
    )
    saved = preferences.effective("phase.chains").value or {} if not confirmed else {}
    if "phases" not in inputs and not confirmed:
        from cafe.utils.phase_config import load_phase_step_model

        chains = []
        overrides = request.get("formatter_inputs", {}).get("phase_chain", [])
        for name, step in model.steps.items():
            if step.assignee_type not in {"agent", "hybrid"}:
                continue
            explicit = next(
                (v.split("=", 1)[1].split(",") for v in overrides if v.startswith(name + "=")), None
            )
            preferred = saved.get("steps", {}).get(name, saved.get("roles", {}).get(step.role))
            if explicit or preferred:
                chain = [
                    dict(zip(("cli", "model"), v.split(":", 1))) for v in explicit or preferred
                ]
            else:
                try:
                    phase = load_phase_step_model(
                        step_name=name, local_path=catalog_args["project_root"] / ".cafe/phases.yaml")
                    chain = [{"cli": cli, "model": model_name} for cli, model_name in phase.clis]
                except ValueError:
                    chain = []
            chains.append({"name": name, "chain": chain})
        inputs["phases"] = chains
    if confirmed:
        from collections.abc import Mapping

        def thaw(value):
            if isinstance(value, Mapping):
                return {k: thaw(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [thaw(v) for v in value]
            return value

        confirmed = thaw(confirmed)
        inputs = {
            "files": list(confirmed["file_scope"]["paths"]),
            "phases": [dict(p) for p in confirmed["phases"]],
            "review_configuration": (dict(confirmed["review_configuration"])
                                     if confirmed["review_configuration"] is not None else None),
            "delivery_contract": dict(confirmed["delivery_contract"]),
        }
    cached_models = VersionedJsonStore(
        (cache_dir or Path.home() / ".cache/cafe/kickoff/v1") / "models-v1.json",
        schema_version=1,
        collection="evidence",
    ).read()
    records = request.get("model_assessments", []) or list(cached_models.values())
    identities = {(e["cli"], e["model"]) for p in inputs.get("phases", []) for e in p["chain"]}
    if inputs.get("review_configuration"):
        review = inputs["review_configuration"]
        identities.add((review["cli"], review["model"]))
    models = [
        assess_model_evidence(
            record,
            now=datetime.now(timezone.utc),
            current_sources=request.get("current_model_sources"),
            contradictions=request.get("model_contradictions"),
        )
        for record in records
        if (record.get("provider"), record.get("model")) in identities
    ]
    native_required = any(step.execution.review_policy for step in model.steps.values())
    evidence_gaps = []
    if confirmed and confirmed["execution"]["graph_digest"] != graph_digest:
        evidence_gaps.append({"owner": "user", "requirement": "selected_graph_changed"})
        graph_digest = confirmed["execution"]["graph_digest"]
    if native_required:
        from cafe.agents.executor import validate_native_review_projection

        configuration = inputs.get("review_configuration")
        if configuration:
            try:
                version = subprocess.run(
                    [configuration["cli"], "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                ).stdout.strip()
                if version != configuration["provider_version"]:
                    raise ValueError("selected provider version evidence changed")
                validate_native_review_projection(
                    {phase["name"]: phase["chain"] for phase in inputs.get("phases", [])},
                    [name for name, step in model.steps.items() if step.execution.review_policy],
                    configuration, working_directory=request["project_root"],
                )
            except (
                OSError,
                ValueError,
                KeyError,
                StopIteration,
                subprocess.SubprocessError,
            ) as exc:
                evidence_gaps.append(
                    {
                        "owner": "selected_evidence",
                        "requirement": "native_review_configuration: " + str(exc)[:300],
                    }
                )
    return {
        "stage": "discovery",
        "contract_mode": "compact",
        "status": "ready",
        "catalog": {"candidates": [selected_candidate], "diagnostics": diagnostics, "reuse": {}},
        "selected_candidate": selected_candidate,
        "diagnostics": diagnostics,
        "inputs": inputs,
        "graph_digest": graph_digest,
        "confirmed": confirmed,
        "preferences": {},
        "delivery": {},
        "models": models,
        "evidence_gaps": evidence_gaps,
    }


def assemble(request, *, discovery):
    root = Path(request["project_root"]).resolve()
    if discovery.get("status") == "invalid":
        return {"contract_mode": "compact", "status": "invalid", "proposal": None,
                "formatter_inputs": None, "diagnostics": discovery["diagnostics"]}
    inputs = dict(discovery.get("inputs", {}))
    candidate = discovery.get("selected_candidate", {})
    review_policy = next((step["execution"]["review_policy"]
                          for step in candidate.get("steps", {}).values()
                          if step["execution"]["review_policy"]), None)
    required = ["files", "phases", "delivery_contract"]
    if review_policy is not None:
        required.append("review_configuration")
    missing = [{"owner": "user", "requirement": key}
               for key in required if not inputs.get(key)]
    missing.extend(discovery.get("evidence_gaps", []))
    for phase in inputs.get("phases", []):
        if not phase.get("chain"):
            missing.append({"owner": "user", "requirement": "phase_chain:" + phase["name"]})
        for entry in phase["chain"]:
            if not any(
                m["status"] == "hit"
                and m["identity"]["provider"] == entry["cli"]
                and m["identity"]["model"] == entry["model"]
                for m in discovery.get("models", [])
            ):
                missing.append(
                    {
                        "owner": "selected_evidence",
                        "requirement": f"model:{entry['cli']}:{entry['model']}",
                    }
                )
    review = inputs.get("review_configuration")
    if review and not any(m["status"] == "hit" and
            m["identity"]["provider"] == review["cli"] and m["identity"]["model"] == review["model"]
            for m in discovery.get("models", [])):
        missing.append({"owner": "selected_evidence",
                        "requirement": f"review_model:{review['cli']}:{review['model']}"})
    proposal = None
    diagnostics = []
    if not missing:
        try:
            confirmed = discovery.get("confirmed")
            from cafe.manager.file_scope import prepare_file_scope
            from cafe.manager.delivery import prepare_compact_delivery

            proposal = {
                "contract_mode": "compact",
                "file_scope": (
                    dict(confirmed["file_scope"])
                    if confirmed
                    else prepare_file_scope(root, inputs["files"])
                ),
                "execution": (dict(confirmed["execution"]) if confirmed else {
                    "playbook_id": candidate["id"],
                    "graph_digest": discovery["graph_digest"],
                    "review_policy": review_policy,
                }),
                "phases": inputs["phases"],
                "review_configuration": inputs.get("review_configuration"),
                "delivery_contract": (inputs["delivery_contract"] if confirmed else
                    prepare_compact_delivery(root, inputs["delivery_contract"],
                                             issue_name=request["issue_name"])),
                "locales": {
                    "conversation": {
                        "value": request.get("conversation_locale", "en-US"),
                        "source": "explicit",
                    }
                },
                "confirmation_contract": {
                    "user_required": candidate.get("confirmation_gates", []),
                    "manager_confirmable": [],
                    "mandatory_human_stops": candidate.get("mandatory_confirmation_gates", []),
                },
                "reactive_user_handoffs": {
                    "need_permission": "user_required",
                    "need_clarification": "user_required",
                    "alignment_checkpoint": "user_required",
                },
                "proactive_review": {
                    "phase_decisions": [
                        {"phase": p["name"], "decision": "not_required"} for p in inputs["phases"]
                    ]
                },
                "manager": {"mode": "unattended"},
                "checkout": {"kind": "current_checkout"},
            }
            expected_phases = {
                name
                for name, step in candidate["steps"].items()
                if step.get("assignee_type", "agent") in {"agent", "hybrid"}
            }
            if {p["name"] for p in inputs["phases"]} != expected_phases:
                raise ValueError("phase chains must match the selected graph")
            from cafe.manager._schema import validate_compact_proposal

            proposal = validate_compact_proposal(proposal)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            proposal = None
            diagnostics.append(str(exc))
    status = "incomplete" if missing else "invalid" if diagnostics else "ready"
    values = {"contract_mode": "compact", "status": status, "proposal": proposal}
    return {
        "contract_mode": "compact",
        "status": status,
        "selected_playbook": request.get("playbook_id"),
        "missing_decisions": missing,
        "diagnostics": diagnostics,
        "formatter_inputs": values,
        "formatter_draft": inputs,
        "proposal": proposal,
    }


def render(values):
    if values.get("status") != "ready" or not values.get("proposal"):
        return {"status": "incomplete", "diagnostics": ["compact_proposal_incomplete"]}
    proposal = values["proposal"]
    groups = {
        "files": proposal["file_scope"]["paths"],
        "execution": {"phases": proposal["phases"], "review": proposal["review_configuration"]},
        "delivery": {k: v for k, v in proposal["delivery_contract"].items()
                     if k != "remote_identity"},
    }
    output = "\n\n".join(
        f"## {name}\n\n{json.dumps(value, ensure_ascii=False, indent=2)}"
        for name, value in groups.items()
    )
    output += "\n\n沒有我的同意禁止修改上面列出來的檔案\n"
    return {
        "status": "rendered",
        "proposal": proposal,
        "decision_groups": groups,
        "output": output,
        "preference_offer": None,
    }

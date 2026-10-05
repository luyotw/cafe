"""Selected compact preparation; no full proposal or questionnaire construction."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from cafe.core.playbook import (
    confirmation_gate_steps, mandatory_confirmation_gate_steps, load_playbook_file,
)
from cafe.skills.loader import SkillLoader
from _kickoff_store import VersionedJsonStore


def discover(request, *, catalog_args, early_catalog, confirmed=None,
             config_dir=None, cache_dir=None):
    selected = request.get("playbook_id")
    candidate = next((c for c in early_catalog["candidates"] if c["id"] == selected), None)
    diagnostics = list(early_catalog["diagnostics"])
    if candidate is None:
        diagnostics.append("selected_playbook_missing")
        return {"contract_mode": "compact", "status": "invalid", "diagnostics": diagnostics}
    # Lazy skill lookup validates only declarations belonging to this graph.
    loader = SkillLoader(**{k: catalog_args[k] for k in
                          ("project_root", "global_root", "builtin_root")})
    loaded = load_playbook_file(Path(candidate["path"]), source=candidate["source"],
                               skill_loader=loader, strict=True)
    model = loaded.model
    graph = model.model_dump(mode="json")
    graph_digest = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    selected_candidate = {**candidate, "steps": graph["steps"],
                          "confirmation_gates": list(confirmation_gate_steps(model)),
                          "mandatory_confirmation_gates": list(mandatory_confirmation_gate_steps(model))}
    inputs = dict(request.get("compact_inputs", {}))
    if confirmed:
        inputs = {"files": list(confirmed["file_scope"]["paths"]),
                  "phases": [dict(p) for p in confirmed["phases"]],
                  "review_configuration": dict(confirmed["review_configuration"]),
                  "delivery_contract": dict(confirmed["delivery_contract"])}
    return {"stage": "discovery", "contract_mode": "compact", "status": "ready",
            "catalog": {"candidates": [selected_candidate], "diagnostics": diagnostics, "reuse": {}},
            "selected_candidate": selected_candidate, "diagnostics": diagnostics,
            "inputs": inputs, "graph_digest": graph_digest, "confirmed": confirmed,
            "preferences": {}, "delivery": {}, "models": []}


def assemble(request, *, discovery):
    root = Path(request["project_root"]).resolve()
    inputs = dict(discovery.get("inputs", {}))
    candidate = discovery.get("selected_candidate", {})
    missing = [{"owner": "user", "requirement": key} for key in
               ("files", "phases", "review_configuration", "delivery_contract")
               if not inputs.get(key)]
    proposal = None
    diagnostics = []
    if not missing:
        try:
            baseline = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                capture_output=True, text=True, check=True, timeout=10).stdout.strip()
            confirmed = discovery.get("confirmed")
            proposal = {
                "contract_mode": "compact", "file_scope": dict(confirmed["file_scope"]) if confirmed else
                    {"paths": inputs["files"], "baseline_commit": baseline, "preexisting": []},
                "execution": {"playbook_id": request["playbook_id"],
                              "graph_digest": discovery["graph_digest"]},
                "phases": inputs["phases"], "review_configuration": inputs["review_configuration"],
                "delivery_contract": inputs["delivery_contract"],
                "locales": {"conversation": {"value": request.get("conversation_locale", "en-US"),
                                              "source": "explicit"}},
                "confirmation_contract": {
                    "user_required": candidate.get("confirmation_gates", []), "manager_confirmable": [],
                    "mandatory_human_stops": candidate.get("mandatory_confirmation_gates", [])},
                "reactive_user_handoffs": {"need_permission": "user_required",
                    "need_clarification": "user_required", "alignment_checkpoint": "user_required"},
                "proactive_review": [], "manager": {"mode": "unattended"},
                "checkout": {"kind": "current_checkout"},
            }
            expected_phases = {name for name, step in candidate["steps"].items()
                               if step.get("assignee_type", "agent") in {"agent", "hybrid"}}
            if {p["name"] for p in inputs["phases"]} != expected_phases:
                raise ValueError("phase chains must match the selected graph")
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            diagnostics.append(str(exc))
    status = "incomplete" if missing else "invalid" if diagnostics else "ready"
    values = {"contract_mode": "compact", "status": status, "proposal": proposal}
    return {"contract_mode": "compact", "status": status,
            "selected_playbook": request.get("playbook_id"), "missing_decisions": missing,
            "diagnostics": diagnostics, "formatter_inputs": values,
            "formatter_draft": inputs, "proposal": proposal}


def render(values):
    if values.get("status") != "ready" or not values.get("proposal"):
        return {"status": "incomplete", "diagnostics": ["compact_proposal_incomplete"]}
    proposal = values["proposal"]
    groups = {"files": proposal["file_scope"]["paths"],
              "execution": {"phases": proposal["phases"], "review": proposal["review_configuration"]},
              "delivery": proposal["delivery_contract"]}
    output = "\n\n".join(f"## {name}\n\n{json.dumps(value, ensure_ascii=False, indent=2)}"
                          for name, value in groups.items())
    output += "\n\n沒有我的同意禁止修改上面列出來的檔案\n"
    return {"status": "rendered", "proposal": proposal, "decision_groups": groups,
            "output": output, "preference_offer": None}

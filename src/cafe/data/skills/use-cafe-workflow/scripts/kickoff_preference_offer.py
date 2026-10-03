"""Project-only preference offers, separate from confirmed workflow authority."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from kickoff_delivery import render_delivery_template
from kickoff_preferences import PreferenceStore

_KEYS = {"conversation.locale", "manager.mode", "manager.event_manager",
         "manager.poll_interval_seconds", "worktree.convention", "phase.chains",
         "confirmation.assignments", "review.decisions", "delivery.convention", "cleanup.convention"}


def default_config_dir() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(configured).expanduser() if configured else Path.home() / ".config") / "cafe/kickoff"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _project_records(store):
    path = store._store("repository").path
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or not isinstance(document.get("preferences"), dict)
            or any(not isinstance(v, dict) for v in document["preferences"].values())):
        raise ValueError("Repair the project preference store before saving")
    return document["preferences"]


def _validate_value(key, value, model=None):
    from format_kickoff_contract import _parse_phase_chains, _parse_event_manager_entries, _resolve_partition
    from cafe.core.playbook import confirmation_gate_steps
    from kickoff_preferences import canonical_language_input

    if key == "phase.chains":
        if not isinstance(value, dict) or set(value) - {"steps", "roles"} or not value:
            raise ValueError("Expected step/role model chains")
        steps, roles = value.get("steps", {}), value.get("roles", {})
        if not isinstance(steps, dict) or not isinstance(roles, dict):
            raise ValueError("Expected step/role mappings")
        if model is not None and (set(steps) - set(model.steps) or set(roles) - {s.role for s in model.steps.values()}):
            raise ValueError("Saved model selectors do not match this playbook")
        for name, chain in [*steps.items(), *roles.items()]:
            if not isinstance(chain, list) or not chain or not all(isinstance(c, str) for c in chain):
                raise ValueError("Expected ordered CLI:model chains")
            _parse_phase_chains([f"{name}={','.join(chain)}"], step_names={name})
    elif key == "review.decisions":
        if not isinstance(value, dict) or any(v not in {"required", "not_required"} for v in value.values()):
            raise ValueError("Expected per-step review decisions")
        if model is not None and set(value) - set(model.steps):
            raise ValueError("Saved review selectors do not match this playbook")
    elif key == "confirmation.assignments":
        if not isinstance(value, dict) or set(value) != {"user_required", "manager_confirmable"}:
            raise ValueError("Expected the complete assignable gate partition")
        if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in value.values()):
            raise ValueError("Expected gate name arrays")
        if model is not None:
            _resolve_partition(candidates=confirmation_gate_steps(model), user_values=value["user_required"],
                               manager_values=value["manager_confirmable"])
    elif key == "conversation.locale":
        canonical_language_input({"value": value, "origin": "explicit", "scope": "repository"})
    elif key == "manager.mode":
        if value not in {"event-driven", "attached", "unattended"}:
            raise ValueError("Invalid Manager mode")
    elif key == "manager.event_manager":
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            raise ValueError("Expected a Manager CLI chain")
        _parse_event_manager_entries(value)
    elif key == "manager.poll_interval_seconds":
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("Expected positive polling seconds")
    elif key == "worktree.convention":
        if value != {"current_checkout": True} and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Expected a checkout template")
    elif key in {"delivery.convention", "cleanup.convention"}:
        stage = "deliver" if key == "delivery.convention" else "cleanup"
        if not isinstance(value, dict) or set(value) != {stage, stage + "_description"}:
            raise ValueError("Expected actions and paired descriptions")
        render_delivery_template({"deliver": value[stage], "deliver_description": value[stage + "_description"]},
                                 {k: k for k in ("issue_name", "issue_id", "project_root", "worktree")})
    else:
        raise ValueError("Unsupported preference key")


def _require_reusable(value, context, *, checkout=False):
    if checkout and isinstance(value, str) and not any(p in value for p in ("{issue_name}", "{issue_id}")):
        raise ValueError("Checkout templates must use an issue placeholder, not this issue's path")
    if isinstance(value, dict):
        for child in value.values():
            _require_reusable(child, context)
    elif isinstance(value, list):
        for child in value:
            _require_reusable(child, context)
    elif isinstance(value, str):
        if context["issue_name"] in value or (context.get("issue_id") and re.search(
                r"(?<![A-Za-z0-9])" + re.escape(context["issue_id"]) + r"(?![A-Za-z0-9])", value)):
            raise ValueError("Use placeholders for this issue's name and ID, including descriptions")
        if context["worktree"] != context["project_root"] and context["worktree"] in value:
            raise ValueError("Use {worktree} instead of this issue's literal path")


def _previous(records, key, selector=None, role=None):
    record = records.get(key)
    if record is None:
        return {"present": False}
    if record.get("origin") != "explicit" or record.get("scope") != "repository" or "value" not in record:
        raise ValueError(f"Repair malformed project preference: {key}")
    value = record["value"]
    if selector is not None:
        if not isinstance(value, dict):
            raise ValueError(f"Repair malformed project preference: {key}")
        if key == "phase.chains":
            steps, roles = value.get("steps", {}), value.get("roles", {})
            if set(value) - {"steps", "roles"} or not isinstance(steps, dict) or not isinstance(roles, dict):
                raise ValueError(f"Repair malformed project preference: {key}")
            value = steps.get(selector, roles.get(role))
        else:
            value = value.get(selector)
        if value is None:
            return {"present": False}
    return {"present": True, "value": copy.deepcopy(value)}


def build_offer(args, proposal, model, *, store=None, templates=None, issue_id=""):
    """Derive exact save candidates from validated decisions, never product/authority fields."""
    root = args.project_root.resolve()
    store = store or PreferenceStore(getattr(args, "preference_config_dir", None) or default_config_dir(), repository_root=root)
    problems = {}
    try:
        records = _project_records(store)
    except (ValueError, OSError) as exc:
        records = {}
        problems["store"] = str(exc)
    for key, record in records.items():
        if key not in _KEYS:
            continue
        try:
            if not isinstance(record, dict):
                raise ValueError("Malformed preference record")
            _previous(records, key)
            _validate_value(key, record["value"], model)
        except (ValueError, TypeError, KeyError) as exc:
            problems[key] = str(exc)
    templates = templates or {}
    if not isinstance(templates, dict) or set(templates) - {
        "worktree.convention", "delivery.convention", "cleanup.convention"
    }:
        problems["templates"] = "preference_templates accepts only worktree, delivery and cleanup conventions"
        templates = {}
    entries, unavailable = [], []

    def add(key, value, selector=None, role=None):
        if key in problems or "store" in problems:
            return
        previous = _previous(records, key, selector, role)
        if previous == {"present": True, "value": value}:
            return
        entries.append({"id": f"{key}/{selector}" if selector is not None else key,
                        "key": key, "selector": selector, "role": role,
                        "previous": previous, "value": copy.deepcopy(value)})

    add("conversation.locale", proposal["locales"]["conversation"]["value"])
    manager = proposal["manager"]
    add("manager.mode", manager["mode"])
    if manager["mode"] == "attached":
        add("manager.poll_interval_seconds", manager["poll_interval_seconds"])
    elif manager["mode"] == "event-driven":
        add("manager.event_manager", [c["cli"] + (":" + c["model"] if c.get("model") else "")
                                      for c in manager["clis"]])
    for phase in proposal["phases"]:
        name = phase["name"]
        add("phase.chains", [f"{c['cli']}:{c['model']}" for c in phase["chain"]],
            name, model.steps[name].role)
    partition = proposal["confirmation_contract"]
    if partition["user_required"] or partition["manager_confirmable"]:
        add("confirmation.assignments", {k: partition[k] for k in ("user_required", "manager_confirmable")})
    for decision in proposal["proactive_review"]["phase_decisions"]:
        add("review.decisions", decision["decision"], decision["phase"])

    context = {"project_root": str(root), "issue_name": args.issue_name,
               "issue_id": str(issue_id), "worktree": args.worktree or str(root)}

    def candidates(key, defaults):
        if key in problems or "store" in problems:
            return []
        if key in templates:
            return [templates[key]]
        saved = records.get(key, {}).get("value")
        return ([saved] if saved is not None else []) + defaults

    def expand_worktree(value):
        if value == {"current_checkout": True}:
            return value
        rendered = render_delivery_template(
            {"deliver": [["path", value]], "deliver_description": ["Checkout"]}, context)
        return {"worktree": rendered["deliver"][0][1]}

    checkout = {"worktree": args.worktree} if args.worktree else {"current_checkout": True}
    defaults = ["{project_root}/.cafe/worktrees/{issue_name}"] if args.worktree else [{"current_checkout": True}]
    for value in candidates("worktree.convention", defaults):
        try:
            _require_reusable(value, context, checkout=True)
            expanded = expand_worktree(value)
        except (KeyError, ValueError, TypeError) as exc:
            if "worktree.convention" in templates:
                problems["worktree.convention"] = str(exc)
            continue
        if expanded == checkout:
            add("worktree.convention", value)
            break
    else:
        unavailable.append("worktree.convention")

    for stage, key in (("deliver", "delivery.convention"), ("cleanup", "cleanup.convention")):
        commands = [c["argv"] for c in proposal["delivery_contract"]["closeout_plan"][stage]]
        descriptions = getattr(args, stage + "_description")
        defaults = [{stage: [], stage + "_description": []}] if not commands else []
        # This exact built-in closeout has no issue-specific arguments to infer.
        if stage == "cleanup" and commands == [["cafe", "close"]]:
            defaults.append({stage: commands, stage + "_description": descriptions})
        if stage == "cleanup" and issue_id and len(commands) == 2 and commands[-1] == ["cafe", "close"]:
            from cafe.utils.git_utils import get_github_repo_name

            try:
                repository = get_github_repo_name(root)
            except (OSError, ValueError):
                repository = None
            if commands[0] == ["gh", "issue", "close", str(issue_id), "--repo", repository]:
                defaults.append({"cleanup": [["gh", "issue", "close", "{issue_id}", "--repo", repository], ["cafe", "close"]],
                                 "cleanup_description": [f"Close GitHub issue {repository}#{{issue_id}}.",
                                                         "Archive this CAFE issue and remove its managed worktree and branch."]})
        for value in candidates(key, defaults):
            try:
                _require_reusable(value, context)
                if not isinstance(value, dict) or set(value) != {stage, stage + "_description"}:
                    raise ValueError(f"Invalid reusable template: {key}")
                expanded = render_delivery_template(
                    {"deliver": value[stage], "deliver_description": value[stage + "_description"]}, context)
            except (KeyError, ValueError, TypeError) as exc:
                if key in templates:
                    problems[key] = str(exc)
                continue
            if expanded == {"deliver": commands, "deliver_description": descriptions}:
                add(key, value)
                break
        else:
            unavailable.append(key)
        if key in templates and key in unavailable:
            problems.setdefault(key, "Reusable template differs from this proposal")
    if "worktree.convention" in templates and "worktree.convention" in unavailable:
        problems.setdefault("worktree.convention", "Reusable template differs from this proposal")
    offer = {"schema_version": 1, "repository_identity": store.repo_id,
             "config_dir": str(store.config_dir.resolve()), "project": str(root),
             "entries": entries, "unavailable_templates": unavailable, "problems": problems}
    offer["offer_id"] = fingerprint(offer)
    return offer


def render_offer(offer, *, zh, table):
    """One visible optional operation next to the formatter's only confirmation prompt."""
    if not offer["entries"] and not offer.get("problems") and not offer["unavailable_templates"]:
        return "", None
    labels = {
        "conversation.locale": "對話語言" if zh else "Conversation language",
        "manager.mode": "Manager 模式" if zh else "Manager mode",
        "manager.event_manager": "Manager CLI" ,
        "manager.poll_interval_seconds": "輪詢秒數" if zh else "Polling seconds",
        "worktree.convention": "工作目錄規則" if zh else "Checkout convention",
        "phase.chains": "階段模型" if zh else "Phase models",
        "confirmation.assignments": "確認分工" if zh else "Confirmation assignments",
        "review.decisions": "主動審查" if zh else "Proactive review",
        "delivery.convention": "交付規則" if zh else "Delivery convention",
        "cleanup.convention": "清理規則" if zh else "Cleanup convention",
    }

    def display(key, value):
        if key == "phase.chains":
            return " → ".join(value) + (("（無備援）" if zh else " (no fallback)") if len(value) == 1 else "")
        if key == "manager.event_manager":
            return " → ".join(value)
        if key == "worktree.convention" and value == {"current_checkout": True}:
            return "目前目錄" if zh else "Current checkout"
        if key == "confirmation.assignments":
            empty = "無" if zh else "none"
            return f"User: {', '.join(value['user_required']) or empty}; Manager: {', '.join(value['manager_confirmable']) or empty}"
        if key in {"delivery.convention", "cleanup.convention"}:
            stage = "deliver" if key == "delivery.convention" else "cleanup"
            return "; ".join(shlex.join(argv) + " — " + description
                             for argv, description in zip(value[stage], value[stage + "_description"])) or (
                                 "不執行" if zh else "No actions")
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    groups = {}
    for entry in offer["entries"]:
        key, previous = entry["key"], entry["previous"]
        old = display(key, previous["value"]) if previous["present"] else ("未儲存" if zh else "Not saved")
        new = display(key, entry["value"])
        group = groups.setdefault((key, old, new), [])
        if entry["selector"] is not None:
            group.append(entry["selector"])
    rows = [[labels[key] + (f" ({', '.join(selectors)})" if selectors else ""), old, new]
            for (key, old, new), selectors in groups.items()]
    heading = "### 下次沿用（僅限本專案）" if zh else "### Remember for this project"
    intro = ("以下設定可存為本專案日後的預設，每次仍可另行指定。只儲存下列項目，不包含本次任務與授權。"
             if zh else "These settings can become this project's defaults; each kickoff can override them. Only the listed entries are saved, never this issue's task or permissions.")
    prompt = ("- **確認**：啟動本次流程，不儲存偏好。\n- **確認並記住**：啟動，並將上列設定存為本專案偏好。\n\n也可以說「確認，只記住模型」。"
              if zh else "- **Confirm**: start this workflow without saving preferences.\n- **Confirm and remember**: start and save only the entries above for this project.\n\nYou may also say: ‘Confirm, remember only the models.’")
    parts = [heading, offer["project"]]
    if rows:
        parts.extend([intro, table(["項目", "已存值", "本次設定"] if zh else ["Setting", "Saved", "This kickoff"], rows)])
    omitted = {**{key: "Needs a reusable template matching this proposal" for key in offer["unavailable_templates"]},
               **offer.get("problems", {})}
    if omitted:
        parts.append(("以下項目暫不可儲存，不影響本次啟動：" if zh else "Unavailable for saving; this does not block kickoff:") +
                     "\n" + "\n".join(f"- {key}: {reason}" for key, reason in omitted.items()))
    return "\n\n".join(parts), prompt if rows else None


def remember_offer(store, offer, *, selections, reuse=False):
    """Save only selected displayed entries, atomically preserving other project choices."""
    if not reuse:
        return {"stored": False, "reason": "explicit_reuse_consent_required"}
    if not isinstance(offer, dict):
        raise ValueError("Expected the displayed preference offer")
    body = {k: v for k, v in offer.items() if k != "offer_id"}
    if (offer.get("schema_version") != 1 or fingerprint(body) != offer.get("offer_id")
            or offer.get("repository_identity") != store.repo_id
            or offer.get("config_dir") != str(store.config_dir.resolve())):
        raise ValueError("Preference offer changed or belongs to another project/store; present a fresh offer")
    entries = {}
    for entry in offer["entries"]:
        key, selector = entry["key"], entry["selector"]
        if (key not in _KEYS or (selector is not None) != (key in {"phase.chains", "review.decisions"})
                or (selector is not None and (not isinstance(selector, str) or not selector))
                or entry["id"] != (f"{key}/{selector}" if selector is not None else key)
                or entry["id"] in entries):
            raise ValueError("Invalid preference offer entry")
        entries[entry["id"]] = entry
    chosen = list(entries) if selections == ["*"] else list(dict.fromkeys(selections))
    if not chosen or set(chosen) - set(entries):
        raise ValueError("Select only entries from the displayed preference offer")

    def update(records):
        _project_records(store)  # Do not replace an unreadable/unsupported store with an empty one.
        records = copy.deepcopy(records)
        for identity in chosen:
            entry = entries[identity]
            key, selector, value = entry["key"], entry["selector"], entry["value"]
            previous = _previous(records, key, selector, entry["role"])
            if previous != entry["previous"] and previous != {"present": True, "value": value}:
                raise ValueError(f"Project preference changed since display: {identity}")
            if key == "phase.chains":
                merged = copy.deepcopy(records.get(key, {}).get("value", {}))
                merged.setdefault("steps", {})[selector] = value
            elif key == "review.decisions":
                merged = copy.deepcopy(records.get(key, {}).get("value", {}))
                merged[selector] = value
            else:
                merged = value
            records[key] = {"value": merged, "scope": "repository", "origin": "explicit"}
            _validate_value(key, merged)
        return records

    store._store("repository").update(update)
    return {"stored": True, "scope": "repository", "entries": chosen, "offer_id": offer["offer_id"]}

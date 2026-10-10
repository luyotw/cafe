#!/usr/bin/env python3
"""Bracket already-authorized native Manager delegation in retained accounting."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from cafe.manager.costs import CostStore, native_delegation_begin, native_delegation_finalize


def _adapter(name):
    spec = importlib.util.spec_from_file_location(
        "native_accounting_" + name, Path(__file__).with_name(name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _binding(issue_dir, issue_name, workflow_id, fresh_facts):
    validator = _adapter("validate_manager_entry")
    result = validator.validate_entry(
        issue_dir=issue_dir, issue_name=issue_name, workflow_id=workflow_id, fresh_facts=fresh_facts
    )
    callback = _adapter("workflow_event_callback")
    manager_dir = callback._manager_dir(issue_dir)
    if not (manager_dir / callback.DISPATCH_STATE_FILENAME).is_file():
        raise ValueError("native Manager has no persisted host entry")
    config = callback._contract_callback_config(
        issue_dir=issue_dir, issue_name=issue_name, workflow_id=workflow_id
    )
    if config is None:
        raise ValueError("native Manager has no event host binding")
    state = callback._load_or_initialize_dispatch_state(
        manager_dir, workflow_id=workflow_id, config=config
    )
    host = callback._current_host_session_binding()
    selected = state["entries"][0]["session"]
    if (
        host is None
        or selected is None
        or selected.get("source") != "host_session"
        or selected["id"] != host["thread_id"]
        or state["active_index"] != 0
        or config["clis"][0]["cli"] != "codex"
        or result["contract_sha256"] != config["contract_sha256"]
    ):
        raise ValueError("native Manager host identity conflicts with confirmed workflow")
    return dict(root_session_id=host["thread_id"], contract_sha256=result["contract_sha256"])


def account(
    operation,
    *,
    project_root,
    issue_dir,
    issue_name,
    workflow_id,
    correlation,
    fresh_facts,
    home=None,
):
    """Validate the current contract and exact host on both boundaries."""
    binding = _binding(Path(issue_dir), issue_name, workflow_id, fresh_facts)
    store = CostStore(Path(project_root), issue_name, workflow_id)
    home = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if operation == "begin":
        return native_delegation_begin(store, binding, correlation, home)
    if operation == "finalize":
        return native_delegation_finalize(store, binding, correlation, home)
    raise ValueError("unsupported native accounting boundary")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["begin", "finalize"])
    for flag in ("project-root", "issue-dir", "issue-name", "workflow-id", "correlation"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--fresh-facts", required=True, type=json.loads)
    args = vars(parser.parse_args())
    result = account(**args)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

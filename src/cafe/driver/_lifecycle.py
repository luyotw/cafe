"""Contract lifecycle transitions behind the narrow public application API."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

from ._freshness import Freshness, compare_freshness
from ._schema import build_initial_contract, semantic_projection
from ._store import contract_lock, load_contract, write_contract


def activate(
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
    confirmed_by: str,
    confirmed_at: str,
    proposal: Mapping[str, Any],
) -> tuple[int, str, bool]:
    """Durably activate once, allowing only an exactly matching retry."""
    candidate = build_initial_contract(
        proposal=proposal,
        issue_name=issue_name,
        workflow_id=workflow_id,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
    )
    with contract_lock(issue_dir):
        try:
            current, current_sha = load_contract(
                issue_dir, issue_name=issue_name, workflow_id=workflow_id
            )
        except ValueError as exc:
            if "missing or unsafe" not in str(exc):
                raise
            digest = write_contract(issue_dir, candidate, expected_predecessor_sha256=None)
            return 1, digest, True
        if current["provenance"]["proposal_digest"] != candidate["provenance"]["proposal_digest"]:
            raise ValueError("a different confirmed contract already exists; reconfirmation is required")
        return current["revision"]["generation"], current_sha, False


def evaluate(
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
    fresh_facts: Mapping[str, Any],
) -> tuple[Freshness, dict[str, Any], str]:
    contract, digest = load_contract(issue_dir, issue_name=issue_name, workflow_id=workflow_id)
    return compare_freshness(contract, fresh_facts), contract, digest


def _changed_paths(before: Any, after: Any, prefix: str = "") -> set[str]:
    if type(before) is not type(after):
        return {prefix}
    if isinstance(before, dict):
        paths: set[str] = set()
        for key in set(before) | set(after):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                paths.add(child)
            else:
                paths |= _changed_paths(before[key], after[key], child)
        return paths
    if isinstance(before, list):
        if len(before) != len(after):
            return {prefix}
        paths: set[str] = set()
        for index, (left, right) in enumerate(zip(before, after)):
            paths |= _changed_paths(left, right, f"{prefix}[{index}]")
        return paths
    return set() if before == after else {prefix}


def _assert_delegated_model_change(current: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    if current["model_adjustment"]["authority"] != "driver_autonomous":
        raise ValueError("the effective contract does not delegate model adjustment")
    changes = _changed_paths(semantic_projection(current), semantic_projection(candidate))
    permitted = {path for path in changes if path.startswith("phases[") and ".chain" in path}
    if not changes or changes != permitted:
        raise ValueError("delegated replacement may change only ordered phase CLI/model chains")


def replace(
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
    confirmed_by: str,
    confirmed_at: str,
    proposal: Mapping[str, Any],
    expected_predecessor_sha256: str,
    kind: str,
    delegated_change: Mapping[str, Any] | None = None,
) -> tuple[int, str]:
    """Replace a complete contract by CAS after authorization is proved."""
    if kind not in {"user_reconfirmation", "delegated_change"}:
        raise ValueError("replacement requires user reconfirmation or delegated model authority")
    with contract_lock(issue_dir):
        current, current_sha = load_contract(issue_dir, issue_name=issue_name, workflow_id=workflow_id)
        if current_sha != expected_predecessor_sha256:
            raise ValueError("Driver contract predecessor is stale")
        candidate = build_initial_contract(
            proposal=proposal,
            issue_name=issue_name,
            workflow_id=workflow_id,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at,
            revision=current["revision"]["generation"] + 1,
            previous_contract_sha256=current_sha,
            provenance_kind=kind,
            delegated_change=delegated_change,
        )
        if kind == "delegated_change":
            _assert_delegated_model_change(current, candidate)
        digest = write_contract(
            issue_dir, candidate, expected_predecessor_sha256=current_sha
        )
        return candidate["revision"]["generation"], digest


def _load_legacy_confirmation(issue_dir: Path) -> Mapping[str, Any] | None:
    """Read only the bounded evidence needed to construct one adoption candidate."""
    candidates = (
        issue_dir / "driver" / "legacy_confirmation.json",
        issue_dir / "issue.yaml",
    )
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if path.suffix == ".json":
                import json

                document = json.loads(path.read_text(encoding="utf-8"))
            else:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            return None
        if isinstance(document, Mapping) and isinstance(document.get("driver_contract"), Mapping):
            return document["driver_contract"]
        if isinstance(document, Mapping) and isinstance(document.get("proposal"), Mapping):
            return document
    return None


def adopt_legacy(
    *, issue_dir: Path, issue_name: str, workflow_id: str
) -> tuple[bool, int | None, str | None, str]:
    """Adopt only complete deterministic legacy evidence; every ambiguity fails closed."""
    with contract_lock(issue_dir):
        try:
            current, digest = load_contract(issue_dir, issue_name=issue_name, workflow_id=workflow_id)
            return True, current["revision"]["generation"], digest, "already_adopted"
        except ValueError as exc:
            if "missing or unsafe" not in str(exc):
                raise
        evidence = _load_legacy_confirmation(issue_dir)
        if evidence is None:
            return False, None, None, "reconfirmation_required"
        try:
            identity = evidence.get("identity") if isinstance(evidence, Mapping) else None
            if not isinstance(identity, Mapping) or identity.get("issue_name") != issue_name or identity.get("workflow_id") != workflow_id:
                return False, None, None, "reconfirmation_required"
            proposal = evidence.get("proposal")
            confirmed_by = evidence.get("confirmed_by")
            confirmed_at = evidence.get("confirmed_at")
            if not isinstance(proposal, Mapping):
                return False, None, None, "reconfirmation_required"
            # A surviving sidecar may be evidence, never a second reader or writer authority.
            config_path = issue_dir / "driver" / "config.yaml"
            if config_path.is_file() and not config_path.is_symlink():
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                if not isinstance(config, Mapping) or config.get("mode") != proposal.get("driver", {}).get("mode"):
                    return False, None, None, "reconfirmation_required"
            contract = build_initial_contract(
                proposal=deepcopy(dict(proposal)),
                issue_name=issue_name,
                workflow_id=workflow_id,
                confirmed_by=str(confirmed_by),
                confirmed_at=str(confirmed_at),
            )
        except (AttributeError, OSError, ValueError, yaml.YAMLError):
            return False, None, None, "reconfirmation_required"
        digest = write_contract(issue_dir, contract, expected_predecessor_sha256=None)
        return True, 1, digest, "adopted"

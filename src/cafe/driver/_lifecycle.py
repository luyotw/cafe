"""Contract lifecycle transitions behind the narrow public application API."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

from cafe.core.packet_io import canonical_json

from ._freshness import Freshness, compare_freshness
from ._schema import build_initial_contract, semantic_projection
from ._store import (
    DriverContractMissingError,
    _decode_exact,
    _read_bounded,
    contract_lock,
    load_contract,
    write_contract,
)


class _ExactLegacyLoader(yaml.SafeLoader):
    """Legacy YAML remains evidence only, but ambiguous keys still fail closed."""


def _construct_exact_legacy_mapping(
    loader: _ExactLegacyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_ExactLegacyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_exact_legacy_mapping,
)


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
        except DriverContractMissingError:
            digest = write_contract(issue_dir, candidate, expected_predecessor_sha256=None)
            return 1, digest, True
        if current["provenance"]["proposal_digest"] != candidate["provenance"]["proposal_digest"]:
            raise ValueError(
                "a different confirmed contract already exists; reconfirmation is required"
            )
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


def event_callback_policy(
    *, issue_dir: Path, issue_name: str, workflow_id: str
) -> tuple[dict[str, Any] | None, str]:
    """Return the bounded event callback projection from the sole contract.

    Callback delivery has no caller-authored preflight payload.  It therefore
    deliberately projects only the already-confirmed event transport policy,
    bound to the exact contract digest read immediately before use.  All other
    entry paths continue through :func:`evaluate` and its freshness check.
    """
    contract, digest = load_contract(issue_dir, issue_name=issue_name, workflow_id=workflow_id)
    if contract["driver"]["mode"] != "event-driven":
        return None, digest
    return {"clis": deepcopy(contract["driver"]["clis"])}, digest


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


def _assert_delegated_model_change(
    current: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    if current["model_adjustment"]["authority"] != "driver_autonomous":
        raise ValueError("the effective contract does not delegate model adjustment")
    changes = _changed_paths(semantic_projection(current), semantic_projection(candidate))
    permitted = {
        path
        for path in changes
        if path.startswith("phases[") and ".chain[" in path and path.endswith(".model")
    }
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
        current, current_sha = load_contract(
            issue_dir, issue_name=issue_name, workflow_id=workflow_id
        )
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
        digest = write_contract(issue_dir, candidate, expected_predecessor_sha256=current_sha)
        return candidate["revision"]["generation"], digest


def _load_legacy_mapping(path: Path) -> Mapping[str, Any] | None:
    if not _legacy_path_present(path):
        return None
    try:
        content = _read_bounded(path, label=f"legacy evidence {path.name}")
        document = (
            _decode_exact(content)
            if path.suffix == ".json"
            else yaml.load(content.decode("utf-8"), Loader=_ExactLegacyLoader)
        )
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        return None
    return document if isinstance(document, Mapping) else None


def _legacy_path_present(path: Path) -> bool:
    """Treat unsafe/dangling legacy locations as evidence, never as absence."""
    return path.exists() or path.is_symlink()


def _load_legacy_confirmation(issue_dir: Path) -> Mapping[str, Any] | None:
    """Require every available legacy confirmation candidate to agree exactly."""
    candidates = (
        issue_dir / "driver" / "legacy_confirmation.json",
        issue_dir / "issue.yaml",
    )
    evidence: list[Mapping[str, Any]] = []
    for path in candidates:
        document = _load_legacy_mapping(path)
        if document is None:
            if _legacy_path_present(path):
                return None
            continue
        if isinstance(document.get("driver_contract"), Mapping):
            evidence.append(document["driver_contract"])
        elif isinstance(document.get("proposal"), Mapping):
            evidence.append(document)
    if not evidence:
        return None
    try:
        first = canonical_json(dict(evidence[0]))
        if any(canonical_json(dict(item)) != first for item in evidence[1:]):
            return None
    except (TypeError, ValueError):
        return None
    return evidence[0]


def _legacy_sidecars_match(issue_dir: Path, proposal: Mapping[str, Any]) -> bool:
    """Sidecars are migration evidence only; a mismatch is never guessed away."""
    issue_path = issue_dir / "issue.yaml"
    issue = _load_legacy_mapping(issue_path)
    if issue is None and _legacy_path_present(issue_path):
        return False
    if issue is not None and not _legacy_issue_projection_matches(issue, proposal):
        return False
    config_path = issue_dir / "driver" / "config.yaml"
    config = _load_legacy_mapping(config_path)
    if config is None and _legacy_path_present(config_path):
        return False
    expected_driver = proposal.get("driver")
    if config is not None:
        if not isinstance(expected_driver, Mapping) or config.get("mode") != expected_driver.get(
            "mode"
        ):
            return False
        if expected_driver.get("mode") == "event-driven" and config.get(
            "clis"
        ) != expected_driver.get("clis"):
            return False
    review_path = issue_dir / "driver" / "proactive_review.yaml"
    review = _load_legacy_mapping(review_path)
    if review is None and _legacy_path_present(review_path):
        return False
    if review is not None:
        expected_review = proposal.get("proactive_review")
        actual_review = review.get("proactive_review", review)
        try:
            if canonical_json(dict(actual_review)) != canonical_json(dict(expected_review)):
                return False
        except (TypeError, ValueError):
            return False
    phases_path = _legacy_phases_path(issue_dir)
    phases = _load_legacy_mapping(phases_path) if phases_path is not None else None
    if phases_path is not None and phases is None and _legacy_path_present(phases_path):
        return False
    if phases is not None and not _legacy_phases_match(phases, proposal):
        return False
    return True


def _legacy_issue_projection_matches(
    document: Mapping[str, Any], proposal: Mapping[str, Any]
) -> bool:
    """Reconcile every ordinary issue projection field that can govern Driver work."""
    expected_playbook = proposal.get("playbook")
    if "playbook_id" in document:
        if not isinstance(expected_playbook, Mapping) or document[
            "playbook_id"
        ] != expected_playbook.get("id"):
            return False
    for field in ("confirmation_contract", "pr"):
        if field not in document:
            continue
        actual = document[field]
        expected = proposal.get(field)
        if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
            return False
        if set(actual) - set(expected):
            return False
        if any(actual[key] != expected[key] for key in actual):
            return False
    return True


def _legacy_phases_path(issue_dir: Path) -> Path | None:
    """Locate a legacy project phase projection only for normal issue layout."""
    issue = Path(issue_dir)
    if issue.parent.name != "issues" or issue.parent.parent.name != ".cafe":
        return None
    return issue.parent.parent / "phases.yaml"


def _legacy_phases_match(document: Mapping[str, Any], proposal: Mapping[str, Any]) -> bool:
    """Require every available legacy phase projection to prove the same chains."""
    raw_phases = proposal.get("phases")
    if not isinstance(raw_phases, list):
        return False
    expected: dict[str, dict[str, Any]] = {}
    for phase in raw_phases:
        if not isinstance(phase, Mapping) or phase.get("assignee_type") not in {"agent", "hybrid"}:
            continue
        name = phase.get("name")
        role = phase.get("role")
        chain = phase.get("chain")
        if not isinstance(name, str) or not isinstance(role, str) or not isinstance(chain, list):
            return False
        expected[name] = {"role": role, "clis": chain}
    if set(document) != set(expected):
        return False
    for name, expected_phase in expected.items():
        actual = document.get(name)
        if not isinstance(actual, Mapping) or set(actual) - {"name", "role", "clis"}:
            return False
        if (
            actual.get("role") != expected_phase["role"]
            or actual.get("clis") != expected_phase["clis"]
        ):
            return False
        name_value = actual.get("name")
        if name_value is not None and (not isinstance(name_value, str) or not name_value.strip()):
            return False
    return True


def adopt_legacy(
    *, issue_dir: Path, issue_name: str, workflow_id: str
) -> tuple[bool, int | None, str | None, str]:
    """Adopt only complete deterministic legacy evidence; every ambiguity fails closed."""
    with contract_lock(issue_dir):
        try:
            current, digest = load_contract(
                issue_dir, issue_name=issue_name, workflow_id=workflow_id
            )
            return True, current["revision"]["generation"], digest, "already_adopted"
        except DriverContractMissingError:
            pass
        evidence = _load_legacy_confirmation(issue_dir)
        if evidence is None:
            return False, None, None, "reconfirmation_required"
        try:
            identity = evidence.get("identity") if isinstance(evidence, Mapping) else None
            if (
                not isinstance(identity, Mapping)
                or identity.get("issue_name") != issue_name
                or identity.get("workflow_id") != workflow_id
            ):
                return False, None, None, "reconfirmation_required"
            proposal = evidence.get("proposal")
            confirmed_by = evidence.get("confirmed_by")
            confirmed_at = evidence.get("confirmed_at")
            if not isinstance(proposal, Mapping):
                return False, None, None, "reconfirmation_required"
            if not _legacy_sidecars_match(issue_dir, proposal):
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

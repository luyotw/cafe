"""Contract lifecycle transitions behind the narrow public application API."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from ._freshness import Freshness, compare_freshness
from ._schema import build_driver_settings_update, build_initial_contract
from ._store import (
    DriverContractMissingError,
    contract_lock,
    load_contract,
    write_contract,
    write_updated_contract,
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

    Callback delivery has no caller-authored freshness payload.  It therefore
    deliberately projects only the already-confirmed event transport policy,
    bound to the exact contract digest read immediately before use.  A fully
    validated v3/v4 predecessor is safe to read for this narrow, read-only
    transport projection: it neither activates product policy nor upgrades the
    contract.  All other entry paths continue through :func:`evaluate` and its
    freshness check, which require the current contract schema.
    """
    contract, digest = load_contract(
        issue_dir,
        issue_name=issue_name,
        workflow_id=workflow_id,
        allow_legacy_upgrade=True,
    )
    if contract["driver"]["mode"] != "event-driven":
        return None, digest
    return {"clis": deepcopy(contract["driver"]["clis"])}, digest


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
) -> tuple[int, str]:
    """Replace a complete contract by CAS after user reconfirmation."""
    if kind != "user_reconfirmation":
        raise ValueError("replacement requires user reconfirmation")
    with contract_lock(issue_dir):
        current, current_sha = load_contract(
            issue_dir, issue_name=issue_name, workflow_id=workflow_id, allow_legacy_upgrade=True
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
        )
        digest = write_contract(issue_dir, candidate, expected_predecessor_sha256=current_sha)
        return candidate["revision"]["generation"], digest


def update_driver(
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
    driver: Mapping[str, Any],
    preview: bool = False,
    expected_contract_sha256: str | None = None,
) -> tuple[str, dict[str, Any], int, str]:
    """Preview or save a Driver-only update without reconfirming the contract."""

    def candidate_from_current() -> tuple[dict[str, Any], str, dict[str, Any]]:
        current, current_sha = load_contract(
            issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
            allow_legacy_upgrade=True,
        )
        if expected_contract_sha256 is not None and current_sha != expected_contract_sha256:
            raise ValueError("Driver settings update conflicts with a newer contract")
        candidate = build_driver_settings_update(
            current, driver, previous_contract_sha256=current_sha
        )
        return current, current_sha, candidate

    if preview:
        current, current_sha, candidate = candidate_from_current()
        changes = {"before": current["driver"], "after": candidate["driver"]}
        if candidate["driver"] == current["driver"]:
            return "unchanged", changes, current["revision"]["generation"], current_sha
        return "proposed", changes, candidate["revision"]["generation"], current_sha

    with contract_lock(issue_dir):
        current, current_sha, candidate = candidate_from_current()
        changes = {"before": current["driver"], "after": candidate["driver"]}
        if candidate["driver"] == current["driver"]:
            return "unchanged", changes, current["revision"]["generation"], current_sha
        digest = write_updated_contract(
            issue_dir, candidate, expected_predecessor_sha256=current_sha
        )
        return "saved", changes, candidate["revision"]["generation"], digest


def adopt_legacy(
    *, issue_dir: Path, issue_name: str, workflow_id: str
) -> tuple[bool, int | None, str | None, str]:
    """Require reconfirmation for legacy evidence that cannot express the v5 contract."""
    with contract_lock(issue_dir):
        try:
            current, digest = load_contract(
                issue_dir, issue_name=issue_name, workflow_id=workflow_id
            )
            return True, current["revision"]["generation"], digest, "already_adopted"
        except DriverContractMissingError:
            pass
        except ValueError:
            return False, None, None, "reconfirmation_required"
        # Legacy proposals include fields whose removal or reinterpretation
        # would change the confirmed authority.  Only a newly rendered and
        # explicitly reconfirmed v5 proposal may replace them.
        return False, None, None, "reconfirmation_required"

"""Reject compact drafts that cannot fit every durable downstream envelope."""

from ._execution_projection import execution_inputs
from .constraints import capture_constraints
from cafe.core.execution_artifacts import bounded_execution_json


def require_compact_capacity(policy):
    # Linux paths can occupy 4096 bytes and expand sixfold when JSON escaped.
    # All five path occurrences and both maximum closeout identities are included.
    path = "/" + "\x01" * 4095
    identity = {"issue_name": "\x01" * 255, "workflow_id": "\x01" * 255}
    bounded_execution_json(policy)
    bounded_execution_json({**policy, "schema_version": 5, "identity": identity,
        "revision": {"generation": 10**19, "previous_contract_sha256": "0" * 64},
        "provenance": {"kind": "user_reconfirmation", "confirmed_by": "user",
            "confirmed_at": "2000-01-01T00:00:00.000000+00:00", "proposal_digest": "0" * 64,
            "runtime_constraints": capture_constraints(policy)}})
    bounded_execution_json(execution_inputs(policy, identity=identity, revision=10**19,
        digest="0" * 64, root=path, review_policy="single_native",
        checkpoint_command=[path, path, "--issue-dir", path, "--root", path]))

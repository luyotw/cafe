"""Pure projection reused for delivery inputs and their producer capacity check."""


def execution_inputs(policy, *, identity, revision, digest, root, review_policy, checkpoint_command):
    scope = policy["file_scope"]
    return {
        "version": 1,
        "authority_digest": digest,
        "revision": revision,
        "identity": dict(identity),
        "root": root,
        "paths": list(scope["paths"]),
        "baseline_commit": scope["baseline_commit"],
        "preexisting": scope["preexisting"],
        "review_configuration": policy["review_configuration"],
        "review_policy": review_policy,
        "phase_chains": {phase["name"]: phase["chain"] for phase in policy["phases"]},
        "delivery_endpoint": policy["delivery_contract"],
        "native_reviewer_type": "cafe_reviewer",
        "checkpoint_command": checkpoint_command,
    }

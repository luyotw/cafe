"""U6/I1-I4/I8: generic, current-snapshot execution checkpoints."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_file_scope import repository


@pytest.fixture
def execution_context(repository):
    root, baseline = repository
    return {
        "version": 1,
        "root": str(root),
        "baseline_commit": baseline,
        "paths": ["allowed"],
        "preexisting": [],
        "authority_digest": "a" * 64,
        "revision": 1,
        "identity": {"issue_name": "sample", "workflow_id": "workflow"},
    }


@pytest.mark.parametrize("boundary", ["before_review", "resume", "before_delivery"])
def test_checkpoint_binds_current_content_authority_and_boundary(execution_context, boundary):
    from cafe.core.execution_checkpoints import checkpoint, require_checkpoint

    receipt = checkpoint(execution_context, boundary, round_id="round-1", parent_id="parent")
    assert receipt["passed"]
    require_checkpoint(execution_context, receipt, boundary)
    with pytest.raises(ValueError):
        require_checkpoint({**execution_context, "revision": 2}, receipt, boundary)
    with pytest.raises(ValueError):
        require_checkpoint({**execution_context, "authority_digest": "b" * 64}, receipt, boundary)
    (Path(execution_context["root"]) / "allowed").write_text("changed")
    with pytest.raises(ValueError):
        require_checkpoint(execution_context, receipt, boundary)


def test_scope_violation_and_missing_receipt_block(execution_context):
    from cafe.core.execution_checkpoints import checkpoint, require_checkpoint

    (Path(execution_context["root"]) / "outside").write_text("changed")
    receipt = checkpoint(execution_context, "before_review", round_id="round-1", parent_id="parent")
    assert not receipt["passed"]
    assert receipt["findings"][0]["path"] == "outside"
    for missing in (None, {}, receipt):
        with pytest.raises(ValueError):
            require_checkpoint(execution_context, missing, "before_review")


def test_runtime_checks_before_custom_named_work(execution_context):
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    root = Path(execution_context["root"])
    calls = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=root / ".cafe/issues/sample",
        playbook={
            "playbook": {"id": "custom"},
            "roles": {"operator": {}},
            "steps": {
                "repair_anything": {
                    "role": "operator",
                    "skill": "plain",
                    "execution": {"checkpoints": ["resume"]},
                    "on": {"await_agent": "_done"},
                }
            },
        },
        executor=lambda *a, **k: calls.append(a),
        execution_context=execution_context,
    )
    (root / "outside").write_text("out of scope")
    result = runtime.run()
    assert not calls
    assert not result.completed
    assert result.final_status_code == "EXECUTION_CHECKPOINT_BLOCKED"


@pytest.mark.parametrize("candidate", ["same", "different", "outside_restored", "replaced"])
def test_checkpoint_binds_selected_commit_and_its_history(execution_context, candidate):
    import os
    import subprocess
    from cafe.core.execution_checkpoints import checkpoint, require_checkpoint

    root = Path(execution_context["root"])
    baseline = execution_context["baseline_commit"]
    receipt = checkpoint(execution_context, "before_review", round_id="round", parent_id="parent")
    env = {**os.environ, "GIT_INDEX_FILE": str(root / ".git/candidate-index")}
    def git(*args, input=None):
        return subprocess.run(["git", "-C", str(root), *args], env=env, input=input,
            capture_output=True, text=True, check=True, timeout=20).stdout.strip()
    git("read-tree", baseline)
    parent = baseline
    if candidate != "same":
        blob = git("hash-object", "-w", "--stdin", input="unreviewed")
        path = "allowed" if candidate in {"different", "replaced"} else "outside"
        git("update-index", "--cacheinfo", "100644," + blob + "," + path)
        if candidate == "outside_restored":
            parent = git("commit-tree", git("write-tree"), "-p", parent, "-m", "outside edit")
            git("read-tree", baseline)
    selected = git("commit-tree", git("write-tree"), "-p", parent, "-m", "selected candidate")
    if candidate == "replaced":
        view = git("commit-tree", git("rev-parse", baseline + "^{tree}"), "-p", baseline,
                   "-m", "reviewed replacement view")
        git("replace", selected, view)
    # Current HEAD/workspace still passes. A separate immutable candidate must
    # match the reviewed implementation and cumulative allowed history itself.
    assert git("rev-parse", "HEAD") == baseline
    assert (root / "allowed").read_text() == "initial"
    require_checkpoint(execution_context, receipt, "before_review")
    if candidate == "same":
        require_checkpoint(execution_context, receipt, "before_review", source_revision=selected)
    else:
        with pytest.raises(ValueError):
            require_checkpoint(execution_context, receipt, "before_review", source_revision=selected)

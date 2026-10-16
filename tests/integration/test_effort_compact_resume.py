"""Issue #408 I3/I4: confirmed effort update reaches compact launch and production setup."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from tests.unit.test_compact_contract import compact_request, compact_proposal
from tests.unit._kickoff_test_support import load_kickoff_module
from cafe.core.blackboard import BlackboardStore
from cafe.manager.api import (
    ActivateConfirmedContract,
    ReplaceConfirmedContract,
    activate_confirmed_contract,
    replace_confirmed_contract,
    confirmed_contract_snapshot,
)
from cafe.manager._schema import freshness_semantic_facts


@pytest.mark.parametrize("provider_mismatch", [False, True])
def test_effort_only_reconfirmation_regenerates_compact_context_and_reaches_provider(
    compact_request, compact_proposal, monkeypatch, provider_mismatch
):
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    board = BlackboardStore(issue).load_or_create("build", playbook_id="selected")
    agent = root / ".cafe/agents/operator/Runner.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: Runner\ndescription: Fixture\n---\n")
    (issue / "issue.yaml").write_text(
        yaml.safe_dump({"playbook": "selected", "build": {"input_method": "auto"}})
    )
    proposal = deepcopy(compact_proposal)
    proposal["manager"] = {"mode": "attached", "poll_interval_seconds": 1}
    first = activate_confirmed_contract(
        ActivateConfirmedContract(
            issue, "sample", board.workflow_id, "user", datetime.now(timezone.utc), proposal
        )
    )

    def phase_yaml(effort):
        (root / ".cafe/phases.yaml").write_text(
            yaml.safe_dump(
                {
                    "build": {
                        "name": "Runner",
                        "role": "operator",
                        "clis": [{"cli": "codex", "model": "test", "reasoning_effort": effort}],
                    }
                }
            )
        )

    phase_yaml("high")
    from cafe.manager.file_scope import execution_scope_projection

    old = execution_scope_projection(issue, root)
    changed = deepcopy(proposal)
    changed["phases"][0]["chain"][0]["reasoning_effort"] = "low"
    second = replace_confirmed_contract(
        ReplaceConfirmedContract(
            issue,
            "sample",
            board.workflow_id,
            "user",
            datetime.now(timezone.utc),
            changed,
            first.contract_sha256,
            "user_reconfirmation",
        )
    )
    phase_yaml("low")
    binary = Path(__import__("os").environ["PATH"].split(__import__("os").pathsep)[0]) / "codex"
    binary.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
 print('0.159.3'); sys.exit(0)
Path('provider-launch.json').write_text(json.dumps(sys.argv[1:]))
issue=Path('.cafe/issues/sample')
iteration=issue/'build/iteration_001'
iteration.mkdir(parents=True,exist_ok=True)
(iteration/'output.md').write_text('Bounded execution complete.\\n')
(issue/'next_step.txt').write_text(json.dumps({'version':1,'intent':'await_agent'}))
print(json.dumps({'type':'thread.started','thread_id':'session-fixture'}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'await_agent'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
""")
    if provider_mismatch:
        binary.write_text(
            binary.read_text().replace(
                "'type':'turn.completed','usage'",
                "'type':'turn.completed','reasoning_effort':'high','usage'",
            )
        )
    binary.chmod(0o755)
    # The provider fixture capture belongs to the approved work scope.
    changed["file_scope"]["paths"].append("provider-launch.json")
    third = replace_confirmed_contract(
        ReplaceConfirmedContract(
            issue,
            "sample",
            board.workflow_id,
            "user",
            datetime.now(timezone.utc),
            changed,
            second.contract_sha256,
            "user_reconfirmation",
        )
    )
    from cafe.manager._store import load_contract

    contract, _ = load_contract(issue)
    facts = {
        "semantic_facts": freshness_semantic_facts(contract),
        "runtime_constraints": contract["provenance"]["runtime_constraints"],
    }
    owner = load_kickoff_module("run_workflow")
    outputs = []

    def process_factory(command, **kwargs):
        process = subprocess.Popen(
            command, **kwargs, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

        class Captured:
            def wait(self):
                output, _ = process.communicate(timeout=30)
                outputs.append(output)
                return process.returncode

        return Captured()

    result = owner.run(
        [
            "--issue",
            "sample",
            "--playbook",
            "selected",
            "--manager-mode",
            "attached",
            "--fresh-facts",
            json.dumps(facts),
        ],
        cwd=root,
        process_factory=process_factory,
    )
    assert result == 0, "\n".join(outputs)
    if provider_mismatch:
        assert "reasoning_effort_mismatch" in "\n".join(outputs)
        iteration_data = json.loads((issue / "build/iteration_001/iteration.json").read_text())
        assert iteration_data["response"] is None

    launched = json.loads((root / "provider-launch.json").read_text())
    assert 'model_reasoning_effort="low"' in launched
    context = json.loads((issue / "execution_context.json").read_text())
    assert context["phase_chains"]["build"][0]["reasoning_effort"] == "low"
    assert context["authority_digest"] == third.contract_sha256
    assert old["phase_chains"]["build"][0]["reasoning_effort"] == "high"
    assert old["authority_digest"] != context["authority_digest"]
    from cafe.services.status_service import StatusService

    durable = StatusService(issues_root=issue.parent).load_iteration_statuses("sample", "build")[0]
    assert durable["reasoning_evidence"]["configured"] == "low"
    assert durable["reasoning_evidence"]["applied"] == "low"
    assert durable["reasoning_evidence"]["verification"] == (
        "mismatch" if provider_mismatch else "invocation-derived"
    )
    assert durable["execution_attempts"][0]["status"] == (
        "failed" if provider_mismatch else "success"
    )
    # Existing CAS and admitted/completed phase immutability remain authoritative.
    stale = ReplaceConfirmedContract(
        issue,
        "sample",
        board.workflow_id,
        "user",
        datetime.now(timezone.utc),
        changed,
        first.contract_sha256,
        "user_reconfirmation",
    )
    with pytest.raises(ValueError):
        replace_confirmed_contract(stale)
    restored = deepcopy(changed)
    restored["phases"][0]["chain"][0]["reasoning_effort"] = "high"
    with pytest.raises(ValueError):
        replace_confirmed_contract(
            ReplaceConfirmedContract(
                issue,
                "sample",
                board.workflow_id,
                "user",
                datetime.now(timezone.utc),
                restored,
                third.contract_sha256,
                "user_reconfirmation",
            )
        )


@pytest.mark.parametrize("start_evidence", ["timestamp", "audit"])
def test_promptless_started_phase_cannot_change_effort_after_reconfirmation(compact_request, compact_proposal, start_evidence):
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    store = BlackboardStore(issue)
    board = store.load_or_create("build", playbook_id="selected")
    first = activate_confirmed_contract(ActivateConfirmedContract(issue, "sample", board.workflow_id, "user", datetime.now(timezone.utc), compact_proposal))
    if start_evidence == "timestamp":
        iteration = issue / "build/iteration_001"
        iteration.mkdir(parents=True)
        (iteration / "iteration.json").write_text(json.dumps({"iteration": 1, "timestamp": datetime.now(timezone.utc).isoformat()}))
    else:
        store.record_event(board, "step_started", {"step": "build", "attempt": 1, "hop": 1, "runtime": "fixture"})
    changed = deepcopy(compact_proposal)
    changed["phases"][0]["chain"][0]["reasoning_effort"] = "low"
    with pytest.raises(ValueError):
        replace_confirmed_contract(ReplaceConfirmedContract(issue, "sample", board.workflow_id, "user", datetime.now(timezone.utc), changed, first.contract_sha256, "user_reconfirmation"))

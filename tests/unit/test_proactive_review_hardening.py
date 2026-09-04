"""Regression coverage for proactive-review trust and resource boundaries."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.playbooks.loader import PlaybookLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow"


def _module():
    spec = importlib.util.spec_from_file_location(
        "proactive_review_hardening", SKILL_ROOT / "scripts" / "proactive_review.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy(project_root: Path, playbook_id: str, *, marker: str = "initial") -> dict[str, object]:
    playbook = PlaybookLoader(project_root=project_root).load_model(playbook_id).model
    phases = []
    for name, step in playbook.steps.items():
        if step.assignee_type not in {"agent", "hybrid"}:
            continue
        phases.append(
            {
                "phase": name,
                "selected": name == "develop",
                "rationale": f"phase-specific assessment {marker}",
                "factors": {
                    factor: "assessed"
                    for factor in (
                        "ambiguity",
                        "novelty",
                        "blast_radius",
                        "protected_risk",
                        "durable_contract",
                        "downstream_review",
                        "late_correction",
                        "cost",
                    )
                },
                **(
                    {
                        "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
                        "ordering": "non_gating",
                        "initial_review_cost": {
                            "tokens": {"estimate": "2k"},
                            "latency": {"estimate": "one minute"},
                            "assumptions": "one complete output",
                            "delay_impact": "driver acceptance only",
                        },
                        "rereview_cost": {"foreseeable": False, "reason": "unknown"},
                    }
                    if name == "develop"
                    else {}
                ),
            }
        )
    return {"playbook_id": playbook_id, "phases": phases}


def _confirmation(module, issue_name: str, policy: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "issue_name": issue_name,
        "playbook_id": policy["playbook_id"],
        "proposal_digest": module.policy_digest(policy),
        "confirmed_by": "user",
        "confirmed_at": "2026-09-04T12:00:00+00:00",
    }


def _activate(
    module, project_root: Path, issue_name: str = "hardening"
) -> tuple[Path, dict[str, object]]:
    issue_dir = project_root / ".cafe" / "issues" / issue_name
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    policy = _policy(project_root, "standard")
    module.activate_contract(
        issue_dir=issue_dir,
        project_root=project_root,
        policy=policy,
        confirmation=_confirmation(module, issue_name, policy),
    )
    return issue_dir, policy


def _reference(path: Path) -> dict[str, str]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _review_inputs(
    issue_dir: Path, project_root: Path
) -> tuple[Path, list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    output = issue_dir / "develop" / "iteration_001" / "output.md"
    requirement = issue_dir / "spec" / "iteration_001" / "output.md"
    upstream = issue_dir / "plan" / "iteration_001" / "output.md"
    repository = project_root / "docs" / "review-evidence.md"
    for path, content in (
        (output, "durable develop output"),
        (requirement, "confirmed requirement"),
        (upstream, "accepted plan"),
        (repository, "repository evidence"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return output, [_reference(requirement)], [_reference(upstream)], [_reference(repository)]


def _complete_result() -> dict[str, object]:
    return {
        "complete": True,
        "scope_adequacy": {"missing": [], "excess": [], "proportionality": "proportionate"},
        "blockers": [],
    }


def test_policy_rejects_non_finite_costs() -> None:
    """BLK-002 — a canonical policy cannot contain non-JSON numeric estimates."""
    module = _module()
    policy = _policy(PROJECT_ROOT, "standard")
    selected = next(item for item in policy["phases"] if item["selected"])
    selected["initial_review_cost"]["tokens"] = float("nan")

    with pytest.raises(ValueError):
        module.validate_policy(
            policy, playbook=PlaybookLoader(project_root=PROJECT_ROOT).load_model("standard").model
        )


def test_contract_replacement_is_root_bound_and_compare_and_swap(
    tmp_path: Path, monkeypatch
) -> None:
    """BLK-003/006/007 — replacement is live-recoverable, serialized, and issue-local."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, _ = _activate(module, project_root)
    active = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)

    (issue_dir / "issue.yaml").write_text("playbook_id: simple\n", encoding="utf-8")
    replacement = _policy(project_root, "simple", marker="simple replacement")
    module.activate_contract(
        issue_dir=issue_dir,
        project_root=project_root,
        policy=replacement,
        confirmation=_confirmation(module, issue_dir.name, replacement),
        expected_active_digest=active["proposal_digest"],
    )
    active = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)

    outside = tmp_path / "outside" / "issue"
    outside.mkdir(parents=True)
    (outside / "issue.yaml").write_text("playbook_id: simple\n", encoding="utf-8")
    with pytest.raises(module.StaleContractError):
        module.activate_contract(
            issue_dir=outside,
            project_root=project_root,
            policy=replacement,
            confirmation=_confirmation(module, outside.name, replacement),
        )

    candidate = _policy(project_root, "simple", marker="concurrent replacement")
    original_write = module._atomic_yaml_write
    first_write = threading.Event()
    allow_first_write = threading.Event()

    def delayed_write(path: Path, value: object, **kwargs: object) -> None:
        if not first_write.is_set():
            first_write.set()
            assert allow_first_write.wait(timeout=3)
        original_write(path, value, **kwargs)

    monkeypatch.setattr(module, "_atomic_yaml_write", delayed_write)
    results: list[object] = []

    def replace() -> None:
        try:
            results.append(
                module.activate_contract(
                    issue_dir=issue_dir,
                    project_root=project_root,
                    policy=candidate,
                    confirmation=_confirmation(module, issue_dir.name, candidate),
                    expected_active_digest=active["proposal_digest"],
                )
            )
        except Exception as exc:  # The public contract distinguishes stale replacement.
            results.append(exc)

    first = threading.Thread(target=replace)
    first.start()
    assert first_write.wait(timeout=3)
    second = threading.Thread(target=replace)
    second.start()
    time.sleep(0.1)
    allow_first_write.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert sum(isinstance(item, Path) for item in results) == 1
    assert sum(isinstance(item, module.StaleContractError) for item in results) == 1


def test_review_evidence_is_current_bounded_and_convergent(tmp_path: Path) -> None:
    """BLK-004/005/008 — current input evidence and prior blockers fail closed."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, _ = _activate(module, project_root)
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)

    outside_output = project_root / "outside-output.md"
    outside_output.write_text("not a phase artifact", encoding="utf-8")
    with pytest.raises(module.ReviewStateError):
        module.prepare_review_inputs(
            issue_dir=issue_dir,
            project_root=project_root,
            phase="develop",
            output_path=outside_output,
            requirements=requirements,
            upstream_artifacts=upstream,
            repository_evidence=evidence,
            correction_history=[],
        )

    manifest = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    Path(evidence[0]["path"]).write_text("changed repository evidence", encoding="utf-8")
    stale = module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=manifest["output_identity"],
        review_input_identity=manifest["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )
    assert stale["status"] == "pending"

    manifest = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=[_reference(Path(evidence[0]["path"]))],
        correction_history=[],
    )
    blocker = {
        "id": "proof",
        "evidence": "proof missing",
        "violated_constraint": "requirement",
        "expected_outcome": "add proof",
        "focused_verification": "inspect output",
    }
    module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=manifest["output_identity"],
        review_input_identity=manifest["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result={**_complete_result(), "blockers": [blocker]},
        authorized_routes=[{"to_owner": "agent", "to_step": "develop", "intent": "await_agent"}],
        correction_route={"to_owner": "agent", "to_step": "develop", "intent": "await_agent"},
    )
    output.write_text("corrected output", encoding="utf-8")
    corrected = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=[_reference(Path(evidence[0]["path"]))],
        correction_history=[{"id": "proof", "status": "still_failing"}],
    )
    unresolved = module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=corrected["output_identity"],
        review_input_identity=corrected["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )
    assert unresolved["status"] == "pending"

    oversized = issue_dir / "develop" / "iteration_002" / "output.md"
    oversized.parent.mkdir(parents=True, exist_ok=True)
    oversized.write_bytes(b"x" * (module.MAX_DURABLE_OUTPUT_BYTES + 1))
    with pytest.raises(module.ReviewStateError):
        module.prepare_review_inputs(
            issue_dir=issue_dir,
            project_root=project_root,
            phase="develop",
            output_path=oversized,
            requirements=requirements,
            upstream_artifacts=upstream,
            repository_evidence=[_reference(Path(evidence[0]["path"]))],
            correction_history=[],
        )

    module.state_path(issue_dir).write_bytes(b"x" * (module.MAX_STATE_BYTES + 1))
    with pytest.raises(module.ReviewStateError):
        module.load_review_state(issue_dir=issue_dir, project_root=project_root)


def test_contract_rejects_an_aggregate_larger_than_its_durable_reader(tmp_path: Path) -> None:
    """BLK-008 — activation must not publish an envelope its loader cannot read."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir = project_root / ".cafe" / "issues" / "oversized"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    policy = _policy(project_root, "standard")
    for phase in policy["phases"][:2]:
        phase["factors"] = {
            factor: "x" * module.MAX_STRING_CHARS for factor in phase["factors"]
        }

    with pytest.raises(ValueError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=project_root,
            policy=policy,
            confirmation=_confirmation(module, issue_dir.name, policy),
        )

    assert not module.contract_path(issue_dir).exists()


def test_before_next_phase_requires_an_unconditional_output_boundary() -> None:
    """U4 — one conditional stop cannot describe an automatic continuation as gated."""
    module = _module()

    def policy() -> dict[str, object]:
        return {
            "playbook_id": "boundary",
            "phases": [
                {
                    "phase": "develop",
                    "selected": True,
                    "rationale": "phase-specific assessment",
                    "factors": {
                        factor: "assessed"
                        for factor in (
                            "ambiguity",
                            "novelty",
                            "blast_radius",
                            "protected_risk",
                            "durable_contract",
                            "downstream_review",
                            "late_correction",
                            "cost",
                        )
                    },
                    "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
                    "ordering": "before_next_phase",
                    "initial_review_cost": {
                        "tokens": {"estimate": "2k"},
                        "latency": {"estimate": "one minute"},
                        "assumptions": "one complete output",
                        "delay_impact": "existing confirmation boundary",
                    },
                    "rereview_cost": {"foreseeable": False, "reason": "unknown"},
                }
            ],
        }

    def playbook(*, auto_continue: bool) -> SimpleNamespace:
        on = {"confirm_output": "develop"}
        if auto_continue:
            on["await_agent"] = "review"
        return SimpleNamespace(
            playbook=SimpleNamespace(id="boundary"),
            steps={
                "develop": SimpleNamespace(
                    assignee_type="agent", output_artifact="code", on=on
                )
            },
        )

    assert module.validate_policy(policy(), playbook=playbook(auto_continue=False))[
        "phases"
    ][0]["ordering"] == "before_next_phase"
    with pytest.raises(ValueError):
        module.validate_policy(policy(), playbook=playbook(auto_continue=True))


def test_activation_rejects_a_symlinked_issue_local_persistence_directory(tmp_path: Path) -> None:
    """U8 — public activation cannot publish contract or state through a directory link."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir = project_root / ".cafe" / "issues" / "linked"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (issue_dir / "driver").mkdir()
    os.symlink(outside, issue_dir / "driver" / "proactive_review", target_is_directory=True)
    policy = _policy(project_root, "standard")

    with pytest.raises(module.StaleContractError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=project_root,
            policy=policy,
            confirmation=_confirmation(module, issue_dir.name, policy),
        )

    assert not (outside / module.CONTRACT_FILENAME).exists()
    assert not (outside / module.STATE_FILENAME).exists()


def test_shared_load_rejects_a_replaced_persistence_parent(tmp_path: Path) -> None:
    """U8 — existing contract and state reads never traverse a parent directory link."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, _ = _activate(module, project_root)
    contract = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)
    persistence_directory = module.contract_path(issue_dir).parent
    outside = tmp_path / "outside"
    persistence_directory.rename(outside)
    os.symlink(outside, persistence_directory, target_is_directory=True)

    with pytest.raises(module.StaleContractError):
        module.load_active_contract(issue_dir=issue_dir, project_root=project_root)
    with pytest.raises(module.ReviewStateError):
        module._load_state_for_contract(issue_dir=issue_dir, contract=contract)


def test_initial_activation_defers_state_until_a_review_obligation_exists(tmp_path: Path) -> None:
    """U9 — activation persists authority only; phase work creates review state."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, _ = _activate(module, project_root)

    assert module.contract_path(issue_dir).is_file()
    assert not module.state_path(issue_dir).exists()
    state = module.load_review_state(issue_dir=issue_dir, project_root=project_root)
    assert state["episodes"] == {}
    assert not module.state_path(issue_dir).exists()


def test_replacement_keeps_the_active_pair_when_resetting_state_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """U8 — a state-write failure leaves the prior contract and current evidence intact."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, _ = _activate(module, project_root)
    active = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    original_contract = module.contract_path(issue_dir).read_bytes()
    original_state = module.state_path(issue_dir).read_bytes()
    replacement = _policy(project_root, "standard", marker="replacement")
    selected = next(entry for entry in replacement["phases"] if entry["selected"])
    selected["reviewer"] = {"cli": "codex", "model": "gpt-5.6-terra"}
    original_write = module._atomic_yaml_write

    def fail_state(path, value, **kwargs):
        if path.name == module.STATE_FILENAME:
            raise OSError("state device unavailable")
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(module, "_atomic_yaml_write", fail_state)
    with pytest.raises(OSError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=project_root,
            policy=replacement,
            confirmation=_confirmation(module, issue_dir.name, replacement),
            expected_active_digest=active["proposal_digest"],
        )

    assert module.contract_path(issue_dir).read_bytes() == original_contract
    assert module.state_path(issue_dir).read_bytes() == original_state


def test_replacement_recovers_the_prior_pair_after_a_state_publication_crash(
    tmp_path: Path,
) -> None:
    """U8 — a crash before contract publication restores the complete prior pair."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, _ = _activate(module, project_root)
    active = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    original_contract = module.contract_path(issue_dir).read_bytes()
    original_state = module.state_path(issue_dir).read_bytes()
    replacement = _policy(project_root, "standard", marker="crash replacement")
    selected = next(entry for entry in replacement["phases"] if entry["selected"])
    selected["reviewer"] = {"cli": "codex", "model": "gpt-5.6-terra"}
    child = """
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "crash_proactive_review", os.environ["CAFE_PROACTIVE_REVIEW"]
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_write = module._atomic_yaml_write

def stop_after_state(path, value, **kwargs):
    original_write(path, value, **kwargs)
    if path.name == module.STATE_FILENAME:
        os._exit(77)

module._atomic_yaml_write = stop_after_state
issue_dir = Path(os.environ["CAFE_ISSUE_DIR"])
policy = json.loads(os.environ["CAFE_REPLACEMENT_POLICY"])
module.activate_contract(
    issue_dir=issue_dir,
    project_root=Path(os.environ["CAFE_PROJECT_ROOT"]),
    policy=policy,
    confirmation={
        "schema_version": 1,
        "issue_name": issue_dir.name,
        "playbook_id": policy["playbook_id"],
        "proposal_digest": module.policy_digest(policy),
        "confirmed_by": "user",
        "confirmed_at": "2026-09-04T12:00:00+00:00",
    },
    expected_active_digest=os.environ["CAFE_ACTIVE_DIGEST"],
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", child],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "CAFE_ACTIVE_DIGEST": active["proposal_digest"],
            "CAFE_ISSUE_DIR": str(issue_dir),
            "CAFE_PROJECT_ROOT": str(project_root),
            "CAFE_PROACTIVE_REVIEW": str(
                SKILL_ROOT / "scripts" / "proactive_review.py"
            ),
            "CAFE_REPLACEMENT_POLICY": json.dumps(replacement),
        },
        check=False,
    )

    assert completed.returncode == 77
    assert module.contract_path(issue_dir).read_bytes() == original_contract
    assert module.state_path(issue_dir).read_bytes() != original_state

    recovered = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)

    assert recovered["proposal_digest"] == active["proposal_digest"]
    assert module.state_path(issue_dir).read_bytes() == original_state
    assert not (module.contract_path(issue_dir).parent / module.REPLACEMENT_FILENAME).exists()


def test_obligation_reconciliation_reuses_one_repository_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    """U9–U11/U14 — one callback render shares one current repository identity."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, policy = _activate(module, project_root)
    active = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)
    review_phase = next(entry for entry in policy["phases"] if entry["phase"] == "review")
    review_phase.update(
        {
            "selected": True,
            "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
            "ordering": "non_gating",
            "initial_review_cost": {
                "tokens": {"estimate": "2k"},
                "latency": {"estimate": "one minute"},
                "assumptions": "one complete output",
                "delay_impact": "driver acceptance only",
            },
            "rereview_cost": {"foreseeable": False, "reason": "unknown"},
        }
    )
    module.activate_contract(
        issue_dir=issue_dir,
        project_root=project_root,
        policy=policy,
        confirmation=_confirmation(module, issue_dir.name, policy),
        expected_active_digest=active["proposal_digest"],
    )
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    review_output = issue_dir / "review" / "iteration_001" / "output.md"
    review_output.parent.mkdir(parents=True)
    review_output.write_text("durable review output", encoding="utf-8")
    for phase, phase_output in (("develop", output), ("review", review_output)):
        manifest = module.prepare_review_inputs(
            issue_dir=issue_dir,
            project_root=project_root,
            phase=phase,
            output_path=phase_output,
            requirements=requirements,
            upstream_artifacts=upstream,
            repository_evidence=evidence,
            correction_history=[],
        )
        result = module.record_review_result(
            issue_dir=issue_dir,
            project_root=project_root,
            phase=phase,
            output_identity=manifest["output_identity"],
            review_input_identity=manifest["review_input_identity"],
            reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
            result=_complete_result(),
            authorized_routes=[],
        )
        assert result["status"] == "clean"

    calls = 0
    original_identity = module._repository_state_identity

    def counted_identity(root: Path) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return original_identity(root)

    monkeypatch.setattr(module, "_repository_state_identity", counted_identity)
    obligations = module.review_obligations(issue_dir=issue_dir, project_root=project_root)

    assert [obligation["status"] for obligation in obligations] == ["clean", "clean"]
    assert calls == 1


def test_clean_evidence_is_revalidated_before_obligations_are_reported(tmp_path: Path) -> None:
    """U9–U11 — public callback reconciliation never trusts a stored clean episode."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q", str(project_root)], check=True)
    subprocess.run(
        ["git", "-C", str(project_root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project_root), "config", "user.name", "CAFE Test"], check=True
    )
    tracked = project_root / "src" / "driver.py"
    tracked.parent.mkdir()
    tracked.write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_root), "add", "src/driver.py"], check=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-qm", "Initial driver"], check=True)

    issue_dir, policy = _activate(module, project_root)
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    manifest = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    clean = module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=manifest["output_identity"],
        review_input_identity=manifest["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )
    assert clean["status"] == "clean"

    output.write_text("changed durable output", encoding="utf-8")
    obligations = module.review_obligations(issue_dir=issue_dir, project_root=project_root)
    assert obligations == [
        {
            "phase": "develop",
            "ordering": "non_gating",
            "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
            "status": "pending",
        }
    ]
    episode = module.load_review_state(issue_dir=issue_dir, project_root=project_root)["episodes"][
        "develop"
    ]
    assert episode["pending_reason"] == "output_identity_stale"

    refreshed = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=refreshed["output_identity"],
        review_input_identity=refreshed["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )
    tracked.write_text("VERSION = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_root), "add", "src/driver.py"], check=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-qm", "Changed driver"], check=True)
    repository_drift = module.review_obligations(
        issue_dir=issue_dir, project_root=project_root
    )
    assert repository_drift[0]["status"] == "pending"
    assert module.load_review_state(issue_dir=issue_dir, project_root=project_root)[
        "episodes"
    ]["develop"]["pending_reason"] == "review_inputs_stale"

    module.state_path(issue_dir).write_text(
        module.yaml.safe_dump(
            {
                "schema_version": module.SCHEMA_VERSION,
                "proposal_digest": module.policy_digest(policy),
                "episodes": {"develop": {"status": "clean"}},
            }
        ),
        encoding="utf-8",
    )
    malformed = module.review_obligations(issue_dir=issue_dir, project_root=project_root)
    assert malformed[0]["status"] == "pending"
    assert module.load_review_state(issue_dir=issue_dir, project_root=project_root)["episodes"][
        "develop"
    ]["pending_reason"] == "review_episode_invalid"


def test_repository_head_drift_keeps_review_result_pending(tmp_path: Path) -> None:
    """BLK-004 — current repository identity is part of clean-result freshness."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q", str(project_root)], check=True)
    subprocess.run(
        ["git", "-C", str(project_root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(project_root), "config", "user.name", "CAFE Test"], check=True)
    source = project_root / "src" / "driver.py"
    source.parent.mkdir()
    source.write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_root), "add", "src/driver.py"], check=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-qm", "Initial driver"], check=True)

    issue_dir, _ = _activate(module, project_root)
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    manifest = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )

    source.write_text("VERSION = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_root), "add", "src/driver.py"], check=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-qm", "Change driver"], check=True)
    stale = module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=manifest["output_identity"],
        review_input_identity=manifest["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )

    assert stale["status"] == "pending"
    assert stale["pending_reason"] == "review_inputs_stale"

    refreshed = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    source.write_text("VERSION = 3\n", encoding="utf-8")
    dirty = module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=refreshed["output_identity"],
        review_input_identity=refreshed["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )

    assert dirty["status"] == "pending"
    assert dirty["pending_reason"] == "review_inputs_stale"


def test_untracked_repository_content_drift_keeps_review_result_pending(tmp_path: Path) -> None:
    """BLK-004 — current identity binds the contents of untracked source files."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q", str(project_root)], check=True)
    subprocess.run(
        ["git", "-C", str(project_root), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(project_root), "config", "user.name", "CAFE Test"], check=True)
    tracked = project_root / "src" / "driver.py"
    tracked.parent.mkdir()
    tracked.write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_root), "add", "src/driver.py"], check=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-qm", "Initial driver"], check=True)

    issue_dir, _ = _activate(module, project_root)
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    untracked = project_root / "src" / "untracked_behavior.py"
    untracked.write_text("BEHAVIOR = 1\n", encoding="utf-8")
    manifest = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )

    untracked.write_text("BEHAVIOR = 2\n", encoding="utf-8")
    stale = module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=manifest["output_identity"],
        review_input_identity=manifest["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )

    assert stale["status"] == "pending"
    assert stale["pending_reason"] == "review_inputs_stale"


def test_untracked_repository_content_boundary_keeps_review_result_pending(
    tmp_path: Path,
) -> None:
    """BLK-004 — content framing distinguishes two-file untracked states."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q", str(project_root)], check=True)
    subprocess.run(
        ["git", "-C", str(project_root), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(project_root), "config", "user.name", "CAFE Test"], check=True)
    tracked = project_root / "src" / "driver.py"
    tracked.parent.mkdir()
    tracked.write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_root), "add", "src/driver.py"], check=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-qm", "Initial driver"], check=True)

    issue_dir, _ = _activate(module, project_root)
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    first = project_root / "src" / "a.py"
    second = project_root / "src" / "b.py"
    first.write_bytes(b"")
    second.write_bytes(b"placeholder")
    raw_name = os.fsencode(second.relative_to(project_root))
    entry_prefix = (
        b"\0untracked\0"
        + len(raw_name).to_bytes(8, "big")
        + raw_name
        + stat.S_IMODE(second.stat().st_mode).to_bytes(4, "big")
        + b"file\0"
    )
    second.write_bytes(b"X" + entry_prefix + b"Y")
    manifest = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )

    first.write_bytes(entry_prefix + b"X")
    second.write_bytes(b"Y")
    stale = module.record_review_result(
        issue_dir=issue_dir,
        project_root=project_root,
        phase="develop",
        output_identity=manifest["output_identity"],
        review_input_identity=manifest["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result=_complete_result(),
        authorized_routes=[],
    )

    assert stale["status"] == "pending"
    assert stale["pending_reason"] == "review_inputs_stale"


def test_repository_state_identity_bounds_git_output_before_capture(tmp_path: Path) -> None:
    """BLK-011 — Git evidence exceeds its aggregate budget without large retention."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q", str(project_root)], check=True)
    subprocess.run(
        ["git", "-C", str(project_root), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(project_root), "config", "user.name", "CAFE Test"], check=True)
    source = project_root / "src" / "large.bin"
    source.parent.mkdir()
    source.write_bytes(os.urandom(2_000_000))
    subprocess.run(["git", "-C", str(project_root), "add", "src/large.bin"], check=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-qm", "Initial binary"], check=True)
    source.write_bytes(os.urandom(2_000_000))

    tracemalloc.start()
    try:
        with pytest.raises(module.ReviewStateError):
            module._repository_state_identity(project_root)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak <= module.MAX_REPOSITORY_STATE_BYTES * 4


def test_concurrent_phase_preparation_preserves_each_current_episode(
    tmp_path: Path, monkeypatch
) -> None:
    """BLK-010 — public state transitions serialize the full read-modify-write span."""
    module = _module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    issue_dir, policy = _activate(module, project_root)
    review_phase = next(item for item in policy["phases"] if item["phase"] == "review")
    review_phase.update(
        {
            "selected": True,
            "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
            "ordering": "non_gating",
            "initial_review_cost": {
                "tokens": {"estimate": "2k"},
                "latency": {"estimate": "one minute"},
                "assumptions": "one complete output",
                "delay_impact": "driver acceptance only",
            },
            "rereview_cost": {"foreseeable": False, "reason": "unknown"},
        }
    )
    active = module.load_active_contract(issue_dir=issue_dir, project_root=project_root)
    module.activate_contract(
        issue_dir=issue_dir,
        project_root=project_root,
        policy=policy,
        confirmation=_confirmation(module, issue_dir.name, policy),
        expected_active_digest=active["proposal_digest"],
    )
    output, requirements, upstream, evidence = _review_inputs(issue_dir, project_root)
    review_output = issue_dir / "review" / "iteration_001" / "output.md"
    review_output.parent.mkdir(parents=True)
    review_output.write_text("durable review output", encoding="utf-8")

    original_load = module._load_state_for_contract
    snapshots_ready = threading.Event()
    release_snapshots = threading.Event()
    snapshots = 0
    snapshot_guard = threading.Lock()

    def synchronized_load(*args, **kwargs):
        nonlocal snapshots
        state = original_load(*args, **kwargs)
        with snapshot_guard:
            snapshots += 1
            if snapshots == 2:
                snapshots_ready.set()
        if snapshots <= 2:
            assert release_snapshots.wait(timeout=3)
        return state

    monkeypatch.setattr(module, "_load_state_for_contract", synchronized_load)
    failures: list[Exception] = []

    def prepare(phase: str, phase_output: Path) -> None:
        try:
            module.prepare_review_inputs(
                issue_dir=issue_dir,
                project_root=project_root,
                phase=phase,
                output_path=phase_output,
                requirements=requirements,
                upstream_artifacts=upstream,
                repository_evidence=evidence,
                correction_history=[],
            )
        except Exception as exc:  # Preserve exceptions for the parent assertion.
            failures.append(exc)

    with module._contract_lock(issue_dir):
        first = threading.Thread(target=prepare, args=("develop", output))
        second = threading.Thread(target=prepare, args=("review", review_output))
        first.start()
        second.start()
        snapshots_ready.wait(timeout=0.25)
        release_snapshots.set()
    first.join(timeout=20)
    second.join(timeout=20)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not failures
    current_episodes = module.load_review_state(
        issue_dir=issue_dir, project_root=project_root
    )["episodes"]
    assert set(current_episodes) == {
        "develop",
        "review",
    }

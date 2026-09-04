"""Selected and empty proactive-review contract journeys."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow"


def _module():
    spec = importlib.util.spec_from_file_location(
        "proactive_review_integration", SKILL_ROOT / "scripts" / "proactive_review.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy(*, selected: str | None) -> dict[str, object]:
    phases = []
    for phase in ("spec", "plan", "develop", "review", "pr"):
        row: dict[str, object] = {
            "phase": phase,
            "selected": phase == selected,
            "rationale": "The driver assessed independent coverage, risk, and cost.",
            "factors": {
                name: "assessed"
                for name in (
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
        }
        if phase == selected:
            row.update(
                {
                    "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
                    "ordering": "non_gating",
                    "initial_review_cost": {
                        "tokens": {"estimate": "2k"},
                        "latency": {"estimate": "1 minute"},
                        "assumptions": "one complete output",
                        "delay_impact": "driver acceptance only",
                    },
                    "rereview_cost": {"foreseeable": False, "reason": "no grounded estimate"},
                }
            )
        phases.append(row)
    return {"playbook_id": "standard", "phases": phases}


def _confirmation(issue_name: str, policy: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "issue_name": issue_name,
        "playbook_id": "standard",
        "proposal_digest": hashlib.sha256(
            json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest(),
        "confirmed_by": "user",
        "confirmed_at": "2026-09-04T12:00:00+00:00",
    }


def _review_evidence(
    issue_dir: Path, project_root: Path
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    paths = (
        issue_dir / "spec" / "iteration_001" / "output.md",
        issue_dir / "plan" / "iteration_001" / "output.md",
        project_root / "repository-evidence.md",
    )
    for path, content in zip(paths, ("requirement", "accepted plan", "repository evidence")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    identities = [
        {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in paths
    ]
    return [identities[0]], [identities[1]], [identities[2]]


@pytest.mark.parametrize("selected", ["develop", None])
def test_confirmed_initial_policy_activates_only_after_issue_preparation(
    tmp_path: Path, selected: str | None
) -> None:
    """I1–I2 — both selected and empty plans persist only the active envelope."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "journey"
    policy = _policy(selected=selected)
    with pytest.raises(ValueError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=tmp_path,
            policy=policy,
            confirmation=_confirmation("journey", policy),
        )
    assert not issue_dir.exists()

    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    contract_path = module.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=policy,
        confirmation=_confirmation("journey", policy),
    )

    assert contract_path.is_file()
    assert not (issue_dir / "driver" / "proactive_review" / "candidate.yaml").exists()
    assert not (issue_dir / "driver" / "proactive_review" / "history").exists()


def test_reconfirmed_replacement_leaves_prior_contract_untouched_until_atomic_success(
    tmp_path: Path,
) -> None:
    """I3 — a complete digest-bound reconfirmation is the only replacement route."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "replacement"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    initial_policy = _policy(selected="develop")
    first = module.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=initial_policy,
        confirmation=_confirmation("replacement", initial_policy),
    )
    original = first.read_bytes()
    with pytest.raises(ValueError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=tmp_path,
            policy=_policy(selected="review"),
            confirmation=_confirmation("replacement", initial_policy),
            expected_active_digest="wrong",
        )
    assert first.read_bytes() == original


def test_blocking_review_correction_converges_to_one_clean_current_episode(tmp_path: Path) -> None:
    """I4 — a corrected durable output is re-reviewed as a whole and compacts on clean."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "convergence"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    policy = _policy(selected="develop")
    module.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=policy,
        confirmation=_confirmation("convergence", policy),
    )
    output = issue_dir / "develop" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("first output", encoding="utf-8")
    requirements, upstream, evidence = _review_evidence(issue_dir, tmp_path)
    first = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=tmp_path,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    module.record_review_result(
        issue_dir=issue_dir,
        project_root=tmp_path,
        phase="develop",
        output_identity=first["output_identity"],
        review_input_identity=first["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result={
            "complete": True,
            "scope_adequacy": {
                "missing": ["proof"],
                "excess": ["unused layer"],
                "proportionality": "not proportionate",
            },
            "blockers": [
                {
                    "id": "scope-gap",
                    "evidence": "current artifact omits proof and adds unused work",
                    "violated_constraint": "confirmed requirement",
                    "expected_outcome": "bounded implementation with proof",
                    "focused_verification": "review the full corrected artifact",
                }
            ],
        },
        authorized_routes=[{"to_owner": "agent", "to_step": "develop", "intent": "await_agent"}],
        correction_route={"to_owner": "agent", "to_step": "develop", "intent": "await_agent"},
    )
    output.write_text("corrected output", encoding="utf-8")
    second = module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=tmp_path,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[{"id": "scope-gap", "status": "resolved"}],
    )
    clean = module.record_review_result(
        issue_dir=issue_dir,
        project_root=tmp_path,
        phase="develop",
        output_identity=second["output_identity"],
        review_input_identity=second["review_input_identity"],
        reviewer={"cli": "codex", "model": "gpt-5.6-sol"},
        result={
            "complete": True,
            "scope_adequacy": {"missing": [], "excess": [], "proportionality": "proportionate"},
            "blockers": [],
        },
        authorized_routes=[],
    )

    assert clean["status"] == "clean"
    assert module.load_review_state(issue_dir=issue_dir, project_root=tmp_path)["episodes"] == {
        "develop": clean
    }


def test_later_driver_snapshot_never_mixes_contract_and_state_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I6, U9 — a public later-driver snapshot is one complete durable generation."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "snapshot"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    initial_policy = _policy(selected="develop")
    module.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=initial_policy,
        confirmation=_confirmation("snapshot", initial_policy),
    )
    output = issue_dir / "develop" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("pending output", encoding="utf-8")
    requirements, upstream, evidence = _review_evidence(issue_dir, tmp_path)
    module.prepare_review_inputs(
        issue_dir=issue_dir,
        project_root=tmp_path,
        phase="develop",
        output_path=output,
        requirements=requirements,
        upstream_artifacts=upstream,
        repository_evidence=evidence,
        correction_history=[],
    )
    old_digest = module.load_active_contract(
        issue_dir=issue_dir, project_root=tmp_path
    )["proposal_digest"]
    replacement_policy = _policy(selected="review")
    replacement_digest = module.policy_digest(replacement_policy)
    final_state_read = threading.Event()
    continue_snapshot = threading.Event()
    replacement_started = threading.Event()
    replacement_lock_acquired = threading.Event()
    replacement_completed = threading.Event()
    original_load_state = module._load_state_for_contract
    original_contract_lock = module._contract_lock
    state_read_under_generation_lock: list[bool] = []

    def pause_final_state_read(**kwargs):
        if not state_read_under_generation_lock:
            state_read_under_generation_lock.append(
                kwargs.get("directory_descriptor") is not None
            )
            final_state_read.set()
            assert continue_snapshot.wait(timeout=30)
        return original_load_state(**kwargs)

    @contextmanager
    def observe_contract_lock(*args, **kwargs):
        with original_contract_lock(*args, **kwargs) as directory_descriptor:
            if replacement_started.is_set():
                replacement_lock_acquired.set()
            yield directory_descriptor

    monkeypatch.setattr(module, "review_obligations", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_load_state_for_contract", pause_final_state_read)
    monkeypatch.setattr(module, "_contract_lock", observe_contract_lock)
    outcome: dict[str, object] = {}
    errors: list[BaseException] = []

    def load_snapshot() -> None:
        try:
            outcome["state"] = module.load_review_state(
                issue_dir=issue_dir, project_root=tmp_path
            )
        except BaseException as exc:  # pragma: no cover - assertion below reports it.
            errors.append(exc)

    def replace_contract() -> None:
        replacement_started.set()
        try:
            module.activate_contract(
                issue_dir=issue_dir,
                project_root=tmp_path,
                policy=replacement_policy,
                confirmation=_confirmation("snapshot", replacement_policy),
                expected_active_digest=old_digest,
            )
        except BaseException as exc:  # pragma: no cover - assertion below reports it.
            errors.append(exc)
        finally:
            replacement_completed.set()

    reader = threading.Thread(target=load_snapshot)
    reader.start()
    assert final_state_read.wait(timeout=30)
    replacement = threading.Thread(target=replace_contract)
    replacement.start()
    assert replacement_started.wait(timeout=5)
    if state_read_under_generation_lock[0]:
        assert not replacement_lock_acquired.wait(timeout=0.1)
    else:
        assert replacement_lock_acquired.wait(timeout=30)
        assert replacement_completed.wait(timeout=30)
    continue_snapshot.set()
    reader.join(timeout=30)
    replacement.join(timeout=30)

    assert not reader.is_alive()
    assert not replacement.is_alive()
    assert not errors
    assert module.load_active_contract(
        issue_dir=issue_dir, project_root=tmp_path
    )["proposal_digest"] == replacement_digest
    snapshot = outcome["state"]
    assert isinstance(snapshot, dict)
    if snapshot["proposal_digest"] == old_digest:
        assert snapshot["episodes"]["develop"]["status"] == "pending"
    else:
        assert snapshot == {
            "schema_version": 1,
            "proposal_digest": replacement_digest,
            "episodes": {},
        }


def test_post_confirmation_activation_command_persists_only_the_confirmed_envelope(
    tmp_path: Path,
) -> None:
    """I1 — the public post-prepare command preserves the rendered proposal binding."""
    issue_dir = tmp_path / ".cafe" / "issues" / "command"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    policy = _policy(selected="develop")
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "proactive_review.py"),
            "activate",
            "--issue-dir",
            str(issue_dir),
            "--project-root",
            str(tmp_path),
            "--policy-json",
            json.dumps(policy),
            "--confirmation-json",
            json.dumps(_confirmation("command", policy)),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (issue_dir / "driver" / "proactive_review" / "contract.yaml").is_file()
    assert not (issue_dir / "driver" / "proactive_review" / "candidate.yaml").exists()

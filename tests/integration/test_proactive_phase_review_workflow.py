"""Selected and empty proactive-review contract journeys."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

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


def test_initial_activation_authority_drift_leaves_no_persistence_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3, U8 — a stale initial activation creates no durable review state."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "initial-live-drift"
    issue_dir.mkdir(parents=True)
    issue_file = issue_dir / "issue.yaml"
    issue_file.write_text("playbook_id: standard\n", encoding="utf-8")
    policy = _policy(selected="develop")
    original_contract_lock = module._contract_lock

    @contextmanager
    def change_live_authority_before_lock(*args, **kwargs):
        issue_file.write_text("playbook_id: simple\n", encoding="utf-8")
        with original_contract_lock(*args, **kwargs) as directory_descriptor:
            yield directory_descriptor

    monkeypatch.setattr(module, "_contract_lock", change_live_authority_before_lock)

    with pytest.raises(module.StaleContractError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=tmp_path,
            policy=policy,
            confirmation=_confirmation("initial-live-drift", policy),
        )

    assert not (issue_dir / "driver").exists()


def test_failed_initial_activation_cleanup_cannot_invalidate_next_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3, U8 — a failed initial activation cannot roll back the next generation."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "initial-cleanup-overlap"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    policy = _policy(selected="develop")
    confirmation = _confirmation("initial-cleanup-overlap", policy)
    original_contract_lock = module._contract_lock
    original_cleanup = module._cleanup_failed_initial_persistence
    original_write = module._atomic_yaml_write
    lock_holders: set[int] = set()
    lock_guard = threading.Lock()
    first_write_failed = threading.Event()
    second_contract_write_ready = threading.Event()
    cleanup_complete = threading.Event()
    write_count = 0

    @contextmanager
    def tracked_contract_lock(*args, **kwargs):
        with original_contract_lock(*args, **kwargs) as directory_descriptor:
            with lock_guard:
                lock_holders.add(threading.get_ident())
            try:
                yield directory_descriptor
            finally:
                with lock_guard:
                    lock_holders.remove(threading.get_ident())

    def controlled_write(*args, **kwargs):
        nonlocal write_count
        with lock_guard:
            write_count += 1
            current_write = write_count
        if current_write == 1:
            first_write_failed.set()
            raise OSError("injected first activation write failure")
        second_contract_write_ready.set()
        assert cleanup_complete.wait(timeout=3)
        return original_write(*args, **kwargs)

    def synchronized_cleanup(*args, **kwargs):
        with lock_guard:
            cleanup_holds_contract_lock = threading.get_ident() in lock_holders
        try:
            if not cleanup_holds_contract_lock:
                assert second_contract_write_ready.wait(timeout=3)
            return original_cleanup(*args, **kwargs)
        finally:
            cleanup_complete.set()

    monkeypatch.setattr(module, "_contract_lock", tracked_contract_lock)
    monkeypatch.setattr(module, "_atomic_yaml_write", controlled_write)
    monkeypatch.setattr(module, "_cleanup_failed_initial_persistence", synchronized_cleanup)
    first_errors: list[BaseException] = []
    second_errors: list[BaseException] = []
    second_results: list[Path] = []

    def activate(errors: list[BaseException], results: list[Path]) -> None:
        try:
            results.append(
                module.activate_contract(
                    issue_dir=issue_dir,
                    project_root=tmp_path,
                    policy=policy,
                    confirmation=confirmation,
                )
            )
        except BaseException as exc:  # Preserve the concurrent outcome for assertions.
            errors.append(exc)

    first = threading.Thread(target=activate, args=(first_errors, []))
    first.start()
    assert first_write_failed.wait(timeout=3)
    second = threading.Thread(target=activate, args=(second_errors, second_results))
    second.start()
    first.join(timeout=20)
    second.join(timeout=20)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(first_errors) == 1
    assert not second_errors
    assert len(second_results) == 1
    assert second_results[0].is_file()
    assert module.load_active_contract(issue_dir=issue_dir, project_root=tmp_path)[
        "proposal_digest"
    ] == module.policy_digest(policy)


def test_failed_initial_activation_does_not_detach_a_waiting_process_directory(
    tmp_path: Path,
) -> None:
    """I3, U8 — process locks precede access to removable persistence directories."""
    issue_dir = tmp_path / ".cafe" / "issues" / "process-cleanup-overlap"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    policy = _policy(selected="develop")
    confirmation = _confirmation("process-cleanup-overlap", policy)
    first_ready = tmp_path / "first-ready"
    allow_first_failure = tmp_path / "allow-first-failure"
    second_started = tmp_path / "second-started"
    allow_second_activation = tmp_path / "allow-second-activation"
    second_lock_entered = tmp_path / "second-lock-entered"
    second_directory_opened = tmp_path / "second-directory-opened"
    script_path = SKILL_ROOT / "scripts" / "proactive_review.py"
    module_loader = """
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "proactive_review_worker", Path(sys.argv[1])
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
"""
    first_worker = module_loader + """
import json
import sys
import time

ready = Path(sys.argv[2])
proceed = Path(sys.argv[3])
issue_dir = Path(sys.argv[4])
project_root = Path(sys.argv[5])
policy = json.loads(sys.argv[6])
confirmation = json.loads(sys.argv[7])
original_write = module._atomic_yaml_write

def fail_contract_write(path, *args, **kwargs):
    if path.name == module.CONTRACT_FILENAME:
        ready.write_text("marker-created", encoding="utf-8")
        while not proceed.exists():
            time.sleep(0.01)
        raise OSError("injected first activation write failure")
    return original_write(path, *args, **kwargs)

module._atomic_yaml_write = fail_contract_write
try:
    module.activate_contract(
        issue_dir=issue_dir,
        project_root=project_root,
        policy=policy,
        confirmation=confirmation,
    )
except OSError:
    pass
else:
    raise AssertionError("first activation unexpectedly succeeded")
"""
    second_worker = module_loader + """
import json
import sys
import time
from contextlib import contextmanager

started = Path(sys.argv[2])
proceed = Path(sys.argv[3])
lock_entered = Path(sys.argv[4])
directory_opened = Path(sys.argv[5])
issue_dir = Path(sys.argv[6])
project_root = Path(sys.argv[7])
policy = json.loads(sys.argv[8])
confirmation = json.loads(sys.argv[9])
original_directory = module._proactive_review_directory
original_lock = module._contract_lock

@contextmanager
def observe_directory(*args, **kwargs):
    with original_directory(*args, **kwargs) as descriptor:
        directory_opened.write_text("opened", encoding="utf-8")
        yield descriptor

module._proactive_review_directory = observe_directory

@contextmanager
def observe_lock(*args, **kwargs):
    lock_entered.write_text("entered", encoding="utf-8")
    with original_lock(*args, **kwargs) as descriptor:
        yield descriptor

module._contract_lock = observe_lock
started.write_text("started", encoding="utf-8")
while not proceed.exists():
    time.sleep(0.01)
module.activate_contract(
    issue_dir=issue_dir,
    project_root=project_root,
    policy=policy,
    confirmation=confirmation,
)
"""
    serialized_policy = json.dumps(policy)
    serialized_confirmation = json.dumps(confirmation)
    first = subprocess.Popen(
        [
            sys.executable,
            "-c",
            first_worker,
            str(script_path),
            str(first_ready),
            str(allow_first_failure),
            str(issue_dir),
            str(tmp_path),
            serialized_policy,
            serialized_confirmation,
        ]
    )
    deadline = time.monotonic() + 5
    while not first_ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert first_ready.exists()
    second = subprocess.Popen(
        [
            sys.executable,
            "-c",
            second_worker,
            str(script_path),
            str(second_started),
            str(allow_second_activation),
            str(second_lock_entered),
            str(second_directory_opened),
            str(issue_dir),
            str(tmp_path),
            serialized_policy,
            serialized_confirmation,
        ]
    )
    deadline = time.monotonic() + 5
    while not second_started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert second_started.exists()
    allow_second_activation.write_text("activate", encoding="utf-8")
    deadline = time.monotonic() + 5
    while not second_lock_entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert second_lock_entered.exists()
    time.sleep(0.5)
    assert not second_directory_opened.exists()
    allow_first_failure.write_text("fail", encoding="utf-8")
    assert first.wait(timeout=20) == 0
    assert second.wait(timeout=20) == 0
    assert second_directory_opened.exists()
    module = _module()
    assert module.load_active_contract(issue_dir=issue_dir, project_root=tmp_path)[
        "proposal_digest"
    ] == module.policy_digest(policy)


@pytest.mark.parametrize("live_drift", ["issue_playbook", "effective_inventory"])
def test_replacement_revalidates_live_authority_after_acquiring_contract_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_drift: str
) -> None:
    """I3, U8 — lock-wait live-authority drift cannot publish a replacement."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "live-replacement"
    issue_dir.mkdir(parents=True)
    issue_file = issue_dir / "issue.yaml"
    issue_file.write_text("playbook_id: standard\n", encoding="utf-8")
    initial_policy = _policy(selected="develop")
    contract_path = module.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=initial_policy,
        confirmation=_confirmation("live-replacement", initial_policy),
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
    original_contract = contract_path.read_bytes()
    original_state = module.state_path(issue_dir).read_bytes()
    initial_digest = module.policy_digest(initial_policy)
    replacement_policy = _policy(selected="review")
    original_contract_lock = module._contract_lock
    original_live_playbook = module._live_playbook
    authority_changed = False

    def changed_live_playbook(**kwargs):
        if authority_changed and live_drift == "effective_inventory":
            return SimpleNamespace(
                playbook=SimpleNamespace(id="standard"),
                steps={},
            )
        return original_live_playbook(**kwargs)

    @contextmanager
    def change_live_authority_before_lock(*args, **kwargs):
        nonlocal authority_changed
        authority_changed = True
        if live_drift == "issue_playbook":
            issue_file.write_text("playbook_id: simple\n", encoding="utf-8")
        with original_contract_lock(*args, **kwargs) as directory_descriptor:
            yield directory_descriptor

    monkeypatch.setattr(module, "_live_playbook", changed_live_playbook)
    monkeypatch.setattr(module, "_contract_lock", change_live_authority_before_lock)

    with pytest.raises(module.StaleContractError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=tmp_path,
            policy=replacement_policy,
            confirmation=_confirmation("live-replacement", replacement_policy),
            expected_active_digest=initial_digest,
        )

    assert contract_path.read_bytes() == original_contract
    assert module.state_path(issue_dir).read_bytes() == original_state


@pytest.mark.parametrize("live_drift", ["issue_playbook", "effective_inventory"])
@pytest.mark.parametrize("drift_boundary", ["recovery", "publication", "contract"])
def test_replacement_revalidates_live_authority_after_locked_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_drift: str,
    drift_boundary: str,
) -> None:
    """I3, U8 — authority drift before publication cannot replace active evidence."""
    module = _module()
    issue_dir = tmp_path / ".cafe" / "issues" / "publication-replacement"
    issue_dir.mkdir(parents=True)
    issue_file = issue_dir / "issue.yaml"
    issue_file.write_text("playbook_id: standard\n", encoding="utf-8")
    initial_policy = _policy(selected="develop")
    contract_path = module.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=initial_policy,
        confirmation=_confirmation("publication-replacement", initial_policy),
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
    original_contract = contract_path.read_bytes()
    original_state = module.state_path(issue_dir).read_bytes()
    initial_digest = module.policy_digest(initial_policy)
    replacement_policy = _policy(selected="review")
    original_live_playbook = module._live_playbook
    original_recovery = module._recover_pending_replacement
    authority_changed = False

    def changed_live_playbook(**kwargs):
        if authority_changed and live_drift == "effective_inventory":
            return SimpleNamespace(
                playbook=SimpleNamespace(id="standard"),
                steps={},
            )
        return original_live_playbook(**kwargs)

    def change_live_authority() -> None:
        nonlocal authority_changed
        authority_changed = True
        if live_drift == "issue_playbook":
            issue_file.write_text("playbook_id: simple\n", encoding="utf-8")

    def change_live_authority_after_locked_validation(directory_descriptor, **kwargs):
        if drift_boundary == "recovery":
            change_live_authority()
        original_recovery(directory_descriptor, **kwargs)

    original_atomic_write = module._atomic_bytes_write_at

    def change_live_authority_before_publish(directory_descriptor, name, content, **kwargs):
        if drift_boundary == "publication" and name == module.ACTIVATION_FILENAME:
            change_live_authority()
        return original_atomic_write(directory_descriptor, name, content, **kwargs)

    original_replace = module.os.replace

    def change_live_authority_before_contract_replace(source, destination, **kwargs):
        if drift_boundary == "contract" and destination == module.CONTRACT_FILENAME:
            change_live_authority()
        return original_replace(source, destination, **kwargs)

    monkeypatch.setattr(module, "_live_playbook", changed_live_playbook)
    monkeypatch.setattr(
        module,
        "_recover_pending_replacement",
        change_live_authority_after_locked_validation,
    )
    monkeypatch.setattr(
        module,
        "_atomic_bytes_write_at",
        change_live_authority_before_publish,
    )
    monkeypatch.setattr(module.os, "replace", change_live_authority_before_contract_replace)

    with pytest.raises(module.StaleContractError):
        module.activate_contract(
            issue_dir=issue_dir,
            project_root=tmp_path,
            policy=replacement_policy,
            confirmation=_confirmation("publication-replacement", replacement_policy),
            expected_active_digest=initial_digest,
        )

    assert contract_path.read_bytes() == original_contract
    assert module.state_path(issue_dir).read_bytes() == original_state
    assert not (contract_path.parent / module.REPLACEMENT_FILENAME).exists()


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

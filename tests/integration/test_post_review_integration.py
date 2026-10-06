"""Destination inspection journeys at real Git and GitHub process boundaries."""

import json
import subprocess

import pytest

from cafe.utils.github import GitHubOps


@pytest.mark.parametrize("merged", [True, False])
def test_explicit_github_destination_observation_uses_only_read_api(monkeypatch, merged):
    requests = []
    payload = {
        "number": 17,
        "state": "closed",
        "merged": merged,
        "merge_commit_sha": "c" * 40,
        "head": {"sha": "a" * 40},
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
    }

    def run(argv, **kwargs):
        requests.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("cafe.utils.github.subprocess.run", run)
    observed = GitHubOps().observe_integration("owner/repo", 17)
    assert observed["repository"] == "owner/repo" and observed["pr"] == 17
    assert observed["merged"] == merged and observed["source_commit"] == "a" * 40
    assert all(
        argv == ["gh", "--version"]
        or argv == ["gh", "api", "--method", "GET", "repos/owner/repo/pulls/17"]
        for argv in requests
    )


def test_unavailable_github_preserves_error_without_mutation(monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="authentication unavailable")

    monkeypatch.setattr("cafe.utils.github.subprocess.run", run)
    from cafe.utils.github import GitHubError

    with pytest.raises(GitHubError):
        GitHubOps().observe_integration("owner/repo", 17)


@pytest.fixture
def local_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, text=True, capture_output=True, check=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Human fixture")
    (repo / "file").write_text("base\n")
    git("add", ".")
    git("commit", "-m", "baseline")
    base = git("rev-parse", "HEAD")
    git("checkout", "-b", "feature")
    (repo / "file").write_text("approved\n")
    git("commit", "-am", "approved source")
    source = git("rev-parse", "HEAD")
    return repo, git, base, source


@pytest.mark.parametrize(
    "delivery", ["equal", "ancestor", "advanced", "unrelated", "missing", "squash", "remote_only"]
)
def test_named_local_destination_ancestry_and_no_mutations(local_repository, monkeypatch, delivery):
    from cafe.core.git import GitOperations
    from cafe.core.integration import IntegrationSelection, evaluate_local

    repo, git, base, source = local_repository
    qualifies = delivery in {"equal", "ancestor", "advanced"}
    if delivery == "equal":
        git("update-ref", "refs/heads/main", source)
    elif delivery in {"ancestor", "advanced"}:
        git("checkout", "main")
        git("merge", "--no-ff", "feature", "-m", "human integration")
        if delivery == "advanced":
            git("commit", "--allow-empty", "-m", "later target work")
    elif delivery == "missing":
        git("branch", "-D", "main")
    elif delivery == "remote_only":
        git("branch", "-D", "main")
        git("update-ref", "refs/remotes/origin/main", source)
    elif delivery == "squash":
        git("checkout", "main")
        git("merge", "--squash", "feature")
        git("commit", "-m", "human squash")
    before = (git("show-ref"), git("status", "--porcelain"), git("rev-parse", "HEAD"))
    requests = []
    original = subprocess.run

    def observe(argv, **kwargs):
        requests.append(argv)
        return original(argv, **kwargs)

    monkeypatch.setattr("cafe.core.git.subprocess.run", observe)
    observed = GitOperations(str(repo)).observe_integration("main", source)
    selected = IntegrationSelection(
        target="local_branch",
        repository=str(repo),
        source_commit=source,
        feature_branch="feature",
        target_branch="main",
    )
    success, reason = evaluate_local(selected, observed)
    assert success == qualifies and reason
    assert all(
        argv[0] == "git" and argv[1] in {"rev-parse", "show-ref", "cat-file", "merge-base"}
        for argv in requests
    )
    monkeypatch.setattr("cafe.core.git.subprocess.run", original)
    assert before == (git("show-ref"), git("status", "--porcelain"), git("rev-parse", "HEAD"))


def test_missing_local_source_never_fetches(local_repository):
    from cafe.core.git import GitOperations

    repo, git, _, source = local_repository
    observed = GitOperations(str(repo)).observe_integration("main", "0" * 40)
    assert observed["unavailable"] and observed["exit_code"] != 0


@pytest.fixture
def journey(tmp_path, monkeypatch):
    from tests.integration.integration_fixture import create_journey

    return create_journey(tmp_path / "journey", monkeypatch)


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_report_then_native_verification_completes_only_with_durable_proof(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    assert journey.complete("ship").target == "destination"
    journey.select(target)
    journey.runtime().run()
    assert journey.complete("confirm").target == "land"
    journey.runtime().run()
    action = journey.pending()
    assert action.step == "land"
    assert journey.complete("performed").target == "inspect"
    assert journey.state().current_step != "done"
    failed = journey.runtime().run()
    assert not failed.completed
    journey.human_integrate(target)
    succeeded = journey.runtime().run()
    assert succeeded.completed
    assert journey.service().completion_allowed(completed=True)
    assert journey.service().records.read()["attempts"][-1]["report_result_id"]
    assert journey.runtime().run().completed


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_already_integrated_restart_preserves_absent_report_and_task_identity(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    journey.complete("ship")
    journey.select(target)
    journey.runtime().run()
    journey.complete("confirm")
    journey.runtime().run()
    action = journey.pending()
    journey.human_integrate(target)
    assert journey.runtime().run().completed
    record = journey.service().records.read()
    assert record["reports"] == [] and record["attempts"][-1]["report_result_id"] is None
    assert len([t for t in journey.service().tasks.tasks() if t.step == "land"]) == 1
    assert journey.service().tasks.get_task(action.id).status.value == "cancelled"


def test_forged_terminal_request_never_publishes_completion(journey):
    result = journey.runtime()._emit_complete(
        current_step="verdict",
        status_code="WORKFLOW_COMPLETE",
        next_step="_done",
        runtime="fixture",
        reason="forged",
    )
    assert not result.completed and journey.state().current_step != "done"


def confirmed_action(journey, target):
    journey.complete("ship")
    journey.select(target)
    journey.runtime().run()
    journey.complete("confirm")
    journey.runtime().run()
    return journey.pending()


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_conflict_and_changed_source_require_declared_review_correction(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    action = confirmed_action(journey, target)
    journey.complete("blocked", action)
    assert not journey.runtime().run().completed
    assert journey.service().records.read()["reports"][-1]["outcome"] == "blocked"
    reviewed = journey.issue_dir / "reviewed.json"
    source = json.loads(reviewed.read_text())
    source["head_sha"] = journey.base
    reviewed.write_text(json.dumps(source))
    assert not journey.runtime().run().completed
    assert journey.state().current_step == "forge"
    assert journey.state().handoff_contract.to_owner.value == "agent"


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_report_and_task_association_crash_windows_reconcile_without_duplicate(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    action = confirmed_action(journey, target)
    journey.complete("performed", action)
    with journey.service().records.transaction() as record:
        record["reports"] = []
        record["selections"][-1]["tasks"].pop("action")
    journey.human_integrate(target)
    assert journey.runtime().run().completed
    record = journey.service().records.read()
    assert len(record["reports"]) == 1
    assert record["selections"][-1]["tasks"]["action"] == action.id
    assert len([t for t in journey.service().tasks.tasks() if t.step == "land"]) == 1


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_failed_proof_persistence_keeps_report_and_retries_observation(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    action = confirmed_action(journey, target)
    journey.complete("performed", action)
    journey.human_integrate(target)
    original = __import__(
        "cafe.core.integration_records", fromlist=["atomic_write_bytes"]
    ).atomic_write_bytes

    def fail(path, data):
        if b'"attempts": [\n' in data:
            raise OSError("proof persistence unavailable")
        return original(path, data)

    monkeypatch.setattr("cafe.core.integration_records.atomic_write_bytes", fail)
    assert not journey.runtime().run().completed
    assert journey.state().current_step != "done"
    assert journey.service().records.read()["reports"][-1]["task_id"] == action.id
    monkeypatch.setattr("cafe.core.integration_records.atomic_write_bytes", original)
    assert journey.runtime().run().completed


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_precompletion_proof_rechecks_rewritten_or_changed_destination(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    confirmed_action(journey, target)
    journey.human_integrate(target)
    assert journey.service().verify()["success"]
    if target == "local_branch":
        journey.git("update-ref", "refs/heads/main", journey.base)
    else:
        journey.observation["target_branch"] = "elsewhere"
    assert not journey.runtime().run().completed
    assert not journey.service().completion_allowed()


def test_retarged_during_inspection_discards_stale_success(journey, monkeypatch):
    confirmed_action(journey, "local_branch")
    journey.human_integrate()
    from cafe.core.git import GitOperations

    original = GitOperations.observe_integration

    def observe(ops, *args):
        facts = original(ops, *args)
        journey.service().propose(
            target="local_branch",
            repository=str(journey.root),
            feature_branch="feature",
            target_branch="other",
        )
        return facts

    monkeypatch.setattr(GitOperations, "observe_integration", observe)
    with pytest.raises(ValueError):
        journey.service().verify()
    assert not journey.service().completion_allowed()


def test_stale_action_cannot_complete_a_new_selection(journey):
    action = confirmed_action(journey, "local_branch")
    journey.service().propose(
        target="local_branch",
        repository=str(journey.root),
        feature_branch="feature",
        target_branch="other",
    )
    assert journey.complete("performed", action).rejection is not None
    assert not journey.service().records.read()["reports"]


def test_review_materialization_crash_cannot_rebind_an_old_task_to_new_source(
    tmp_path, monkeypatch
):
    """U7/U2: persist the reviewed snapshot before exposing its HumanTask."""
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.integration import IntegrationService
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.playbooks.loader import PlaybookLoader
    from tests.integration.integration_fixture import create_journey

    root = tmp_path / "snapshot-crash"
    original = IntegrationService.associate

    def interrupted(service, task):
        if task.step == service.declaration.review_step:
            raise OSError("interrupted task/snapshot association")
        return original(service, task)

    monkeypatch.setattr(IntegrationService, "associate", interrupted)
    with pytest.raises(OSError):
        create_journey(root, monkeypatch)
    issue_dir = root / ".cafe/issues/delivery"
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    source_path = issue_dir / "reviewed.json"
    raw = json.loads(source_path.read_text())
    original_source = raw["head_sha"]
    raw["head_sha"] = raw["base_sha"]
    source_path.write_text(json.dumps(raw))
    monkeypatch.setattr(IntegrationService, "associate", original)
    playbook = PlaybookLoader(project_root=root).load("custom-delivery")
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=lambda *a: None
    )
    runtime.run(start_step="verdict")
    assert HumanTaskRecordStore(issue_dir).tasks()[0].id == task.id
    snapshot = IntegrationService(
        issue_dir, playbook, BlackboardStore(issue_dir).load_read_only()
    ).records.read()["reviews"][task.id]
    assert snapshot["source"]["head_sha"] == original_source


def test_local_feature_revision_drift_requires_review_and_deleted_feature_is_historical(journey):
    confirmed_action(journey, "local_branch")
    journey.git("commit", "--allow-empty", "-m", "unreviewed conflict resolution")
    assert not journey.runtime().run().completed
    assert journey.state().current_step == "forge"


def test_completed_unchanged_delivery_has_no_new_tasks_attempts_or_network(journey, monkeypatch):
    confirmed_action(journey, "local_branch")
    journey.human_integrate()
    assert journey.runtime().run().completed
    before = journey.service().records.read()

    def forbidden(*a, **kw):
        raise AssertionError("Completed delivery must not be monitored")

    monkeypatch.setattr("cafe.core.git.GitOperations.observe_integration", forbidden)
    assert journey.runtime().run().completed
    assert journey.service().records.read() == before


def test_github_changed_approved_pr_source_routes_to_declared_correction(journey, monkeypatch):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    confirmed_action(journey, "github_pr")
    journey.observation["source_commit"] = journey.base
    assert not journey.runtime().run().completed
    assert journey.state().current_step == "forge"
    assert journey.service().records.read()["attempts"][-1]["success"] is False


def test_deleted_feature_ref_still_allows_reviewed_historical_source(journey):
    confirmed_action(journey, "local_branch")
    journey.human_integrate()
    journey.git("checkout", "main")
    journey.git("branch", "-D", "feature")
    assert journey.runtime().run().completed


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_success_failure_and_recovery_issue_only_fixed_read_requests(journey, monkeypatch, target):
    from tests.integration.integration_fixture import install_github_process_fixture

    confirmed_action(journey, target)
    if target == "github_pr":
        fixture = install_github_process_fixture(journey.root, journey.source)
        import os

        monkeypatch.setenv("PATH", str(fixture) + os.pathsep + os.environ["PATH"])
    requests = []
    original = subprocess.run

    def tracked(argv, **kwargs):
        requests.append(list(argv))
        return original(argv, **kwargs)

    monkeypatch.setattr("cafe.core.git.subprocess.run", tracked)
    assert not journey.runtime().run().completed
    if target == "github_pr":
        state = json.loads((fixture / "pr.json").read_text())
        state.update(merged=True, state="closed", merge_commit_sha="c" * 40)
        (fixture / "pr.json").write_text(json.dumps(state))
    else:
        monkeypatch.setattr("cafe.core.git.subprocess.run", original)
        journey.human_integrate()
        monkeypatch.setattr("cafe.core.git.subprocess.run", tracked)
    assert journey.runtime().run().completed
    for argv in requests:
        if argv[0] == "git":
            assert argv[1] in {"rev-parse", "show-ref", "cat-file", "merge-base"}
        else:
            assert argv == ["gh", "--version"] or argv == [
                "gh",
                "api",
                "--method",
                "GET",
                "repos/owner/repo/pulls/17",
            ]
    if target == "local_branch":
        assert all(argv[0] == "git" for argv in requests)


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
@pytest.mark.parametrize("entry", ["workflow", "emit", "owner", "lifecycle"])
@pytest.mark.parametrize("boundary", ["completion_record", "terminal_event"])
def test_changed_source_at_final_publication_never_completes(
    tmp_path, monkeypatch, target, entry, boundary
):
    """U6/U8/I9/I10: source changes at persistence cannot publish invalid done."""
    from tests.integration.integration_fixture import create_journey
    from cafe.core.integration_records import IntegrationRecordStore
    from cafe.core.blackboard import BlackboardStore
    from cafe.ui.cli import app
    from typer.testing import CliRunner

    journey = create_journey(tmp_path / "publication", monkeypatch)
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    journey.complete("ship")
    journey.select(target)
    journey.runtime().run(start_step="destination")
    journey.complete("confirm")
    journey.runtime().run()
    journey.human_integrate(target)
    runtime = journey.runtime()
    if entry == "lifecycle":
        runtime.blackboard_store.set_current_step(runtime.blackboard, "land")
        runtime.blackboard_store.record_event(
            runtime.blackboard,
            "workflow_completed",
            {"step": "land", "next_step": "done", "status_code": "WORKFLOW_COMPLETE"},
        )

    def change_source():
        path = journey.issue_dir / "reviewed.json"
        raw = json.loads(path.read_text())
        raw["head_sha"] = journey.base
        path.write_text(json.dumps(raw))

    if boundary == "completion_record":
        original = IntegrationRecordStore.mark_completion

        def persist(store):
            change_source()
            return original(store)

        monkeypatch.setattr(IntegrationRecordStore, "mark_completion", persist)
    else:
        original = BlackboardStore.record_event

        def publish(store, state, kind, payload, **kwargs):
            if kind in {"workflow_completed", "completion_recovered"}:
                change_source()
            return original(store, state, kind, payload, **kwargs)

        monkeypatch.setattr(BlackboardStore, "record_event", publish)

    if entry == "workflow":
        result = CliRunner().invoke(app, ["workflow", "--issue", "delivery", "--execute"])
        assert result.exit_code == 0, (result.stdout, result.exception)
    elif entry == "lifecycle":
        result = runtime._recover_unpublished_lifecycle_position()
        assert result is not None and not result.completed
    elif entry == "owner":
        result = runtime._complete_owned_transition(
            current_step="land",
            status_code="await_agent",
            runtime="owner_dispatch",
            source="automatic",
        )
        assert not result.completed
    else:
        result = runtime._emit_complete(
            current_step="land",
            status_code="WORKFLOW_COMPLETE",
            next_step="_done",
            runtime="baton",
            reason="requested",
            update_contract=True,
        )
        assert not result.completed
    state = journey.state()
    assert state.current_step != "done"
    assert not journey.service().completion_allowed(completed=True)
    # Every direct/recovery entry re-enters the declared automatic owner.
    # The verifier then produces the ordinary graph's renewed-review outcome.
    result = journey.runtime().run()
    assert not result.completed
    state = journey.state()
    if state.current_step != "forge":
        assert not journey.runtime().run().completed
        state = journey.state()
    assert state.current_step == "forge"
    assert state.handoff_contract.to_step == "forge"
    assert any(
        e.event_type == "automatic_step_completed" and e.data.get("intent") == "manual_handoff"
        for e in state.events
    )


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
@pytest.mark.parametrize("change", ["artifact_version", "selection", "proof"])
def test_final_event_uses_current_decision_and_proof_identity(
    tmp_path, monkeypatch, target, change
):
    """U6/I9/I10: publication validates reconciled metadata and locked records."""
    from tests.integration.integration_fixture import create_journey
    from cafe.core.blackboard import BlackboardStore
    from cafe.ui.cli import app
    from typer.testing import CliRunner

    journey = create_journey(tmp_path / "final-identity", monkeypatch)
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    journey.complete("ship")
    journey.select(target)
    journey.runtime().run(start_step="destination")
    journey.complete("confirm")
    journey.runtime().run()
    journey.human_integrate(target)
    original = BlackboardStore.record_event

    def publish(store, state, kind, payload, **kwargs):
        if kind == "workflow_completed":
            if change == "artifact_version":
                fresh = store.load_read_only()
                entry = fresh.artifacts["approved_delta"]
                entry.version += 1
                store.put_artifact(fresh, entry)
            else:
                # Persistence-boundary injection represents another decision/proof
                # becoming durable after the caller's predicate was evaluated.
                path = journey.issue_dir / "integration.json"
                raw = json.loads(path.read_text())
                if change == "proof":
                    raw["attempts"][-1]["success"] = False
                else:
                    raw["selections"][-1]["selection"]["target_branch"] = "other"
                path.write_text(json.dumps(raw))
        return original(store, state, kind, payload, **kwargs)

    monkeypatch.setattr(BlackboardStore, "record_event", publish)
    result = CliRunner().invoke(app, ["workflow", "--issue", "delivery", "--execute"])
    assert result.exit_code == 0, (result.stdout, result.exception)
    state = journey.state()
    assert state.current_step != "done"
    assert not journey.service().completion_allowed(completed=True)
    assert not any(e.event_type == "workflow_completed" for e in state.events)


@pytest.mark.parametrize("entry", ["verify", "workflow"])
@pytest.mark.parametrize("failure", ["stalled", "failed"])
def test_complete_github_inspection_is_bounded_and_reaps_child(
    tmp_path, monkeypatch, entry, failure
):
    """U4/I2/I7/I13: actual gh I/O failure persists evidence and stays incomplete."""
    import os
    import sys
    from tests.integration.integration_fixture import create_journey
    from cafe.ui.cli import app
    from typer.testing import CliRunner

    journey = create_journey(tmp_path / "github-budget", monkeypatch)
    journey.complete("ship")
    journey.select("github_pr")
    journey.runtime().run(start_step="destination")
    journey.complete("confirm")
    journey.runtime().run()
    journey.complete("performed")
    fixture = journey.root / ".cafe/testing-gh"
    fixture.mkdir()
    log = fixture / "requests.jsonl"
    script = fixture / "gh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys,time\n"
        f"with open({str(log)!r}, 'a') as output: output.write(json.dumps(sys.argv[1:])+'\\n')\n"
        # A redundant availability probe would stall before any durable failure.
        "if sys.argv[1:] == ['--version']: time.sleep(60)\n"
        + ("time.sleep(60)\n" if failure == "stalled" else "sys.exit(1)\n")
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(fixture) + os.pathsep + os.environ["PATH"])
    original_run, original_popen = subprocess.run, subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        if args[0][0] == "gh":
            children.append(process)
        return process

    def bounded(argv, **kwargs):
        if argv[0] == "gh":
            assert 0 < kwargs.get("timeout", 0) <= 30
            # Scale only the process deadline at the I/O boundary. The native
            # production request must supply its supported finite budget first.
            kwargs["timeout"] = 0.25
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(subprocess, "run", bounded)
    args = (
        ["integration", "verify", "--issue", "delivery", "--json"]
        if entry == "verify"
        else ["workflow", "--issue", "delivery", "--execute"]
    )
    result = CliRunner().invoke(app, args)
    if isinstance(result.exception, AssertionError):
        raise result.exception
    assert result.exit_code == (1 if entry == "verify" else 0), (result.stdout, result.exception)
    service = journey.service()
    attempt = service.records.read()["attempts"][-1]
    assert not attempt["success"] and attempt["observed"]["unavailable"]
    assert attempt["reason"] and service.status()["next_action"]
    assert service.status()["report"]["outcome"] == "performed"
    assert journey.state().current_step != "done"
    assert not service.completion_allowed(completed=True)
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        ["api", "--method", "GET", "repos/owner/repo/pulls/17"]
    ]
    assert len(children) == 1
    assert children[0].returncode is not None and children[0].returncode != 0
    assert children[0].poll() == children[0].returncode

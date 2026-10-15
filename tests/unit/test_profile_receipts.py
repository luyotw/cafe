"""U2/U3/U4: real suite evidence, context locality and historical boundaries."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cafe.verification import VerificationReceiptError, run_verification
from cafe.verification.profile_runner import check_profile, reuse_profile, run_profile
from cafe.verification.profiles import load_profile


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in ["TZ", "CLOCK", "SEED"]:
        monkeypatch.setenv(name, "fixed")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / ".gitignore").write_text(".cafe/\n")
    (repo / ".cafe").mkdir()
    (repo / ".cafe/input").write_text("original")
    data = {
        "schema_version": 1,
        "profiles": {"custom": {"mandatory": ["bootstrap", "logout"]}},
        "suites": {
            "bootstrap": {
                "argv": [sys.executable, "-c", "print('bootstrap')"],
                "reuse": True,
                "context": {"files": [".cafe/input"], "env": ["TZ", "CLOCK", "SEED"]},
            },
            "logout": {"argv": [sys.executable, "-c", "print('logout')"], "reuse": True},
        },
    }
    (repo / "profiles.json").write_text(json.dumps(data))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    return repo


def request(repo):
    return load_profile(repo, Path("profiles.json"), "custom")


def output(repo, name="first"):
    return repo / ".cafe" / name / "output.md"


def test_real_completed_successes_and_same_context_reuse(project):
    code, path, payload = run_profile(request(project), output(project))
    assert code == 0 and payload["status"] == "passed"
    assert payload["executed"] == 2 and payload["reused"] == 0
    before = path.read_bytes()
    _, reused = reuse_profile(request(project), output(project), output(project, "next"))
    assert reused["executed"] == 0 and reused["reused"] == 2
    assert check_profile(request(project), output(project, "next")).valid
    assert path.read_bytes() == before
    assert all(row["provenance"]["receipt_sha256"] for row in reused["suites"])


@pytest.mark.parametrize("dependency", ["file", "TZ", "CLOCK", "SEED"])
def test_context_changes_rerun_only_dependent_suite(project, monkeypatch, dependency):
    for name in ["TZ", "CLOCK", "SEED"]:
        monkeypatch.setenv(name, "fixed")
    run_profile(request(project), output(project))
    if dependency == "file":
        (project / ".cafe/input").write_text("changed")
    else:
        monkeypatch.setenv(dependency, "changed")
    assert not check_profile(request(project), output(project)).valid
    with pytest.raises(VerificationReceiptError):
        reuse_profile(request(project), output(project), output(project, "reuse-only"))
    code, _, payload = run_profile(request(project), output(project, "mixed"), output(project))
    assert code == 0
    assert [(row["id"], row["action"]) for row in payload["suites"]] == [
        ("bootstrap", "executed"),
        ("logout", "reused"),
    ]


def test_irrelevant_environment_and_release_diagnostics_do_not_invalidate(project, monkeypatch):
    run_profile(request(project), output(project))
    monkeypatch.setenv("UNRELATED", "changed")
    path = output(project).parent / "verification.json"
    data = json.loads(path.read_text())
    data["producer_version"] = "unknown-future"
    path.write_text(json.dumps(data))
    assert check_profile(request(project), output(project)).valid


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "failed",
        "interrupted",
        "command",
        "log",
        "membership",
        "skipped",
        "segment",
    ],
)
def test_full_valid_labels_cannot_hide_incomplete_evidence(project, mutation):
    _, path, data = run_profile(request(project), output(project))
    if mutation == "missing":
        data["suites"].pop()
    elif mutation == "duplicate":
        data["suites"].append(data["suites"][0])
    elif mutation == "failed":
        data["suites"][0]["exit_code"] = 7
    elif mutation == "interrupted":
        data["suites"][0]["completion"] = "started"
    elif mutation == "command":
        data["suites"][0]["argv"] = ["true"]
    elif mutation == "skipped":
        data["suites"][0]["action"] = "skipped"
    elif mutation == "segment":
        data["suites"][0]["log"]["sha256"] = "0" * 64
    elif mutation == "membership":
        data["mandatory"] = ["bootstrap"]
    else:
        (path.parent / "verification.log").write_text("tampered")
    path.write_text(json.dumps(data))
    assert not check_profile(request(project), output(project)).valid


def test_legacy_receipts_cannot_claim_profiles_and_dirty_or_changed_head_reject(project):
    run_verification(
        output_file=output(project),
        command=[sys.executable, "-c", "pass"],
        scope="full",
        cwd=project,
    )
    assert not check_profile(request(project), output(project)).valid
    run_profile(request(project), output(project))
    (project / "untracked").write_text("dirty")
    assert not check_profile(request(project), output(project)).valid
    with pytest.raises(VerificationReceiptError):
        run_profile(request(project), output(project, "dirty"), output(project))
    git(project, "add", ".")
    git(project, "commit", "-m", "new head")
    assert not check_profile(request(project), output(project)).valid


def test_missing_required_input_cannot_be_reused(project):
    (project / ".cafe/input").unlink()
    code, _, data = run_profile(request(project), output(project))
    assert code == 0  # execution success does not imply context equivalence
    assert data["suites"][0]["context_errors"]
    with pytest.raises(VerificationReceiptError):
        reuse_profile(request(project), output(project), output(project, "missing"))


def test_capability_probe_observes_real_output_and_exit(project):
    path = project / "profiles.json"
    data = json.loads(path.read_text())
    data["suites"]["bootstrap"]["context"]["probes"] = [
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "value=Path('.cafe/capability').read_text(); print(value); "
                "sys.exit(0 if value.startswith('supported') else 8)"
            ),
        ]
    ]
    path.write_text(json.dumps(data))
    git(project, "add", ".")
    git(project, "commit", "-m", "declare capability")
    capability = project / ".cafe/capability"
    capability.write_text("supported-a")
    run_profile(request(project), output(project))
    for index, value in enumerate(["supported-b", "unsupported"]):
        capability.write_text(value)
        code, _, data = run_profile(request(project), output(project, str(index)), output(project))
        assert code == 0
        assert [row["action"] for row in data["suites"]] == ["executed", "reused"]
        if value == "unsupported":
            assert data["suites"][0]["context_errors"]


def test_failed_child_preserves_exit_and_later_suites_pending(project):
    path = project / "profiles.json"
    data = json.loads(path.read_text())
    data["suites"]["bootstrap"]["argv"] = [sys.executable, "-c", "print('actual failure'); exit(7)"]
    path.write_text(json.dumps(data))
    git(project, "add", ".")
    git(project, "commit", "-m", "failing baseline")
    code, receipt, data = run_profile(request(project), output(project))
    assert code == 7 and data["exit_code"] == 7
    assert data["status"] == "failed" and data["pending"] == ["logout"]
    assert "actual failure" in (receipt.parent / "verification.log").read_text()
    assert not check_profile(request(project), output(project)).valid


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", []),
        ("selected", [{}]),
        ("suites", ["bad"]),
        ("suites", None),
        ("profile", {"name": "custom", "source": "profiles.json"}),
    ],
)
def test_malformed_receipt_fails_closed_without_crashing_mixed_run(project, field, value):
    _, path, data = run_profile(request(project), output(project))
    data[field] = value
    path.write_text(json.dumps(data))
    assert not check_profile(request(project), output(project)).valid
    code, _, rerun = run_profile(request(project), output(project, "repaired"), output(project))
    assert code == 0 and rerun["executed"] == 2


def test_interrupted_attempt_cannot_leave_a_prior_passing_target(project, monkeypatch):
    run_profile(request(project), output(project))
    original = subprocess.run

    def interrupted(argv, **kwargs):
        if argv[0] == sys.executable:
            raise KeyboardInterrupt("interrupted child boundary")
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_profile(request(project), output(project))
    monkeypatch.setattr(subprocess, "run", original)
    assert not check_profile(request(project), output(project)).valid


def test_reuse_only_never_executes_when_a_probe_invalidates_context_at_boundary(
    project, monkeypatch
):
    path = project / "profiles.json"
    data = json.loads(path.read_text())
    data["suites"]["bootstrap"]["argv"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; "
        "p=Path('.cafe/suite-runs'); "
        "p.write_text(p.read_text()+'ran\\n' if p.exists() else 'ran\\n')",
    ]
    data["suites"]["bootstrap"]["context"]["probes"] = [
        [
            sys.executable,
            "-c",
            "import os; from pathlib import Path; "
            "Path('.cafe/input').write_text('changed') if "
            "os.getenv('MUTATE_INPUT')=='1' else None; "
            "print('supported')",
        ]
    ]
    path.write_text(json.dumps(data))
    git(project, "add", ".")
    git(project, "commit", "-m", "capability boundary fixture")
    monkeypatch.setenv("MUTATE_INPUT", "0")
    run_profile(request(project), output(project))
    before = (project / ".cafe/suite-runs").read_bytes()
    monkeypatch.setenv("MUTATE_INPUT", "1")
    with pytest.raises(VerificationReceiptError):
        reuse_profile(request(project), output(project), output(project, "reuse-only"))
    assert (project / ".cafe/suite-runs").read_bytes() == before

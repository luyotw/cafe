"""U10-U11: repository delivery discovery, freshness and isolation."""

from __future__ import annotations

import sys
import subprocess
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


def test_delivery_template_is_reusable_only_while_its_source_is_valid(tmp_path):
    module = load_kickoff_module("kickoff_delivery")
    now = datetime.now(timezone.utc)
    record = _record(module, tmp_path, observed_at=now)
    record["delivery_template"] = {"deliver": [["gh", "pr", "merge", "--merge"]],
                                   "deliver_description": ["Merge the PR for {issue_name}."]}
    refreshed = module.refresh_delivery({}, evidence=record, project_root=tmp_path, now=now)
    assert refreshed["refreshed"]
    warm = module.assess_delivery(refreshed["record"], project_root=tmp_path, now=now)
    assert warm["delivery_template"] == record["delivery_template"]
    (tmp_path / "docs/delivery.md").write_text("New delivery route")
    stale = module.assess_delivery(refreshed["record"], project_root=tmp_path, now=now)
    assert stale["status"] == "miss" and stale["delivery_template"] is None


@pytest.mark.parametrize("template", [
    {"deliver": "gh pr merge", "deliver_description": ["Merge"]},
    {"deliver": [["gh", "pr", "merge"]], "deliver_description": []},
    {"deliver": [["echo", "{issue_id.__class__}"]], "deliver_description": ["Bad template"]},
    {"deliver": [["echo", "{future_pr}"]], "deliver_description": ["Unknown target"]},
])
def test_invalid_delivery_template_cannot_replace_or_reuse_evidence(tmp_path, template):
    module = load_kickoff_module("kickoff_delivery")
    now = datetime.now(timezone.utc)
    record = _record(module, tmp_path, observed_at=now)
    evidence = {**record, "delivery_template": template}
    result = module.refresh_delivery(record, evidence=evidence, project_root=tmp_path, now=now)
    assert not result["refreshed"] and result["record"] == record
    assert module.assess_delivery(evidence, project_root=tmp_path, now=now)["status"] == "miss"


def test_template_expansion_retains_literal_arguments_without_shell_evaluation():
    module = load_kickoff_module("kickoff_delivery")
    template = {"deliver": [["tool", "{worktree}", "literal; $(echo nope)"]],
                "deliver_description": ["Deliver {issue_name}."]}
    rendered = module.render_delivery_template(template, {"worktree": "/a path/with spaces", "issue_name": "sample"})
    assert rendered["deliver"] == [["tool", "/a path/with spaces", "literal; $(echo nope)"]]
    with pytest.raises(ValueError, match="requires worktree"):
        module.render_delivery_template(template, {"issue_name": "sample"})


def _record(module, project: Path, *, observed_at: datetime) -> dict:
    source = project / "docs/delivery.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("Use the repository release notes.", encoding="utf-8")
    manifest = module.discover_delivery_manifest(project)
    source_item = next(item for item in manifest["sources"] if item["path"] == "docs/delivery.md")
    return {
        "repository": manifest["repository"],
        "target": "local-release",
        "stable_conventions": ["Use the repository release notes."],
        "sources": [source_item],
        "discovery": manifest,
        "observations": [
            {
                "target": "local-release",
                "observed_at": observed_at.isoformat(),
                "retrieved_at": observed_at.isoformat(),
                "valid_until": (observed_at + timedelta(days=2)).isoformat(),
                "result": "delivery completed",
            }
        ],
    }


def test_delivery_discovery_detects_material_membership_and_content_changes(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_delivery")
    project = tmp_path / "project"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    record = _record(module, project, observed_at=now - timedelta(hours=1))

    warm = module.assess_delivery(record, project_root=project, now=now)
    (project / "docs/deploy.yaml").write_text("target: production\n", encoding="utf-8")
    added = module.assess_delivery(record, project_root=project, now=now)
    (project / "docs/deploy.yaml").unlink()
    (project / "docs/delivery.md").write_text("Use a changed delivery route.", encoding="utf-8")
    edited = module.assess_delivery(record, project_root=project, now=now)

    assert warm["status"] == "hit"
    assert added["status"] == "miss" and added["discovery_gap"]
    assert edited["status"] == "miss" and edited["diagnostics"]


def test_delivery_expiry_and_failed_refresh_do_not_renew_observations(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_delivery")
    project = tmp_path / "project"
    observed = datetime(2026, 9, 27, tzinfo=timezone.utc)
    record = _record(module, project, observed_at=observed)

    expired = module.assess_delivery(record, project_root=project, now=observed + timedelta(hours=25))
    failed = module.refresh_delivery(record, evidence=None, project_root=project, now=observed + timedelta(days=3))

    assert expired["status"] == "miss"
    assert "delivery completed" not in expired["current_observations"]
    assert failed["record"] == record
    assert failed["refreshed"] is False


def test_future_observation_and_current_contradiction_are_misses(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_delivery")
    project = tmp_path / "project"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    record = _record(module, project, observed_at=now + timedelta(hours=1))
    future = module.assess_delivery(record, project_root=project, now=now)
    past = _record(module, project, observed_at=now - timedelta(hours=1))
    contradiction = module.assess_delivery(
        past, project_root=project, now=now, contradictions=["delivery target no longer exists"]
    )

    assert future["status"] == "miss"
    assert contradiction["status"] == "miss"


def test_unrelated_content_edit_preserves_reusable_delivery_facts(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_delivery")
    project = tmp_path / "project"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    source = project / "src/unrelated.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    record = _record(module, project, observed_at=now - timedelta(hours=1))
    baseline = module.assess_delivery(record, project_root=project, now=now)
    source.write_text("value = 2\n", encoding="utf-8")

    after = module.assess_delivery(record, project_root=project, now=now)

    assert baseline["status"] == "hit"
    assert after["status"] == "hit"


def test_linked_worktree_shares_delivery_identity_but_material_divergence_isolated(
    tmp_path: Path,
) -> None:
    module = load_kickoff_module("kickoff_delivery")
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    clone = tmp_path / "clone"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.name", "Test"], check=True)
    (main / "docs").mkdir()
    (main / "docs/delivery.md").write_text("Use the repository release notes.", encoding="utf-8")
    subprocess.run(["git", "-C", str(main), "add", "docs/delivery.md"], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "initial"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach", "-q", str(linked), "HEAD"], check=True)
    observed = datetime(2026, 9, 29, tzinfo=timezone.utc)
    record = _record(module, main, observed_at=observed - timedelta(hours=1))

    shared = module.assess_delivery(record, project_root=linked, now=observed)
    (linked / "docs/deploy.yaml").write_text("target: linked-only\n", encoding="utf-8")
    divergent = module.assess_delivery(record, project_root=linked, now=observed)
    stable = module.assess_delivery(record, project_root=main, now=observed)
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    (clone / "docs").mkdir()
    (clone / "docs/delivery.md").write_text("Release notes are authoritative.", encoding="utf-8")
    separate = module.assess_delivery(record, project_root=clone, now=observed)

    assert shared["status"] == "hit"
    assert divergent["status"] == "miss"
    assert stable["status"] == "hit"
    assert separate["status"] == "miss"


@pytest.mark.parametrize('mutation', [
    {'sources': None}, {'sources': []}, {'sources': [None]},
    {'stable_conventions': None}, {'stable_conventions': []}, {'target': None},
    {'discovery': {'inventory': None}}, {'observations': 'invalid'},
])
def test_corrupt_persisted_delivery_never_exposes_reusable_payload(tmp_path, mutation):
    """U05/U10/I05: persisted records must satisfy the refresh contract too."""
    module = load_kickoff_module('kickoff_delivery')
    now = datetime.now(timezone.utc)
    record = _record(module, tmp_path, observed_at=now)
    record.update(mutation)
    result = module.assess_delivery(record, project_root=tmp_path, now=now)
    assert result['status'] == 'miss'
    assert result['diagnostics']
    assert not result['delivery_template'] and not result['current_observations']


def test_explicit_invalid_delivery_expiry_is_not_an_absent_expiry(tmp_path):
    """U05/U10: malformed explicit expiry rejects refresh and persisted reuse."""
    module = load_kickoff_module('kickoff_delivery')
    now = datetime.now(timezone.utc)
    record = _record(module, tmp_path, observed_at=now)
    record['observations'][0]['valid_until'] = 'not-a-date'
    assert not module.refresh_delivery({}, evidence=record, project_root=tmp_path, now=now)['refreshed']
    result = module.assess_delivery(record, project_root=tmp_path, now=now)
    assert result['status'] == 'miss' and not result['current_observations']

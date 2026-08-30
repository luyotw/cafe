"""Pinned upstream and safe update behavior for the portable review fallback."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from cafe.review import fallback as review_fallback_module
from cafe.review.fallback import ReviewFallbackUpdateError, ReviewFallbackUpdater

REQUIRED_SOURCE = b"""---
name: code-reviewer
---
## Review Scope
scope
## Core Review Responsibilities
responsibilities
## Issue Confidence Scoring
**Only report issues with confidence >= 80**
Only report issues with confidence \xe2\x89\xa5 80
## Output Format
format
"""


def _write_skill(tmp_path: Path, *, content: bytes = REQUIRED_SOURCE) -> Path:
    skill_dir = tmp_path / "cafe-review-fallback"
    procedure = skill_dir / "references/review_procedure.md"
    license_file = skill_dir / "references/LICENSE.md"
    procedure.parent.mkdir(parents=True)
    license_file.write_text("Apache License 2.0\n", encoding="utf-8")
    normalized = ReviewFallbackUpdater._normalize_upstream_content(content)
    procedure.write_bytes(normalized)
    manifest = {
        "schema_version": 2,
        "source_repository": "anthropics/claude-plugins-official",
        "source_path": "plugins/pr-review-toolkit/agents/code-reviewer.md",
        "pinned_revision": "a" * 40,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "procedure_path": "references/review_procedure.md",
        "procedure_sha256": hashlib.sha256(normalized).hexdigest(),
        "license": "Apache-2.0",
        "license_path": "references/LICENSE.md",
    }
    assets = skill_dir / "assets"
    assets.mkdir()
    (assets / "update.lock").write_text("test lock\n", encoding="utf-8")
    (assets / "upstream.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return skill_dir


def _fetcher(
    target: bytes,
    *,
    revision: str = "b" * 40,
    current: bytes = REQUIRED_SOURCE,
):
    def fetch(url: str) -> bytes:
        if "api.github.com" in url:
            return json.dumps({"sha": revision}).encode("utf-8")
        if f"/{'a' * 40}/" in url:
            return current
        return target

    return fetch


def test_bundled_fallback_procedure_matches_pin_and_license() -> None:
    project_root = Path(__file__).resolve().parents[2]
    skill_dir = project_root / "src/cafe/data/skills/cafe-review-fallback"
    manifest = json.loads((skill_dir / "assets/upstream.json").read_text(encoding="utf-8"))
    procedure = (skill_dir / manifest["procedure_path"]).read_bytes()

    assert manifest["license"] == "Apache-2.0"
    assert len(manifest["pinned_revision"]) == 40
    assert len(manifest["source_sha256"]) == 64
    assert hashlib.sha256(procedure).hexdigest() == manifest["procedure_sha256"]
    assert (skill_dir / manifest["license_path"]).is_file()
    assert b"Only report issues with confidence \xe2\x89\xa5 80" in procedure
    assert b"model: opus" not in procedure
    assert b"CLAUDE.md" not in procedure


def test_update_check_is_read_only_and_returns_bounded_diff(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    target = REQUIRED_SOURCE.replace(b"scope\n", b"complete scope\n")
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(target))

    plan = updater.check()

    assert plan.changed
    assert plan.current_revision == "a" * 40
    assert plan.target_revision == "b" * 40
    assert "Source delta:" in plan.diff
    assert "Portable procedure delta:" in plan.diff
    assert "complete scope" in plan.diff
    expected = ReviewFallbackUpdater._normalize_upstream_content(REQUIRED_SOURCE)
    assert (skill_dir / "references/review_procedure.md").read_bytes() == expected


def test_apply_revalidates_drift_and_updates_procedure_and_pin(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    target = REQUIRED_SOURCE.replace(b"scope\n", b"complete scope\n")
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(target))
    plan = updater.check()

    updater.apply(plan)

    manifest = json.loads((skill_dir / "assets/upstream.json").read_text(encoding="utf-8"))
    procedure = ReviewFallbackUpdater._normalize_upstream_content(target)
    assert (skill_dir / "references/review_procedure.md").read_bytes() == procedure
    assert manifest["pinned_revision"] == "b" * 40
    assert manifest["source_sha256"] == hashlib.sha256(target).hexdigest()
    assert manifest["procedure_sha256"] == hashlib.sha256(procedure).hexdigest()


def test_update_rejects_local_procedure_drift(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    (skill_dir / "references/review_procedure.md").write_text(
        "locally modified\n",
        encoding="utf-8",
    )
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(REQUIRED_SOURCE))

    with pytest.raises(ReviewFallbackUpdateError, match="local drift"):
        updater.check()

    with pytest.raises(ReviewFallbackUpdateError, match="local drift"):
        updater.verify_local()


def test_update_rejects_incompatible_upstream_contract(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(b"new unrelated prompt\n"))

    with pytest.raises(ReviewFallbackUpdateError, match="manual adapter review"):
        updater.check()


def test_update_rejects_unverifiable_current_pin(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    changed_current = REQUIRED_SOURCE.replace(b"scope\n", b"drifted scope\n")
    updater = ReviewFallbackUpdater(
        skill_dir,
        fetcher=_fetcher(REQUIRED_SOURCE, current=changed_current),
    )

    with pytest.raises(ReviewFallbackUpdateError, match="source_sha256"):
        updater.check()


def test_source_only_update_reports_raw_delta_without_procedure_delta(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    target = REQUIRED_SOURCE.replace(b"name: code-reviewer", b"name: portable-reviewer")
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(target))

    plan = updater.check()

    assert plan.changed
    assert "Source delta:" in plan.diff
    assert "name: portable-reviewer" in plan.diff
    assert "Portable procedure delta:" not in plan.diff
    assert plan.current_procedure_sha256 == plan.target_procedure_sha256


def test_update_rejects_non_github_repository_manifest(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    manifest_path = skill_dir / "assets/upstream.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_repository"] = "https://internal.invalid/reviews"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(REQUIRED_SOURCE))

    with pytest.raises(ReviewFallbackUpdateError, match="owner/repository"):
        updater.check()


def test_concurrent_apply_serializes_and_rejects_stale_plan(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path)
    target_one = REQUIRED_SOURCE.replace(b"scope\n", b"first scope\n")
    target_two = REQUIRED_SOURCE.replace(b"scope\n", b"second scope\n")
    first = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(target_one, revision="b" * 40))
    second = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(target_two, revision="c" * 40))
    first_plan = first.check()
    second_plan = second.check()
    procedure_written = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    errors: list[Exception] = []
    original_atomic_write = first._atomic_write

    def delayed_atomic_write(path: Path, content: bytes) -> None:
        original_atomic_write(path, content)
        if path == first_plan.procedure_path and content == first_plan.target_procedure:
            procedure_written.set()
            assert release_first.wait(timeout=2)

    first._atomic_write = delayed_atomic_write  # type: ignore[method-assign]

    def apply_first() -> None:
        first.apply(first_plan)

    def apply_second() -> None:
        try:
            second.apply(second_plan)
        except Exception as exc:
            errors.append(exc)
        finally:
            second_finished.set()

    first_thread = threading.Thread(target=apply_first)
    second_thread = threading.Thread(target=apply_second)
    first_thread.start()
    assert procedure_written.wait(timeout=2)
    second_thread.start()
    assert not second_finished.wait(timeout=0.1)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(errors) == 1
    assert "manifest changed" in str(errors[0])
    first.verify_local()


def test_apply_uses_windows_process_lock_when_fcntl_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    skill_dir = _write_skill(tmp_path)
    target = REQUIRED_SOURCE.replace(b"scope\n", b"windows scope\n")
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(target))
    plan = updater.check()

    class WindowsLock:
        LK_LOCK = 1
        LK_UNLCK = 2

        def __init__(self) -> None:
            self.calls: list[int] = []

        def locking(self, descriptor: int, operation: int, length: int) -> None:
            assert descriptor >= 0
            assert length == 1
            self.calls.append(operation)

    windows_lock = WindowsLock()
    monkeypatch.setattr(review_fallback_module, "fcntl", None)
    monkeypatch.setattr(review_fallback_module, "msvcrt", windows_lock)

    updater.apply(plan)

    assert windows_lock.calls == [windows_lock.LK_LOCK, windows_lock.LK_UNLCK]
    updater.verify_local()


def test_apply_fails_closed_when_process_locking_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    skill_dir = _write_skill(tmp_path)
    target = REQUIRED_SOURCE.replace(b"scope\n", b"new scope\n")
    updater = ReviewFallbackUpdater(skill_dir, fetcher=_fetcher(target))
    plan = updater.check()
    monkeypatch.setattr(review_fallback_module, "fcntl", None)
    monkeypatch.setattr(review_fallback_module, "msvcrt", None)

    with pytest.raises(ReviewFallbackUpdateError, match="locking is unavailable"):
        updater.apply(plan)

    updater.verify_local()

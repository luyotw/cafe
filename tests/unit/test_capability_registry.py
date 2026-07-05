"""Tests for file-backed capability registry loading."""

from pathlib import Path

import pytest
import yaml

from cafe.core.capabilities import (
    CAPABILITY_PR_PUBLISH_ID,
    CapabilityRegistryError,
    load_capability_registry,
    run_capability_request,
    run_pr_publish_capability,
)


def test_load_registry_duplicate_ids(tmp_path: Path) -> None:
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    one = {
        "id": "dup",
        "script_ref": "sync_pr",
        "args_schema": {"required": []},
        "expected_outputs": {"required": ["pr_url", "pr_number"]},
    }
    two = dict(one)
    (cap_dir / "a.yaml").write_text(yaml.safe_dump(one), encoding="utf-8")
    (cap_dir / "b.yaml").write_text(yaml.safe_dump(two), encoding="utf-8")
    with pytest.raises(CapabilityRegistryError) as exc:
        load_capability_registry([cap_dir])
    assert "dup" in str(exc.value).lower()


def test_load_registry_invalid_yaml(tmp_path: Path) -> None:
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    (cap_dir / "bad.yaml").write_text("{not: valid: yaml", encoding="utf-8")
    with pytest.raises(CapabilityRegistryError):
        load_capability_registry([cap_dir])


def test_run_pr_publish_unknown_capability(tmp_path: Path) -> None:
    reg = {"other": {"id": "other"}}
    req = {"capability": "missing", "args": {"output": "x.md"}}
    run = run_pr_publish_capability(
        repo_root=tmp_path,
        registry=reg,
        publish_request=req,
        pr_markdown_file=tmp_path / "x.md",
    )
    assert run.receipt["success"] is False
    assert run.receipt["category"] == "validation_error"


def test_run_generic_capability_request_rejects_unknown_without_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cafe.core.capabilities as cap_mod

    called = False

    def _fake_run(*_a: object, **_k: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("unknown capability must not run subprocess")

    monkeypatch.setattr(cap_mod.subprocess, "run", _fake_run)

    run = run_capability_request(
        repo_root=tmp_path,
        registry={},
        capability_request={"capability": "demo.unknown", "args": {"output": "x.md"}},
        output_file=tmp_path / "x.md",
    )

    assert called is False
    assert run.receipt["success"] is False
    assert run.receipt["category"] == "validation_error"
    assert run.receipt["code"] == "unknown_capability"


def test_run_generic_capability_request_rejects_unknown_with_malformed_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cafe.core.capabilities as cap_mod

    called = False

    def _fake_run(*_a: object, **_k: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("unknown capability must not run subprocess")

    monkeypatch.setattr(cap_mod.subprocess, "run", _fake_run)

    run = run_capability_request(
        repo_root=tmp_path,
        registry={},
        capability_request={"capability": "demo.unknown", "args": "bad"},
        output_file=tmp_path / "x.md",
    )

    assert called is False
    assert run.receipt["success"] is False
    assert run.receipt["category"] == "validation_error"
    assert run.receipt["code"] == "unknown_capability"
    assert run.receipt["inputs"] == {}


def test_run_generic_capability_request_rejects_unsupported_without_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cafe.core.capabilities as cap_mod

    called = False

    def _fake_run(*_a: object, **_k: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("unsupported capability must not run subprocess")

    monkeypatch.setattr(cap_mod.subprocess, "run", _fake_run)

    run = run_capability_request(
        repo_root=tmp_path,
        registry={"demo.unsupported": {"id": "demo.unsupported", "script_ref": "sync_pr"}},
        capability_request={"capability": "demo.unsupported", "args": {}},
        output_file=tmp_path / "output.md",
    )

    assert called is False
    assert run.receipt["success"] is False
    assert run.receipt["category"] == "validation_error"
    assert run.receipt["code"] == "unsupported_capability"


def test_run_pr_publish_validation_missing_output(tmp_path: Path) -> None:
    reg = {
        CAPABILITY_PR_PUBLISH_ID: {
            "id": CAPABILITY_PR_PUBLISH_ID,
            "script_ref": "sync_pr",
            "args_schema": {"required": ["output"]},
            "expected_outputs": {"required": ["pr_url", "pr_number"]},
        }
    }
    out = tmp_path / "pr.md"
    out.write_text("# t\n", encoding="utf-8")
    run = run_pr_publish_capability(
        repo_root=tmp_path,
        registry=reg,
        publish_request={"capability": CAPABILITY_PR_PUBLISH_ID, "args": {}},
        pr_markdown_file=out,
    )
    assert run.receipt["success"] is False
    assert run.pr_synced_event is None


def test_blackboard_capability_receipts_roundtrip(tmp_path: Path) -> None:
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / "issue"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    store.append_capability_receipt(
        state,
        {"capability": "cafe.pr.publish", "success": True, "correlation_id": "abc"},
    )
    loaded = store.load_or_create("pr")
    assert len(loaded.capability_receipts) == 1
    assert loaded.capability_receipts[0]["correlation_id"] == "abc"


def test_run_pr_publish_success_mocked_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.capabilities as cap_mod

    out = tmp_path / "pr.md"
    out.write_text("# Title\n", encoding="utf-8")
    rel = str(out.relative_to(tmp_path))

    def _fake_run(*_a: object, **_k: object) -> object:
        class _R:
            returncode = 0
            stdout = '{"pr_url":"https://example/pr/1","pr_number":"1","action":"synced"}\n'
            stderr = ""

        return _R()

    monkeypatch.setattr(cap_mod.subprocess, "run", _fake_run)

    reg = {
        CAPABILITY_PR_PUBLISH_ID: {
            "id": CAPABILITY_PR_PUBLISH_ID,
            "script_ref": "sync_pr",
            "args_schema": {"required": ["output"]},
            "expected_outputs": {"required": ["pr_url", "pr_number"]},
        }
    }
    run = run_pr_publish_capability(
        repo_root=tmp_path,
        registry=reg,
        publish_request={"capability": CAPABILITY_PR_PUBLISH_ID, "args": {"output": rel}},
        pr_markdown_file=out,
    )
    assert run.receipt["success"] is True
    assert run.pr_synced_event is not None
    assert run.pr_synced_event["url"] == "https://example/pr/1"

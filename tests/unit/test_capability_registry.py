"""Tests for file-backed capability registry loading."""

from pathlib import Path

import pytest
import yaml

from cafe.core.capabilities import (
    CAPABILITY_BROWSER_OPEN_ID,
    CAPABILITY_PR_PUBLISH_ID,
    CapabilityManifest,
    CapabilityRegistryError,
    ExecutionRequest,
    PolicyDecision,
    canonical_request_fingerprint,
    evaluate_capability_request,
    load_capability_registry,
    run_capability_request,
    run_pr_publish_capability,
)


def _manifest(capability_id: str = "demo.echo", **overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "id": capability_id,
        "version": 1,
        "implementation": (
            "sync_pr" if capability_id == CAPABILITY_PR_PUBLISH_ID else "open_current_pr"
        ),
        "arguments": {
            "required": ["target_ref"],
            "properties": {"target_ref": {"type": "string", "enum": ["current_pr"]}},
        },
        "outputs": {"required": [], "properties": {}},
        "effects": {
            "writes": [],
            "network_destinations": [],
            "browser_open": ["current_pr"],
        },
        "credentials": [],
        "permissions": {},
        "idempotency": "safe",
        "risk": "low",
        "approval": "not_required",
        "policy": "allow",
    }
    manifest.update(overrides)
    return manifest


def test_load_registry_returns_typed_complete_manifests(tmp_path: Path) -> None:
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    (cap_dir / "demo.yaml").write_text(yaml.safe_dump(_manifest()), encoding="utf-8")

    registry = load_capability_registry([cap_dir])

    assert isinstance(registry["demo.echo"], CapabilityManifest)
    assert registry["demo.echo"].implementation == "open_current_pr"
    with pytest.raises(TypeError):
        registry["new"] = registry["demo.echo"]  # type: ignore[index]

    manifest = registry["demo.echo"]
    with pytest.raises(TypeError):
        manifest.arguments.properties["other"] = manifest.arguments.properties["target_ref"]  # type: ignore[index]
    with pytest.raises(TypeError):
        manifest.permissions["network"] = ("example.test",)  # type: ignore[index]


def test_load_registry_rejects_coerced_manifest_scalars(tmp_path: Path) -> None:
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    malformed = _manifest(version=True)
    (cap_dir / "demo.yaml").write_text(yaml.safe_dump(malformed), encoding="utf-8")

    with pytest.raises(CapabilityRegistryError):
        load_capability_registry([cap_dir])


@pytest.mark.parametrize(
    "field",
    [
        "version",
        "implementation",
        "arguments",
        "outputs",
        "effects",
        "credentials",
        "permissions",
        "idempotency",
        "risk",
        "approval",
        "policy",
    ],
)
def test_load_registry_rejects_incomplete_manifest(tmp_path: Path, field: str) -> None:
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    data = _manifest()
    data.pop(field)
    (cap_dir / "demo.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(CapabilityRegistryError):
        load_capability_registry([cap_dir])


def test_load_registry_rejects_extra_and_unsupported_implementation(tmp_path: Path) -> None:
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    malformed = _manifest(implementation="repo_script", executable="./run.sh")
    (cap_dir / "demo.yaml").write_text(yaml.safe_dump(malformed), encoding="utf-8")

    with pytest.raises(CapabilityRegistryError):
        load_capability_registry([cap_dir])


def _typed_manifest(**overrides: object) -> CapabilityManifest:
    return CapabilityManifest.model_validate(_manifest(**overrides))


def _browser_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "capability": "demo.echo",
        "args": {"target_ref": "current_pr"},
        "effects": {
            "writes": [],
            "network_destinations": [],
            "browser_open": ["current_pr"],
        },
        "credentials": [],
        "permissions": {},
    }
    request.update(overrides)
    return request


def test_execution_request_is_strict_and_fingerprint_covers_security_boundary() -> None:
    first = ExecutionRequest.model_validate(_browser_request())
    reordered = ExecutionRequest.model_validate(
        {
            "permissions": {},
            "credentials": [],
            "effects": {
                "browser_open": ["current_pr"],
                "network_destinations": [],
                "writes": [],
            },
            "args": {"target_ref": "current_pr"},
            "capability": "demo.echo",
        }
    )
    assert canonical_request_fingerprint(first) == canonical_request_fingerprint(reordered)

    changed = ExecutionRequest.model_validate(
        _browser_request(effects={"writes": [], "network_destinations": [], "browser_open": []})
    )
    assert canonical_request_fingerprint(first) != canonical_request_fingerprint(changed)

    with pytest.raises(ValueError):
        ExecutionRequest.model_validate({**_browser_request(), "script": "./owned.sh"})


@pytest.mark.parametrize(
    ("request_update", "reason_code"),
    [
        ({"args": {"target_ref": "https://evil.test"}}, "argument_not_allowed"),
        (
            {
                "effects": {
                    "writes": ["../outside"],
                    "network_destinations": [],
                    "browser_open": ["current_pr"],
                }
            },
            "effect_not_allowed",
        ),
        ({"credentials": ["admin-token"]}, "credential_not_allowed"),
        ({"permissions": {"network": ["evil.test"]}}, "permission_not_allowed"),
    ],
)
def test_policy_denies_broadened_request(
    request_update: dict[str, object], reason_code: str
) -> None:
    evaluation = evaluate_capability_request(
        {"demo.echo": _typed_manifest()}, _browser_request(**request_update)
    )
    assert evaluation.decision == PolicyDecision.DENY
    assert evaluation.reason_code == reason_code


@pytest.mark.parametrize(
    "request_update",
    [
        {
            "effects": {
                "writes": [],
                "network_destinations": [],
                "browser_open": [],
            }
        },
        {"credentials": []},
        {"permissions": {}},
    ],
)
def test_policy_denies_requests_that_omit_fixed_adapter_authority(
    request_update: dict[str, object],
) -> None:
    manifest = _typed_manifest(
        credentials=("browser",),
        permissions={"browser": ("current_pr",)},
    )
    request = _browser_request(
        **{
            "credentials": ["browser"],
            "permissions": {"browser": ["current_pr"]},
            **request_update,
        }
    )

    evaluation = evaluate_capability_request({"demo.echo": manifest}, request)

    assert evaluation.decision == PolicyDecision.DENY


def test_policy_decision_is_total_for_allow_approval_and_deny() -> None:
    allowed = evaluate_capability_request({"demo.echo": _typed_manifest()}, _browser_request())
    approval = evaluate_capability_request(
        {"demo.echo": _typed_manifest(approval="required")}, _browser_request()
    )
    denied = evaluate_capability_request(
        {"demo.echo": _typed_manifest(policy="deny")}, _browser_request()
    )

    assert allowed.decision == PolicyDecision.ALLOW
    assert approval.decision == PolicyDecision.REQUIRE_APPROVAL
    assert denied.decision == PolicyDecision.DENY
    assert allowed.fingerprint
    assert allowed.allowed_effects == allowed.request.effects


def test_dispatch_gate_records_approval_without_calling_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.capabilities as cap_mod

    monkeypatch.setattr(
        cap_mod,
        "HOST_CAPABILITY_ADAPTERS",
        {"open_current_pr": lambda **_kwargs: (_ for _ in ()).throw(AssertionError())},
    )
    run = run_capability_request(
        repo_root=tmp_path,
        registry={"demo.echo": _typed_manifest(approval="required")},
        capability_request=_browser_request(),
        output_file=tmp_path / "output.md",
    )

    assert run.receipt["success"] is False
    assert run.receipt["decision"]["outcome"] == "require_approval"
    assert run.receipt["outcome"] == "approval_required"
    assert run.receipt["request_fingerprint"]
    assert run.receipt["requested_effects"]["browser_open"] == ["current_pr"]


def test_dispatch_gate_records_policy_denial_without_calling_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.capabilities as cap_mod

    monkeypatch.setattr(
        cap_mod,
        "HOST_CAPABILITY_ADAPTERS",
        {"open_current_pr": lambda **_kwargs: (_ for _ in ()).throw(AssertionError())},
    )
    run = run_capability_request(
        repo_root=tmp_path,
        registry={"demo.echo": _typed_manifest(policy="deny")},
        capability_request=_browser_request(),
        output_file=tmp_path / "output.md",
    )

    assert run.receipt["decision"]["outcome"] == "deny"
    assert run.receipt["outcome"] == "policy_denied"


def test_dispatch_success_requires_output_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.capabilities as cap_mod

    manifest = _typed_manifest(
        outputs={
            "required": ["opened"],
            "properties": {"opened": {"type": "boolean"}},
        }
    )
    monkeypatch.setattr(
        cap_mod,
        "HOST_CAPABILITY_ADAPTERS",
        {"open_current_pr": lambda **_kwargs: ({"opened": "yes"}, None)},
    )
    run = run_capability_request(
        repo_root=tmp_path,
        registry={"demo.echo": manifest},
        capability_request=_browser_request(),
        output_file=tmp_path / "output.md",
    )

    assert run.receipt["success"] is False
    assert run.receipt["outcome"] == "execution_failure"
    assert run.receipt["category"] == "output_contract_error"


def test_enriched_blackboard_receipt_remains_backward_readable(tmp_path: Path) -> None:
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / "issue"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    enriched = {
        "capability": "demo.echo",
        "success": False,
        "correlation_id": "new",
        "request_fingerprint": "abc",
        "manifest": {"id": "demo.echo", "version": 1},
        "decision": {"outcome": "deny", "reason_code": "policy_denied"},
        "outcome": "policy_denied",
    }
    store.append_capability_receipt(state, enriched)
    store.append_capability_receipt(
        state, {"capability": "legacy", "success": True, "correlation_id": "old"}
    )

    loaded = store.load_or_create("develop")
    assert loaded.capability_receipts[-2:] == [
        enriched,
        {"capability": "legacy", "success": True, "correlation_id": "old"},
    ]


def test_current_pr_browser_adapter_opens_only_resolved_repository_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.capabilities as cap_mod

    registry = load_capability_registry([cap_mod._package_capabilities_dir()])
    monkeypatch.setattr(cap_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        cap_mod.GitHubOps,
        "get_current_pr_url",
        lambda _self: "https://github.com/acme/widgets/pull/42",
    )
    opened: list[str] = []
    monkeypatch.setattr(cap_mod.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        cap_mod,
        "_current_repo_slug",
        lambda _repo_root: "acme/widgets",
    )

    run = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request={
            "capability": CAPABILITY_BROWSER_OPEN_ID,
            "args": {"target_ref": "current_pr"},
            "effects": {
                "browser_open": ["current_pr"],
                "writes": [],
                "network_destinations": [],
            },
            "credentials": [],
            "permissions": {},
        },
        output_file=tmp_path / "output.md",
    )

    assert run.receipt["success"] is True
    assert opened == ["https://github.com/acme/widgets/pull/42"]


@pytest.mark.parametrize(
    "resolved_url",
    [
        "http://github.com/acme/widgets/pull/42",
        "https://evil.test/acme/widgets/pull/42",
        "https://github.com/acme/other/pull/42",
        "https://github.com/acme/widgets/issues/42",
        "https://github.com/acme/widgets/pull/not-a-number",
    ],
)
def test_current_pr_browser_adapter_rejects_noncanonical_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resolved_url: str
) -> None:
    import cafe.core.capabilities as cap_mod

    registry = load_capability_registry([cap_mod._package_capabilities_dir()])
    monkeypatch.setattr(cap_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cap_mod.GitHubOps, "get_current_pr_url", lambda _self: resolved_url)
    monkeypatch.setattr(cap_mod, "_current_repo_slug", lambda _repo_root: "acme/widgets")
    opened: list[str] = []
    monkeypatch.setattr(cap_mod.webbrowser, "open", opened.append)

    run = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request=_browser_request(capability=CAPABILITY_BROWSER_OPEN_ID),
        output_file=tmp_path / "output.md",
    )

    assert run.receipt["success"] is False
    assert opened == []


def test_load_registry_duplicate_ids(tmp_path: Path) -> None:
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    one = _manifest("dup")
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


def test_run_pr_publish_skips_invalid_base_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.capabilities as cap_mod

    out = tmp_path / "pr.md"
    out.write_text("# Title\n", encoding="utf-8")
    rel = str(out.relative_to(tmp_path))
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(*cmd_args: object, **_kwargs: object) -> _Result:
        cmd = list(cmd_args[0])
        calls.append(cmd)
        if cmd and cmd[0] == "git":
            return _Result(returncode=1)
        if cmd and cmd[0] == "/bin/bash":
            return _Result(
                returncode=0,
                stdout='{"pr_url":"https://example/pr/1","pr_number":"1","action":"synced"}\n',
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(cap_mod.subprocess, "run", _fake_run)

    reg = {
        CAPABILITY_PR_PUBLISH_ID: {
            "id": CAPABILITY_PR_PUBLISH_ID,
            "script_ref": "sync_pr",
            "args_schema": {"required": ["output"]},
            "expected_outputs": {"required": ["pr_url", "pr_number"]},
        }
    }
    run = cap_mod.run_pr_publish_capability(
        repo_root=tmp_path,
        registry=reg,
        publish_request={
            "capability": CAPABILITY_PR_PUBLISH_ID,
            "args": {"output": rel, "base": "codex/alignment-policy-escalation"},
        },
        pr_markdown_file=out,
    )
    assert run.receipt["success"] is True

    bash_calls = [cmd for cmd in calls if cmd and cmd[0] == "/bin/bash"]
    assert bash_calls, "sync_pr command was not invoked"
    assert "--base" not in bash_calls[0]


def test_run_pr_publish_skips_local_only_base_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import cafe.core.capabilities as cap_mod

    out = tmp_path / "pr.md"
    out.write_text("# Title\n", encoding="utf-8")
    rel = str(out.relative_to(tmp_path))
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(*cmd_args: object, **_kwargs: object) -> _Result:
        cmd = list(cmd_args[0])
        calls.append(cmd)
        if cmd and cmd[0] == "git":
            ref = str(cmd[-1])
            return _Result(returncode=0 if ref.startswith("refs/heads/") else 1)
        if cmd and cmd[0] == "/bin/bash":
            return _Result(
                returncode=0,
                stdout='{"pr_url":"https://example/pr/1","pr_number":"1","action":"synced"}\n',
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(cap_mod.subprocess, "run", _fake_run)

    reg = {
        CAPABILITY_PR_PUBLISH_ID: {
            "id": CAPABILITY_PR_PUBLISH_ID,
            "script_ref": "sync_pr",
            "args_schema": {"required": ["output"]},
            "expected_outputs": {"required": ["pr_url", "pr_number"]},
        }
    }
    run = cap_mod.run_pr_publish_capability(
        repo_root=tmp_path,
        registry=reg,
        publish_request={
            "capability": CAPABILITY_PR_PUBLISH_ID,
            "args": {"output": rel, "base": "codex/alignment-policy-escalation"},
        },
        pr_markdown_file=out,
    )
    assert run.receipt["success"] is True

    bash_calls = [cmd for cmd in calls if cmd and cmd[0] == "/bin/bash"]
    assert bash_calls, "sync_pr command was not invoked"
    assert "--base" not in bash_calls[0]

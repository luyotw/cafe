"""Invariant coverage for the package-owned Slack HumanTask notification path."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from cafe.core.capabilities import (
    CAPABILITY_SLACK_HUMAN_TASK_ID,
    default_capability_definition_dirs,
    load_capability_registry,
    run_capability_request,
)
from cafe.core.human_task_notifications import (
    SlackNotificationError,
    build_human_task_message,
    load_slack_webhook_url,
    post_slack_notification,
)


VALID_WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/secret-value"


class _SlackResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"ok") -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> "_SlackResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._body


def _slack_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "capability": CAPABILITY_SLACK_HUMAN_TASK_ID,
        "args": {
            "repository": "openfunltd/cafe",
            "workflow_id": "workflow-one",
            "task_id": "task-one",
            "reason": "Review the implementation plan.",
        },
        "effects": {
            "writes": [],
            "network_destinations": ["hooks.slack.com"],
            "browser_open": [],
        },
        "credentials": ["slack_human_task_webhook"],
        "permissions": {"network": ["hooks.slack.com"]},
    }
    request.update(overrides)
    return request


def _set_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    import cafe.core.human_task_notifications as notification_mod

    monkeypatch.setattr(notification_mod, "_trusted_user_home", lambda: home)


def _write_credential(home: Path, value: str = VALID_WEBHOOK) -> Path:
    credential = home / ".slack-webhook"
    credential.write_text(value, encoding="utf-8")
    credential.chmod(0o600)
    return credential


def test_actionable_message_exposes_task_journey_without_credentials() -> None:
    """Unit Test 4: task identity and supported actions are stable message fields."""
    message = build_human_task_message(
        repository="openfunltd/cafe",
        workflow_id="workflow-one",
        task_id="task-one",
        reason="Review the implementation plan.",
    )

    assert message.repository == "openfunltd/cafe"
    assert message.workflow_id == "workflow-one"
    assert message.task_id == "task-one"
    assert message.reason == "Review the implementation plan."
    assert message.inspect_command == "cafe task inspect task-one"
    assert message.complete_command == "cafe task complete task-one"
    assert "secret-value" not in json.dumps(message.to_slack_payload())


def test_credential_resolver_reads_only_the_fixed_user_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit Test 5: project files and environment hints cannot replace the credential."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    _write_credential(home)
    (project / ".slack-webhook").write_text(
        "https://hooks.slack.com/services/PROJECT/REDIRECT/value", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("CAFE_SLACK_WEBHOOK", "https://hooks.slack.com/services/ENV/REDIRECT/value")
    _set_home(monkeypatch, home)

    assert load_slack_webhook_url() == VALID_WEBHOOK


def test_credential_resolver_ignores_home_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit Test 5: HOME cannot select a project-adjacent credential."""
    trusted_home = tmp_path / "trusted-home"
    injected_home = tmp_path / "project" / "home"
    trusted_home.mkdir()
    injected_home.mkdir(parents=True)
    _write_credential(trusted_home)
    _write_credential(
        injected_home,
        "https://hooks.slack.com/services/PROJECT/REDIRECT/value",
    )
    monkeypatch.setenv("HOME", str(injected_home))
    _set_home(monkeypatch, trusted_home)

    assert load_slack_webhook_url() == VALID_WEBHOOK


@pytest.mark.parametrize("unsafe_kind", ["mode", "owner", "symlink"])
def test_credential_resolver_rejects_unsafe_user_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unsafe_kind: str
) -> None:
    """Unit Test 5: the fixed credential must be private, regular, and unsymlinked."""
    home = tmp_path / "home"
    home.mkdir()
    credential = home / ".slack-webhook"
    if unsafe_kind in {"mode", "owner"}:
        _write_credential(home)
        if unsafe_kind == "mode":
            credential.chmod(0o644)
        else:
            import cafe.core.human_task_notifications as notification_mod

            metadata = credential.stat()
            monkeypatch.setattr(
                notification_mod.os,
                "fstat",
                lambda _descriptor: SimpleNamespace(
                    st_mode=metadata.st_mode,
                    st_uid=metadata.st_uid + 1,
                    st_nlink=metadata.st_nlink,
                ),
            )
    else:
        target = tmp_path / "project-credential"
        target.write_text(VALID_WEBHOOK, encoding="utf-8")
        target.chmod(0o600)
        credential.symlink_to(target)
    _set_home(monkeypatch, home)

    with pytest.raises(SlackNotificationError) as exc:
        load_slack_webhook_url()

    assert exc.value.code == "slack_credentials_unsafe"


@pytest.mark.parametrize(
    ("credential", "expected_code"),
    [
        (None, "slack_credentials_missing"),
        ("", "slack_credentials_empty"),
        ("http://hooks.slack.com/services/T/B/value", "slack_credentials_invalid"),
        ("https://evil.test/services/T/B/value", "slack_credentials_invalid"),
        ("https://hooks.slack.com/not-services/T/B/value", "slack_credentials_invalid"),
        ("https://hooks.slack.com/services/T/B/value?redirect=1", "slack_credentials_invalid"),
    ],
)
def test_credential_resolver_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    credential: str | None,
    expected_code: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    if credential is not None:
        _write_credential(home, credential)
    _set_home(monkeypatch, home)

    with pytest.raises(SlackNotificationError) as exc:
        load_slack_webhook_url()

    assert exc.value.code == expected_code
    assert VALID_WEBHOOK not in str(exc.value)


@pytest.mark.parametrize(
    ("response", "raised", "expected_code"),
    [
        (_SlackResponse(), None, None),
        (_SlackResponse(status=503), None, "slack_http_error"),
        (_SlackResponse(body=b"invalid_payload"), None, "slack_response_not_ok"),
        (None, URLError("network unavailable"), "slack_transport_error"),
    ],
)
def test_outbound_adapter_classifies_delivery_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    response: _SlackResponse | None,
    raised: Exception | None,
    expected_code: str | None,
) -> None:
    """Unit Test 6: the HTTPS boundary yields stable secret-free outcome codes."""
    import cafe.core.human_task_notifications as notification_mod

    requests = []

    def _open_slack_request(request, *, timeout: float):
        requests.append((request, timeout))
        if raised is not None:
            raise raised
        return response

    monkeypatch.setattr(notification_mod, "_open_slack_request", _open_slack_request)
    message = build_human_task_message(
        repository="openfunltd/cafe",
        workflow_id="workflow-one",
        task_id="task-one",
        reason="Review the implementation plan.",
    )

    if expected_code is None:
        post_slack_notification(VALID_WEBHOOK, message, timeout_sec=4.0)
        payload = json.loads(requests[0][0].data)
        assert requests[0][0].full_url == VALID_WEBHOOK
        assert requests[0][1] == 4.0
        assert "task-one" in payload["text"]
        return

    with pytest.raises(SlackNotificationError) as exc:
        post_slack_notification(VALID_WEBHOOK, message, timeout_sec=4.0)

    assert exc.value.code == expected_code
    assert "secret-value" not in str(exc.value)


def test_outbound_adapter_installs_a_redirect_rejecting_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit Test 5: the fixed Slack destination remains fixed after HTTP responses."""
    import cafe.core.human_task_notifications as notification_mod

    handlers = []

    class _Opener:
        def open(self, _request, *, timeout: float):
            assert timeout == 4.0
            return _SlackResponse()

    def _build_opener(*items):
        handlers.extend(items)
        return _Opener()

    monkeypatch.setattr(notification_mod, "build_opener", _build_opener)
    message = build_human_task_message(
        repository="openfunltd/cafe",
        workflow_id="workflow-one",
        task_id="task-one",
        reason="Review the implementation plan.",
    )

    post_slack_notification(VALID_WEBHOOK, message, timeout_sec=4.0)

    assert len(handlers) == 1
    assert handlers[0].redirect_request(None, None, 302, "Found", {}, "https://evil.test") is None


@pytest.mark.parametrize(
    ("credential", "response", "raised", "expected_code"),
    [
        (None, None, None, "slack_credentials_missing"),
        ("", None, None, "slack_credentials_empty"),
        ("https://evil.test/services/T/B/value", None, None, "slack_credentials_invalid"),
        (VALID_WEBHOOK, _SlackResponse(status=500), None, "slack_http_error"),
        (VALID_WEBHOOK, _SlackResponse(body=b"no"), None, "slack_response_not_ok"),
        (VALID_WEBHOOK, None, URLError("offline"), "slack_transport_error"),
    ],
)
def test_capability_receipts_classify_failures_without_secret_material(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    credential: str | None,
    response: _SlackResponse | None,
    raised: Exception | None,
    expected_code: str,
) -> None:
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    home.mkdir()
    if credential is not None:
        _write_credential(home, credential)
    _set_home(monkeypatch, home)

    def _open_slack_request(_request, *, timeout: float):
        del timeout
        if raised is not None:
            raise raised
        return response

    monkeypatch.setattr(notification_mod, "_open_slack_request", _open_slack_request)
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))

    run = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request=_slack_request(),
        output_file=tmp_path / "output.md",
        timeout_sec=4.0,
    )

    assert run.receipt["success"] is False
    assert run.receipt["code"] == expected_code
    assert run.receipt["outcome"] == "execution_failure"
    assert "secret-value" not in json.dumps(run.receipt)


def test_capability_receipt_records_success_and_policy_denial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda _request, *, timeout: _SlackResponse(),
    )
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))

    successful = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request=_slack_request(),
        output_file=tmp_path / "output.md",
    )
    denied = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request=_slack_request(
            effects={
                "writes": [],
                "network_destinations": ["evil.test"],
                "browser_open": [],
            }
        ),
        output_file=tmp_path / "output.md",
    )

    assert successful.receipt["success"] is True
    assert successful.receipt["outputs"] == {
        "delivered": True,
        "workflow_id": "workflow-one",
        "task_id": "task-one",
    }
    assert denied.receipt["success"] is False
    assert denied.receipt["code"] == "effect_not_allowed"
    assert denied.receipt["outcome"] == "policy_denied"
    assert "secret-value" not in json.dumps([successful.receipt, denied.receipt])

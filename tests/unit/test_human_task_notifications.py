"""Invariant coverage for the package-owned Slack HumanTask notification path."""

from __future__ import annotations

import json
from pathlib import Path
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
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))


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
    (home / ".slack-webhook").write_text(VALID_WEBHOOK, encoding="utf-8")
    (project / ".slack-webhook").write_text(
        "https://hooks.slack.com/services/PROJECT/REDIRECT/value", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("CAFE_SLACK_WEBHOOK", "https://hooks.slack.com/services/ENV/REDIRECT/value")
    _set_home(monkeypatch, home)

    assert load_slack_webhook_url() == VALID_WEBHOOK


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
        (home / ".slack-webhook").write_text(credential, encoding="utf-8")
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

    def _urlopen(request, *, timeout: float):
        requests.append((request, timeout))
        if raised is not None:
            raise raised
        return response

    monkeypatch.setattr(notification_mod, "urlopen", _urlopen)
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
        (home / ".slack-webhook").write_text(credential, encoding="utf-8")
    _set_home(monkeypatch, home)

    def _urlopen(_request, *, timeout: float):
        del timeout
        if raised is not None:
            raise raised
        return response

    monkeypatch.setattr(notification_mod, "urlopen", _urlopen)
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
    (home / ".slack-webhook").write_text(VALID_WEBHOOK, encoding="utf-8")
    _set_home(monkeypatch, home)
    monkeypatch.setattr(notification_mod, "urlopen", lambda _request, *, timeout: _SlackResponse())
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

"""Invariant coverage for the package-owned Slack HumanTask notification path."""

from __future__ import annotations

import json
import os
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
            "step": "develop",
            "task_type": "permission-answers",
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


def _write_test_run_credential(home: Path, value: str = VALID_WEBHOOK) -> Path:
    credential = home / ".cafe" / "test-slack-webhook"
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text(value, encoding="utf-8")
    credential.chmod(0o600)
    return credential


def _write_machine_config(home: Path, contents: str) -> Path:
    config = home / ".cafe" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(contents, encoding="utf-8")
    config.chmod(0o600)
    return config


def test_actionable_message_exposes_allowlisted_task_journey_without_prompt_or_credentials() -> (
    None
):
    """Test List 2: notification content stays within the safe task allowlist."""
    message = build_human_task_message(
        repository="openfunltd/cafe",
        workflow_id="workflow-one",
        task_id="task-one",
        step="develop",
        task_type="permission-answers",
    )

    assert message.repository == "openfunltd/cafe"
    assert message.workflow_id == "workflow-one"
    assert message.task_id == "task-one"
    assert message.step == "develop"
    assert message.task_type == "permission-answers"
    assert message.inspect_command == "cafe task inspect task-one"
    assert message.complete_command == "cafe task complete task-one"
    payload = json.dumps(message.to_slack_payload())
    assert "Review the implementation plan." not in payload
    assert "secret-value" not in payload


def test_actionable_message_bounds_project_controlled_metadata_to_one_safe_line_per_field() -> None:
    """Test List 2: metadata cannot add Slack markup or notification lines."""
    message = build_human_task_message(
        repository="repository\n<!channel> *urgent*",
        workflow_id="workflow\n@here",
        task_id="task\n<https://attacker.invalid>",
        step="develop\n<!subteam^S123>",
        task_type="permission-answers\n@channel",
    )

    payload = message.to_slack_payload()["text"]

    assert payload.count("\n") == 7
    assert "<!channel>" not in payload
    assert "<!subteam" not in payload
    assert "@here" not in payload
    assert "@channel" not in payload
    assert "https://attacker.invalid" not in payload


@pytest.mark.parametrize(
    "project_value",
    [
        "https://attacker.invalid/path",
        "www.attacker.invalid",
        "namespace:www.attacker.invalid",
    ],
)
def test_actionable_message_rejects_url_shaped_project_metadata(project_value: str) -> None:
    """Project-owned identifiers cannot add clickable links to a trusted notification."""
    message = build_human_task_message(
        repository=project_value,
        workflow_id="workflow-one",
        task_id="task-one",
        step=project_value,
        task_type=project_value,
    )

    payload = message.to_slack_payload()["text"]

    assert project_value not in payload
    assert message.repository.startswith("invalid-")
    assert message.step.startswith("invalid-")
    assert message.task_type.startswith("invalid-")


def test_actionable_message_preserves_non_url_identifier_punctuation() -> None:
    """Safe project identifiers remain recognizable instead of becoming opaque hashes."""
    message = build_human_task_message(
        repository="openfunltd/cafe.engine",
        workflow_id="workflow:one",
        task_id="task-one",
        step="review.v2/approval",
        task_type="namespace:permission",
    )

    assert message.repository == "openfunltd/cafe.engine"
    assert message.workflow_id == "workflow:one"
    assert message.step == "review.v2/approval"
    assert message.task_type == "namespace:permission"


def test_machine_notification_settings_ignore_project_and_environment_injection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test List 1: trusted machine settings alone choose the notification transport."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".cafe").mkdir(parents=True)
    project.mkdir()
    (home / ".cafe" / "config.yaml").write_text(
        "notifications:\n  human_tasks:\n    enabled: false\n    transport: slack\n",
        encoding="utf-8",
    )
    (project / "config.yaml").write_text(
        "human_task_notifications:\n  enabled: true\n  transport: attacker\n",
        encoding="utf-8",
    )
    _set_home(monkeypatch, home)
    monkeypatch.chdir(project)
    monkeypatch.setenv("CAFE_HUMAN_TASK_TRANSPORT", "attacker")

    settings = notification_mod.load_human_task_notification_settings()

    assert settings.enabled is False
    assert settings.transport == "slack"


@pytest.mark.parametrize(
    ("config", "expected_code"),
    [
        (
            "notifications:\n  human_tasks:\n    enabled: sometimes\n    transport: slack\n",
            "human_task_notification_config_invalid",
        ),
        (
            "notifications:\n  human_tasks:\n    enabled: true\n    transport: email\n",
            "human_task_notification_transport_unsupported",
        ),
    ],
)
def test_machine_notification_settings_skip_invalid_or_unsupported_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str, expected_code: str
) -> None:
    """Test List 1: unusable machine declarations never select an outbound adapter."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    (home / ".cafe").mkdir(parents=True)
    (home / ".cafe" / "config.yaml").write_text(config, encoding="utf-8")
    _set_home(monkeypatch, home)

    settings = notification_mod.load_human_task_notification_settings()

    assert settings.enabled is False
    assert settings.outcome == "skipped"
    assert settings.code == expected_code


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


def test_credential_resolver_uses_private_machine_project_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A machine-owned absolute project route overrides only that repository."""
    home = tmp_path / "home"
    project = tmp_path / "open-forest-scripts"
    other_project = tmp_path / "other-project"
    home.mkdir()
    project.mkdir()
    other_project.mkdir()
    _write_credential(home)
    project_webhook = "https://hooks.slack.com/services/T00000000/B00000000/project-route"
    _write_machine_config(
        home,
        "\n".join(
            (
                "notifications:",
                "  human_tasks:",
                "    projects:",
                f"      {project}:",
                f"        webhook_url: {project_webhook}",
                "",
            )
        ),
    )
    _set_home(monkeypatch, home)

    assert load_slack_webhook_url(repository_root=project) == project_webhook
    assert load_slack_webhook_url(repository_root=other_project) == VALID_WEBHOOK


def test_project_route_requires_private_machine_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A direct webhook in a world-readable config fails closed before dispatch."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    project = tmp_path / "open-forest-scripts"
    home.mkdir()
    project.mkdir()
    config = home / ".cafe" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "\n".join(
            (
                "notifications:",
                "  human_tasks:",
                "    projects:",
                f"      {project}:",
                "        webhook_url: https://hooks.slack.com/services/T00000000/B00000000/project-route",
                "",
            )
        ),
        encoding="utf-8",
    )
    config.chmod(0o644)
    _set_home(monkeypatch, home)

    settings = notification_mod.load_human_task_notification_settings()
    assert settings.enabled is False
    assert settings.code == "human_task_notification_config_unsafe"

    with pytest.raises(SlackNotificationError) as exc:
        load_slack_webhook_url(repository_root=project)
    assert exc.value.code == "human_task_notification_config_unsafe"


def test_credential_resolver_uses_the_test_channel_only_for_the_coverage_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The test runner can select only its separately provisioned local credential."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    home.mkdir()
    _write_credential(home, "https://hooks.slack.com/services/T00000000/B00000000/production")
    test_webhook = "https://hooks.slack.com/services/T00000000/B00000000/test-channel"
    _write_test_run_credential(home, test_webhook)
    _set_home(monkeypatch, home)
    monkeypatch.setattr(notification_mod, "_login_user_home", lambda: home)
    monkeypatch.setenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", "1")

    assert load_slack_webhook_url() == test_webhook


def test_coverage_runner_does_not_use_a_project_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Coverage notifications stay on their isolated credential even with routes."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    project = tmp_path / "open-forest-scripts"
    home.mkdir()
    project.mkdir()
    _write_test_run_credential(home, "https://hooks.slack.com/services/T00000000/B00000000/test")
    _write_machine_config(
        home,
        "\n".join(
            (
                "notifications:",
                "  human_tasks:",
                "    projects:",
                f"      {project}:",
                "        webhook_url: https://hooks.slack.com/services/T00000000/B00000000/project-route",
                "",
            )
        ),
    )
    _set_home(monkeypatch, home)
    monkeypatch.setattr(notification_mod, "_login_user_home", lambda: home)
    monkeypatch.setenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", "1")

    assert (
        load_slack_webhook_url(repository_root=project)
        == "https://hooks.slack.com/services/T00000000/B00000000/test"
    )


def test_credential_resolver_fails_closed_when_the_coverage_runner_has_no_test_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A test run must never fall back to the HumanTask channel and cause noise."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    monkeypatch.setattr(notification_mod, "_login_user_home", lambda: home)
    monkeypatch.setenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", "1")

    with pytest.raises(SlackNotificationError) as exc:
        load_slack_webhook_url()

    assert exc.value.code == "slack_credentials_missing"


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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
def test_credential_resolver_rejects_fifo_without_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit Test 5: a non-regular credential cannot block the HumanTask handoff."""
    home = tmp_path / "home"
    home.mkdir()
    os.mkfifo(home / ".slack-webhook", mode=0o600)
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
        step="develop",
        task_type="permission-answers",
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
        step="develop",
        task_type="permission-answers",
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
        trusted_human_task_notification=True,
    )

    assert run.receipt["success"] is False
    assert run.receipt["code"] == expected_code
    assert run.receipt["outcome"] == "execution_failure"
    assert "secret-value" not in json.dumps(run.receipt)


def test_capability_uses_machine_project_route_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The trusted adapter resolves a route from its real repository root."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    project = tmp_path / "open-forest-scripts"
    home.mkdir()
    project.mkdir()
    project_webhook = "https://hooks.slack.com/services/T00000000/B00000000/project-route"
    _write_machine_config(
        home,
        "\n".join(
            (
                "notifications:",
                "  human_tasks:",
                "    projects:",
                f"      {project}:",
                f"        webhook_url: {project_webhook}",
                "",
            )
        ),
    )
    _set_home(monkeypatch, home)
    requests = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: requests.append((request, timeout)) or _SlackResponse(),
    )
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))

    run = run_capability_request(
        repo_root=project,
        registry=registry,
        capability_request=_slack_request(),
        output_file=tmp_path / "output.md",
        timeout_sec=4.0,
        trusted_human_task_notification=True,
    )

    assert run.receipt["success"] is True
    assert requests[0][0].full_url == project_webhook
    assert project_webhook not in json.dumps(run.receipt)


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
        trusted_human_task_notification=True,
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
        trusted_human_task_notification=True,
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

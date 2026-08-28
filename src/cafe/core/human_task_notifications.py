"""Trusted Slack delivery for newly materialized HumanTasks."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml

SLACK_WEBHOOK_FILENAME = ".slack-webhook"
SLACK_WEBHOOK_HOST = "hooks.slack.com"
MAX_CREDENTIAL_BYTES = 8192
MACHINE_CONFIG_DIRECTORY = ".cafe"
MACHINE_CONFIG_FILENAME = "config.yaml"


class SlackNotificationError(RuntimeError):
    """A stable, secret-free Slack delivery failure."""

    def __init__(self, category: str, code: str) -> None:
        super().__init__(code)
        self.category = category
        self.code = code


@dataclass(frozen=True)
class HumanTaskSlackMessage:
    """Actionable, non-secret fields for one pending HumanTask."""

    repository: str
    workflow_id: str
    task_id: str
    step: str
    task_type: str
    inspect_command: str
    complete_command: str

    def to_slack_payload(self) -> dict[str, str]:
        text = "\n".join(
            (
                "CAFE HumanTask requires action",
                f"Repository: {self.repository}",
                f"Workflow: {self.workflow_id}",
                f"Step: {self.step}",
                f"HumanTask: {self.task_id}",
                f"Task type: {self.task_type}",
                f"Inspect: {self.inspect_command}",
                f"Complete: {self.complete_command}",
            )
        )
        return {"text": text}


def build_human_task_message(
    *, repository: str, workflow_id: str, task_id: str, step: str, task_type: str
) -> HumanTaskSlackMessage:
    """Build the supported task inspection and completion journey."""
    return HumanTaskSlackMessage(
        repository=repository,
        workflow_id=workflow_id,
        task_id=task_id,
        step=step,
        task_type=task_type,
        inspect_command=f"cafe task inspect {task_id}",
        complete_command=f"cafe task complete {task_id}",
    )


@dataclass(frozen=True)
class HumanTaskNotificationSettings:
    """Machine-owned transport decision for one HumanTask notification."""

    enabled: bool
    transport: str
    outcome: Literal["enabled", "disabled", "skipped"]
    code: str


def load_human_task_notification_settings() -> HumanTaskNotificationSettings:
    """Resolve the machine-only notification setting without project input.

    The absence of a machine config preserves the established Slack delivery
    behavior. Operators can explicitly disable delivery in ``~/.cafe/config.yaml``;
    malformed or unsupported declarations are observable skipped outcomes.
    """
    config_path = _trusted_user_home() / MACHINE_CONFIG_DIRECTORY / MACHINE_CONFIG_FILENAME
    if not config_path.exists():
        return HumanTaskNotificationSettings(
            enabled=True,
            transport="slack",
            outcome="enabled",
            code="human_task_notification_enabled",
        )
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return HumanTaskNotificationSettings(
            enabled=False,
            transport="",
            outcome="skipped",
            code="human_task_notification_config_invalid",
        )
    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        return HumanTaskNotificationSettings(
            enabled=False,
            transport="",
            outcome="skipped",
            code="human_task_notification_config_invalid",
        )
    declaration = raw_config.get("human_task_notifications", {})
    if declaration is None:
        declaration = {}
    if not isinstance(declaration, dict):
        return HumanTaskNotificationSettings(
            enabled=False,
            transport="",
            outcome="skipped",
            code="human_task_notification_config_invalid",
        )
    enabled = declaration.get("enabled", True)
    transport = declaration.get("transport", "slack")
    if not isinstance(enabled, bool) or not isinstance(transport, str):
        return HumanTaskNotificationSettings(
            enabled=False,
            transport="",
            outcome="skipped",
            code="human_task_notification_config_invalid",
        )
    if not enabled:
        return HumanTaskNotificationSettings(
            enabled=False,
            transport=transport,
            outcome="disabled",
            code="human_task_notification_disabled",
        )
    if transport != "slack":
        return HumanTaskNotificationSettings(
            enabled=False,
            transport=transport,
            outcome="skipped",
            code="human_task_notification_transport_unsupported",
        )
    return HumanTaskNotificationSettings(
        enabled=True,
        transport=transport,
        outcome="enabled",
        code="human_task_notification_enabled",
    )


def _validate_slack_webhook_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise SlackNotificationError("validation_error", "slack_credentials_invalid") from exc
    path_parts = parsed.path.removeprefix("/").split("/")
    valid_tokens = (
        len(path_parts) == 4
        and path_parts[0] == "services"
        and all(re.fullmatch(r"[A-Za-z0-9_-]+", token) for token in path_parts[1:])
    )
    if not (
        parsed.scheme == "https"
        and parsed.hostname == SLACK_WEBHOOK_HOST
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and valid_tokens
    ):
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    return raw_url


def _trusted_user_home() -> Path:
    """Resolve the login account home without consulting the mutable HOME variable."""
    if os.name != "posix":  # pragma: no cover - Windows has no pwd database.
        return Path.home()
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def load_slack_webhook_url() -> str:
    """Read and validate only the fixed user-owned Slack credential file."""
    credential_file = _trusted_user_home() / SLACK_WEBHOOK_FILENAME
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(credential_file, flags)
    except FileNotFoundError as exc:
        raise SlackNotificationError("validation_error", "slack_credentials_missing") from exc
    except OSError as exc:
        if credential_file.is_symlink():
            raise SlackNotificationError("validation_error", "slack_credentials_unsafe") from exc
        raise SlackNotificationError("validation_error", "slack_credentials_unreadable") from exc
    try:
        metadata = os.fstat(descriptor)
        private_mode = stat.S_IMODE(metadata.st_mode) & 0o077 == 0
        owned_by_user = not hasattr(os, "getuid") or metadata.st_uid == os.getuid()
        if not (
            stat.S_ISREG(metadata.st_mode)
            and private_mode
            and owned_by_user
            and metadata.st_nlink == 1
        ):
            raise SlackNotificationError("validation_error", "slack_credentials_unsafe")
        with os.fdopen(descriptor, encoding="utf-8") as credential_stream:
            descriptor = -1
            webhook_url = credential_stream.read(MAX_CREDENTIAL_BYTES + 1).strip()
    except UnicodeError as exc:
        raise SlackNotificationError("validation_error", "slack_credentials_invalid") from exc
    except OSError as exc:
        raise SlackNotificationError("validation_error", "slack_credentials_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(webhook_url.encode("utf-8")) > MAX_CREDENTIAL_BYTES:
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    if not webhook_url:
        raise SlackNotificationError("validation_error", "slack_credentials_empty")
    return _validate_slack_webhook_url(webhook_url)


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Keep every request on the manifest-declared Slack destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _open_slack_request(request: Request, *, timeout: float):
    return build_opener(_RejectRedirectHandler()).open(request, timeout=timeout)


def post_slack_notification(
    webhook_url: str,
    message: HumanTaskSlackMessage,
    *,
    timeout_sec: float,
) -> None:
    """Submit one Slack Incoming Webhook request through the HTTPS boundary."""
    validated_url = _validate_slack_webhook_url(webhook_url)
    request = Request(
        validated_url,
        data=json.dumps(message.to_slack_payload()).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with _open_slack_request(
            request, timeout=timeout_sec
        ) as response:  # noqa: S310 - URL is fixed/validated.
            status = response.status
            body = response.read(64).decode("utf-8", errors="replace").strip()
    except HTTPError as exc:
        raise SlackNotificationError("script_exit_error", "slack_http_error") from exc
    except TimeoutError as exc:
        raise SlackNotificationError("timeout_error", "slack_timeout") from exc
    except (URLError, OSError) as exc:
        raise SlackNotificationError("script_exit_error", "slack_transport_error") from exc
    if status != 200:
        raise SlackNotificationError("script_exit_error", "slack_http_error")
    if body != "ok":
        raise SlackNotificationError("script_exit_error", "slack_response_not_ok")

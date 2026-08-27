"""Trusted Slack delivery for newly materialized HumanTasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SLACK_WEBHOOK_FILENAME = ".slack-webhook"
SLACK_WEBHOOK_HOST = "hooks.slack.com"


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
    reason: str
    inspect_command: str
    complete_command: str

    def to_slack_payload(self) -> dict[str, str]:
        text = "\n".join(
            (
                "CAFE HumanTask requires action",
                f"Repository: {self.repository}",
                f"Workflow: {self.workflow_id}",
                f"HumanTask: {self.task_id}",
                f"Reason: {self.reason}",
                f"Inspect: {self.inspect_command}",
                f"Complete: {self.complete_command}",
            )
        )
        return {"text": text}


def build_human_task_message(
    *, repository: str, workflow_id: str, task_id: str, reason: str
) -> HumanTaskSlackMessage:
    """Build the supported task inspection and completion journey."""
    return HumanTaskSlackMessage(
        repository=repository,
        workflow_id=workflow_id,
        task_id=task_id,
        reason=reason,
        inspect_command=f"cafe task inspect {task_id}",
        complete_command=f"cafe task complete {task_id}",
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


def load_slack_webhook_url() -> str:
    """Read and validate only the fixed user-owned Slack credential file."""
    credential_file = Path.home() / SLACK_WEBHOOK_FILENAME
    try:
        webhook_url = credential_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise SlackNotificationError("validation_error", "slack_credentials_missing") from exc
    except OSError as exc:
        raise SlackNotificationError("validation_error", "slack_credentials_unreadable") from exc
    if not webhook_url:
        raise SlackNotificationError("validation_error", "slack_credentials_empty")
    return _validate_slack_webhook_url(webhook_url)


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
        with urlopen(
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

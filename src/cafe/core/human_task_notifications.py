"""Trusted Slack delivery for newly materialized HumanTasks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml

SLACK_WEBHOOK_FILENAME = ".slack-webhook"
TEST_RUN_SLACK_WEBHOOK_FILENAME = ".cafe/test-slack-webhook"
TEST_RUN_SLACK_ROUTING_ENV = "CAFE_TEST_RUN_SLACK_NOTIFICATIONS"
SLACK_WEBHOOK_HOST = "hooks.slack.com"
MAX_CREDENTIAL_BYTES = 8192
MAX_MACHINE_CONFIG_BYTES = 65536
MAX_PROJECT_ROUTES = 128
MACHINE_CONFIG_DIRECTORY = ".cafe"
MACHINE_CONFIG_FILENAME = "config.yaml"
MAX_NOTIFICATION_METADATA_LENGTH = 128
SAFE_NOTIFICATION_METADATA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
HUMAN_TASK_STEP_LABELS = {
    "spec": "需求規格",
    "plan": "規劃",
    "develop": "開發",
    "review": "審查",
    "pr": "提交與合併",
}
HUMAN_TASK_ACTION_LABELS = {
    "clarification-feedback": "回覆釐清問題",
    "output-review": "確認結果",
    "permission-answers": "回覆權限相關問題",
    "alignment-decision": "確認方向",
    "no-changes-needed": "確認沒有需要變更",
    "agent-execution-interrupted": "決定如何繼續",
}


class SlackNotificationError(RuntimeError):
    """A stable, secret-free Slack delivery failure."""

    def __init__(self, category: str, code: str) -> None:
        super().__init__(code)
        self.category = category
        self.code = code


def _machine_config_path() -> Path:
    """Return the only machine-owned notification configuration path."""
    return _trusted_user_home() / MACHINE_CONFIG_DIRECTORY / MACHINE_CONFIG_FILENAME


def _load_machine_config() -> tuple[Path, dict[object, object]]:
    """Load the machine-only configuration or raise a stable safe error."""
    config_path = _machine_config_path()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(config_path, flags)
    except FileNotFoundError:
        return config_path, {}
    except OSError as exc:
        raise SlackNotificationError(
            "validation_error", "human_task_notification_config_invalid"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            )
        with os.fdopen(descriptor, "rb") as config_stream:
            descriptor = -1
            config_bytes = config_stream.read(MAX_MACHINE_CONFIG_BYTES + 1)
        if len(config_bytes) > MAX_MACHINE_CONFIG_BYTES:
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            )
        raw_config = yaml.safe_load(config_bytes.decode("utf-8"))
    except SlackNotificationError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SlackNotificationError(
            "validation_error", "human_task_notification_config_invalid"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if raw_config is None:
        return config_path, {}
    if not isinstance(raw_config, dict):
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    return config_path, raw_config


def _human_task_notification_declaration(raw_config: dict[object, object]) -> dict[object, object]:
    """Extract the human-task declaration without accepting project input."""
    notifications = raw_config.get("notifications", {})
    if notifications is None:
        notifications = {}
    if not isinstance(notifications, dict):
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    declaration = notifications.get("human_tasks", {})
    if declaration is None:
        declaration = {}
    if not isinstance(declaration, dict):
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    return declaration


def _normalise_project_root(repository_root: Path) -> str:
    """Return a stable absolute route key supplied only to trusted config lookup."""
    try:
        root = repository_root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SlackNotificationError(
            "validation_error", "human_task_notification_config_invalid"
        ) from exc
    if not root.is_absolute():
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    return str(root)


def _is_private_machine_config(config_path: Path) -> bool:
    """Direct URLs are credentials, so their config must be private and regular."""
    try:
        metadata = config_path.lstat()
    except OSError:
        return False
    private_mode = stat.S_IMODE(metadata.st_mode) & 0o077 == 0
    owned_by_user = not hasattr(os, "getuid") or metadata.st_uid == os.getuid()
    return (
        stat.S_ISREG(metadata.st_mode) and private_mode and owned_by_user and metadata.st_nlink == 1
    )


def _bounded_project_webhook_declarations(
    *, config_path: Path, declaration: dict[object, object]
) -> dict[object, object]:
    """Return the bounded private map without inspecting unrelated routes."""
    projects = declaration.get("projects", {})
    if projects is None:
        projects = {}
    if not isinstance(projects, dict) or len(projects) > MAX_PROJECT_ROUTES:
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    if projects and not _is_private_machine_config(config_path):
        raise SlackNotificationError("validation_error", "human_task_notification_config_unsafe")
    return projects


def _project_webhook_route(
    *, config_path: Path, declaration: dict[object, object], repository_root: Path
) -> str | None:
    """Validate only routes that resolve to the selected repository."""
    projects = _bounded_project_webhook_declarations(
        config_path=config_path,
        declaration=declaration,
    )
    selected_root = _normalise_project_root(repository_root)
    selected_urls: set[str] = set()
    for configured_root, configured_route in projects.items():
        if not isinstance(configured_root, str) or not configured_root:
            continue
        configured_path = Path(configured_root)
        if not configured_path.is_absolute():
            continue
        try:
            normalised_root = _normalise_project_root(configured_path)
        except SlackNotificationError:
            continue
        if normalised_root != selected_root:
            continue
        if not isinstance(configured_route, dict):
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            )
        if set(configured_route) != {"webhook_url"}:
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            )
        webhook_url = configured_route.get("webhook_url")
        if not isinstance(webhook_url, str):
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            )
        try:
            validated_url = _validate_slack_webhook_url(webhook_url)
        except SlackNotificationError as exc:
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            ) from exc
        selected_urls.add(validated_url)
    if len(selected_urls) > 1:
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    return next(iter(selected_urls), None)


@dataclass(frozen=True)
class HumanTaskSlackMessage:
    """Actionable, non-secret fields for one pending HumanTask."""

    repository: str
    issue: str
    workflow_id: str
    task_id: str
    step: str
    task_type: str

    def to_slack_payload(self) -> dict[str, str]:
        repository = _readable_metadata(self.repository, fallback="目前專案")
        issue = _readable_metadata(self.issue, fallback="未命名工作項目")
        step_label = HUMAN_TASK_STEP_LABELS.get(self.step, "工作流程")
        action_label = HUMAN_TASK_ACTION_LABELS.get(self.task_type, "處理 CAFE 工作項目")
        text = "\n".join(
            (
                "CAFE 需要你的處理",
                f"專案：{repository}",
                f"對話：{issue}",
                f"目前階段：{step_label}",
                f"需要你做的事：{action_label}",
                f"請回到 CAFE 的「{issue}」工作項目處理。",
            )
        )
        return {"text": text}


@dataclass(frozen=True)
class WorkflowCallbackFailureSlackMessage:
    """Readable, secret-free notification for an asynchronous callback failure."""

    repository: str
    issue: str
    step: str
    event_type: str
    error_code: str

    def to_slack_payload(self) -> dict[str, str]:
        repository = _readable_metadata(self.repository, fallback="目前專案")
        issue = _readable_metadata(self.issue, fallback="未命名工作項目")
        step = HUMAN_TASK_STEP_LABELS.get(self.step, self.step or "未知階段")
        event_type = _readable_metadata(self.event_type, fallback="未知事件")
        error_code = _readable_metadata(self.error_code, fallback="未知錯誤")
        text = "\n".join(
            (
                "CAFE event callback 執行失敗",
                f"專案：{repository}",
                f"對話：{issue}",
                f"目前階段：{step}",
                f"事件：{event_type}",
                f"錯誤：{error_code}",
                f"工作流程狀態已保存，請回到 CAFE 的「{issue}」工作項目查看。",
            )
        )
        return {"text": text}


class SlackPayloadMessage(Protocol):
    """Minimal message contract accepted by the trusted Slack transport."""

    def to_slack_payload(self) -> dict[str, str]: ...


def build_human_task_message(
    *,
    repository: str,
    workflow_id: str,
    task_id: str,
    step: str,
    task_type: str,
    issue: str = "",
) -> HumanTaskSlackMessage:
    """Build one readable, bounded HumanTask notification."""
    repository = sanitize_human_task_metadata(repository)
    issue = sanitize_human_task_metadata(issue)
    workflow_id = sanitize_human_task_metadata(workflow_id)
    task_id = sanitize_human_task_metadata(task_id)
    step = sanitize_human_task_metadata(step)
    task_type = sanitize_human_task_metadata(task_type)
    return HumanTaskSlackMessage(
        repository=repository,
        issue=issue,
        workflow_id=workflow_id,
        task_id=task_id,
        step=step,
        task_type=task_type,
    )


def build_workflow_callback_failure_message(
    *, repository: str, issue: str, step: str, event_type: str, error_code: str
) -> WorkflowCallbackFailureSlackMessage:
    """Build a bounded callback-failure notification without raw exception text."""
    return WorkflowCallbackFailureSlackMessage(
        repository=sanitize_human_task_metadata(repository),
        issue=sanitize_human_task_metadata(issue),
        step=sanitize_human_task_metadata(step),
        event_type=sanitize_human_task_metadata(event_type),
        error_code=sanitize_human_task_metadata(error_code),
    )


def sanitize_human_task_metadata(value: str) -> str:
    """Keep task metadata identifiable without making it Slack-authored content."""
    if (
        len(value) <= MAX_NOTIFICATION_METADATA_LENGTH
        and SAFE_NOTIFICATION_METADATA.fullmatch(value) is not None
        and not _is_url_shaped_metadata(value)
    ):
        return value
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    return f"invalid-{digest}"


def _is_url_shaped_metadata(value: str) -> bool:
    """Reject link-like identifiers while retaining ordinary namespaced IDs."""
    normalized = value.casefold()
    return "://" in normalized or "www." in normalized


def _readable_metadata(value: str, *, fallback: str) -> str:
    """Avoid displaying sanitization hashes to people receiving Slack messages."""
    return fallback if value.startswith("invalid-") else value


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
    try:
        config_path, raw_config = _load_machine_config()
        declaration = _human_task_notification_declaration(raw_config)
    except SlackNotificationError as exc:
        return HumanTaskNotificationSettings(
            enabled=False,
            transport="",
            outcome="skipped",
            code=exc.code,
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
    try:
        _bounded_project_webhook_declarations(
            config_path=config_path,
            declaration=declaration,
        )
    except SlackNotificationError as exc:
        return HumanTaskNotificationSettings(
            enabled=False,
            transport="",
            outcome="skipped",
            code=exc.code,
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


def _login_user_home() -> Path:
    """Resolve the real login home without test seams or mutable environment input."""
    if os.name != "posix":  # pragma: no cover - Windows has no pwd database.
        return Path.home()
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _slack_credential_file() -> Path:
    """Select one package-defined credential file for the current process."""
    user_home = _trusted_user_home()
    if os.environ.get(TEST_RUN_SLACK_ROUTING_ENV) == "1" and user_home == _login_user_home():
        return user_home / TEST_RUN_SLACK_WEBHOOK_FILENAME
    return user_home / SLACK_WEBHOOK_FILENAME


def load_slack_webhook_url(*, repository_root: Path | None = None) -> str:
    """Read a private machine project route or the package default credential."""
    if repository_root is not None and os.environ.get(TEST_RUN_SLACK_ROUTING_ENV) != "1":
        config_path, raw_config = _load_machine_config()
        declaration = _human_task_notification_declaration(raw_config)
        project_url = _project_webhook_route(
            config_path=config_path,
            declaration=declaration,
            repository_root=repository_root,
        )
        if project_url is not None:
            return project_url
    credential_file = _slack_credential_file()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
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
    message: SlackPayloadMessage,
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

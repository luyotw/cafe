"""Trusted Slack delivery for newly materialized HumanTasks."""

from __future__ import annotations

import errno
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

from cafe.core.conversation_locale import DEFAULT_CONVERSATION_LOCALE
from cafe.core.runtime_locales import render_text
from cafe.utils.yaml_utils import safe_load

SLACK_WEBHOOK_FILENAME = ".slack-webhook"
TEST_RUN_SLACK_WEBHOOK_FILENAME = ".cafe/test-slack-webhook"
TEST_RUN_SLACK_ROUTING_ENV = "CAFE_TEST_RUN_SLACK_NOTIFICATIONS"
SLACK_WEBHOOK_HOST = "hooks.slack.com"
MAX_CREDENTIAL_BYTES = 8192
MAX_CREDENTIAL_STORE_BYTES = 65536
MAX_SLACK_DESTINATIONS = 128
SLACK_CREDENTIAL_STORE_FILENAME = "credentials.yaml"
SLACK_DESTINATION_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
MAX_MACHINE_CONFIG_BYTES = 65536
MAX_PROJECT_ROUTES = 128
MACHINE_CONFIG_DIRECTORY = ".cafe"
MACHINE_CONFIG_FILENAME = "config.yaml"
MAX_NOTIFICATION_METADATA_LENGTH = 128
SAFE_NOTIFICATION_METADATA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def _notification_text(name: str, locale: str | None, **values: str) -> str:
    return render_text(f"notification.{name}", locale=locale, **values)


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
    except OSError:
        descriptor = -1
    if descriptor < 0:
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    invalid = False
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
        raw_config = safe_load(config_bytes.decode("utf-8"))
    except SlackNotificationError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        invalid = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    # Raise outside the handler so parser/decoder context cannot expose secrets.
    if invalid:
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
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
    """All nonempty routing maps require the existing private-config boundary."""
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
    *,
    config_path: Path,
    declaration: dict[object, object],
    repository_root: Path | None,
    credential_mode: Literal["v1", "legacy"] = "legacy",
) -> str | None:
    """Validate only routes that resolve to the selected repository."""
    projects = _bounded_project_webhook_declarations(
        config_path=config_path,
        declaration=declaration,
    )
    if repository_root is None:
        return None
    selected_root = _normalise_project_root(repository_root)
    route_field = "destination" if credential_mode == "v1" else "webhook_url"
    selected_values: set[str] = set()
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
        if set(configured_route) != {route_field}:
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            )
        value = configured_route[route_field]
        if not isinstance(value, str):
            raise SlackNotificationError(
                "validation_error", "human_task_notification_config_invalid"
            )
        if credential_mode == "v1":
            if SLACK_DESTINATION_NAME.fullmatch(value) is None:
                raise SlackNotificationError(
                    "validation_error", "human_task_notification_config_invalid"
                )
        else:
            try:
                value = _validate_slack_webhook_url(value)
            except SlackNotificationError as exc:
                raise SlackNotificationError(
                    "validation_error", "human_task_notification_config_invalid"
                ) from exc
        selected_values.add(value)
    if len(selected_values) > 1:
        raise SlackNotificationError("validation_error", "human_task_notification_config_invalid")
    return next(iter(selected_values), None)


@dataclass(frozen=True)
class NotificationPresentation:
    """Transient authored labels supplied by the actual producer, never authority."""

    step_label: str | None = None
    action_label: str | None = None


def _safe_label(value: str | None, fallback: str) -> str:
    """Bound project-authored labels and reject Slack links, mentions and extra lines."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_NOTIFICATION_METADATA_LENGTH
        or any(
            ord(character) < 32 or ord(character) == 127 or character in "<>&@*`|~"
            for character in value
        )
        or _is_url_shaped_metadata(value)
    ):
        return fallback
    return value


@dataclass(frozen=True)
class HumanTaskSlackMessage:
    """Actionable, non-secret fields for one pending HumanTask."""

    repository: str
    issue: str
    workflow_id: str
    task_id: str
    step: str
    task_type: str
    locale: str = DEFAULT_CONVERSATION_LOCALE

    presentation: NotificationPresentation | None = None

    def to_slack_payload(self) -> dict[str, str]:
        def text(name: str, **values: str) -> str:
            return _notification_text(name, self.locale, **values)

        repository = _readable_metadata(self.repository, fallback=text("repository_fallback"))
        issue = _readable_metadata(self.issue, fallback=text("issue_fallback"))
        presentation = self.presentation or NotificationPresentation()
        step_label = _safe_label(presentation.step_label, text("step_fallback"))
        action_label = _safe_label(presentation.action_label, text("action_fallback"))
        separator = text("field_separator")
        lines = (
            text("task_headline"),
            f"{text('repository_field')}{separator}{repository}",
            f"{text('issue_field')}{separator}{issue}",
            f"{text('step_field')}{separator}{step_label}",
            f"{text('action_field')}{separator}{action_label}",
            text("task_closing", issue=issue),
        )
        return {"text": "\n".join(lines)}


@dataclass(frozen=True)
class WorkflowCallbackFailureSlackMessage:
    """Readable, secret-free notification for an asynchronous callback failure."""

    repository: str
    issue: str
    step: str
    event_type: str
    error_code: str
    locale: str = DEFAULT_CONVERSATION_LOCALE

    presentation: NotificationPresentation | None = None

    def to_slack_payload(self) -> dict[str, str]:
        def text(name: str, **values: str) -> str:
            return _notification_text(name, self.locale, **values)

        repository = _readable_metadata(self.repository, fallback=text("repository_fallback"))
        issue = _readable_metadata(self.issue, fallback=text("issue_fallback"))
        presentation = self.presentation or NotificationPresentation()
        step = _safe_label(
            presentation.step_label,
            _safe_label(self.step, text("unknown_step")),
        )
        if self.error_code == "callback_ValueError":
            reason = text("reason_state")
        elif self.error_code.startswith("codex_queue_"):
            reason = text("reason_queue")
        else:
            reason = text("reason_generic")
        separator = text("field_separator")
        lines = (
            text("callback_headline"),
            f"{text('repository_field')}{separator}{repository}",
            f"{text('issue_field')}{separator}{issue}",
            f"{text('step_field')}{separator}{step}",
            f"{text('status_field')}{separator}{reason}",
            text("callback_impact"),
            text("callback_closing", issue=issue),
        )
        return {"text": "\n".join(lines)}


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
    locale: str = DEFAULT_CONVERSATION_LOCALE,
    presentation: NotificationPresentation | None = None,
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
        locale=locale,
        presentation=presentation,
    )


def build_workflow_callback_failure_message(
    *,
    repository: str,
    issue: str,
    step: str,
    event_type: str,
    error_code: str,
    locale: str = DEFAULT_CONVERSATION_LOCALE,
    presentation: NotificationPresentation | None = None,
) -> WorkflowCallbackFailureSlackMessage:
    """Build a bounded callback-failure notification without raw exception text."""
    return WorkflowCallbackFailureSlackMessage(
        repository=sanitize_human_task_metadata(repository),
        issue=sanitize_human_task_metadata(issue),
        step=sanitize_human_task_metadata(step),
        event_type=sanitize_human_task_metadata(event_type),
        error_code=sanitize_human_task_metadata(error_code),
        locale=locale,
        presentation=presentation,
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
        if os.environ.get(TEST_RUN_SLACK_ROUTING_ENV) != "1":
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
    except ValueError:
        parsed = None
    if parsed is None:
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    path_parts = parsed.path.removeprefix("/").split("/")
    valid_tokens = (
        len(path_parts) == 4
        and path_parts[0] == "services"
        and all(re.fullmatch(r"[A-Za-z0-9_-]+", token) for token in path_parts[1:])
    )
    if not (
        not any(ord(character) <= 32 or ord(character) == 127 for character in raw_url)
        and parsed.scheme == "https"
        and parsed.hostname == SLACK_WEBHOOK_HOST
        and parsed.netloc.lower() in {SLACK_WEBHOOK_HOST, f"{SLACK_WEBHOOK_HOST}:443"}
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and "?" not in raw_url
        and "#" not in raw_url
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
    """Select the fixed isolated test path or deprecated legacy path."""
    user_home = _trusted_user_home()
    if os.environ.get(TEST_RUN_SLACK_ROUTING_ENV) == "1":
        return user_home / TEST_RUN_SLACK_WEBHOOK_FILENAME
    return user_home / SLACK_WEBHOOK_FILENAME


def _private_credential_metadata(metadata: os.stat_result, *, allow_unlinked: bool = False) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) & 0o077 == 0
        and (not hasattr(os, "getuid") or metadata.st_uid == os.getuid())
        # An unlinked open inode remains authoritative; reject additional links.
        and (metadata.st_nlink == 1 or (allow_unlinked and metadata.st_nlink == 0))
    )


def _open_slack_credential(path: Path, *, allow_absent: bool = False) -> int | None:
    """Observe mode with exactly one no-follow, non-blocking open."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    code = ""
    try:
        return os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ENOENT:
            if allow_absent:
                return None
            code = "slack_credentials_missing"
        else:
            code = "slack_credentials_unreadable"
            if error.errno == errno.ELOOP:
                code = "slack_credentials_unsafe"
            else:
                try:
                    if not _private_credential_metadata(path.lstat()):
                        code = "slack_credentials_unsafe"
                except OSError:
                    pass
    raise SlackNotificationError("validation_error", code)


def _read_slack_credential(descriptor: int, *, limit: int, allow_unlinked: bool = False) -> bytes:
    """Validate and read only the opened inode, never reopen its pathname."""
    code = ""
    try:
        metadata = os.fstat(descriptor)
        if not _private_credential_metadata(metadata, allow_unlinked=allow_unlinked):
            raise SlackNotificationError("validation_error", "slack_credentials_unsafe")
        if metadata.st_size > limit:
            raise SlackNotificationError("validation_error", "slack_credentials_invalid")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            contents = stream.read(limit + 1)
    except OSError:
        code = "slack_credentials_unreadable"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if code:
        raise SlackNotificationError("validation_error", code)
    if len(contents) > limit:
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    return contents


class _SlackCredentialStoreLoader(yaml.SafeLoader):
    """Safe YAML with no aliases, anchors, explicit tags or duplicate keys."""

    def compose_node(self, parent, index):
        event = self.peek_event()
        if (
            isinstance(event, yaml.events.AliasEvent)
            or getattr(event, "anchor", None) is not None
            or getattr(event, "tag", None) is not None
        ):
            raise yaml.YAMLError("unsupported credential YAML syntax")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key == "<<" or key in mapping:
                raise yaml.YAMLError("invalid credential mapping key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _load_slack_destinations(descriptor: int) -> dict[str, str]:
    """Validate the entire bounded v1 store before any destination selection."""
    contents = _read_slack_credential(
        descriptor, limit=MAX_CREDENTIAL_STORE_BYTES, allow_unlinked=True
    )
    invalid = False
    try:
        text = contents.decode("utf-8")
        if not text.strip():
            raise SlackNotificationError("validation_error", "slack_credentials_empty")
        store = yaml.load(text, Loader=_SlackCredentialStoreLoader)
    except (UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        invalid = True
    if invalid:
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    if not (
        isinstance(store, dict)
        and set(store) == {"version", "slack"}
        and type(store["version"]) is int
        and store["version"] == 1
        and isinstance(store["slack"], dict)
        and set(store["slack"]) == {"destinations"}
    ):
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    destinations = store["slack"]["destinations"]
    if not (
        isinstance(destinations, dict)
        and "default" in destinations
        and len(destinations) <= MAX_SLACK_DESTINATIONS
    ):
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    validated = {}
    for name, destination in destinations.items():
        if not (
            isinstance(name, str)
            and SLACK_DESTINATION_NAME.fullmatch(name) is not None
            and isinstance(destination, dict)
            and set(destination) == {"webhook_url"}
            and isinstance(destination["webhook_url"], str)
        ):
            raise SlackNotificationError("validation_error", "slack_credentials_invalid")
        validated[name] = _validate_slack_webhook_url(destination["webhook_url"])
    return validated


def _load_legacy_slack_webhook(path: Path) -> str:
    """Retain the legacy bounded text read while sanitizing decoder failures."""
    descriptor = _open_slack_credential(path)
    assert descriptor is not None
    code = ""
    try:
        if not _private_credential_metadata(os.fstat(descriptor)):
            raise SlackNotificationError("validation_error", "slack_credentials_unsafe")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            webhook_url = stream.read(MAX_CREDENTIAL_BYTES + 1).strip()
    except UnicodeError:
        code = "slack_credentials_invalid"
    except OSError:
        code = "slack_credentials_unreadable"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if code:
        raise SlackNotificationError("validation_error", code)
    if len(webhook_url.encode("utf-8")) > MAX_CREDENTIAL_BYTES:
        raise SlackNotificationError("validation_error", "slack_credentials_invalid")
    if not webhook_url:
        raise SlackNotificationError("validation_error", "slack_credentials_empty")
    return _validate_slack_webhook_url(webhook_url)


def load_slack_webhook_url(*, repository_root: Path | None = None) -> str:
    """Resolve exactly one machine-owned destination for both package consumers."""
    if os.environ.get(TEST_RUN_SLACK_ROUTING_ENV) == "1":
        return _load_legacy_slack_webhook(_slack_credential_file())
    store_path = _trusted_user_home() / MACHINE_CONFIG_DIRECTORY / SLACK_CREDENTIAL_STORE_FILENAME
    descriptor = _open_slack_credential(store_path, allow_absent=True)
    destinations = _load_slack_destinations(descriptor) if descriptor is not None else None
    if destinations is None and repository_root is None:
        return _load_legacy_slack_webhook(_slack_credential_file())
    config_path, raw_config = _load_machine_config()
    declaration = _human_task_notification_declaration(raw_config)
    route = _project_webhook_route(
        config_path=config_path,
        declaration=declaration,
        repository_root=repository_root,
        credential_mode="v1" if destinations is not None else "legacy",
    )
    if destinations is not None:
        name = route if route is not None else "default"
        if name not in destinations:
            raise SlackNotificationError(
                "validation_error", "slack_credentials_destination_missing"
            )
        return destinations[name]
    if route is not None:
        return route
    return _load_legacy_slack_webhook(_slack_credential_file())


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
    except HTTPError:
        failure = ("script_exit_error", "slack_http_error")
    except TimeoutError:
        failure = ("timeout_error", "slack_timeout")
    except (URLError, OSError):
        failure = ("script_exit_error", "slack_transport_error")
    else:
        failure = None
    # Transport errors can embed the request URL; retain only stable codes.
    if failure is not None:
        raise SlackNotificationError(*failure)
    if status != 200:
        raise SlackNotificationError("script_exit_error", "slack_http_error")
    if body != "ok":
        raise SlackNotificationError("script_exit_error", "slack_response_not_ok")

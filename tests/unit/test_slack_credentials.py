"""Named destination, migration and private-file invariants of the shared resolver."""

from __future__ import annotations

import errno
import json
import os
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import cafe.core.human_task_notifications as notifications
from cafe.core.human_task_notifications import SlackNotificationError, load_slack_webhook_url

DEFAULT = "https://hooks.slack.com/services/T/B/default-secret"
OPENFUN = "https://hooks.slack.com/services/T/B/openfun-secret"
OPERATIONS = "https://hooks.slack.com/services/T/B/operations-secret"
MARKER = "recognizable-secret-marker"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "login-home"
    (home / ".cafe").mkdir(parents=True)
    monkeypatch.delenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", raising=False)
    monkeypatch.setattr(notifications, "_trusted_user_home", lambda: home)
    monkeypatch.setattr(notifications, "_login_user_home", lambda: home)
    legacy = home / ".slack-webhook"
    legacy.write_text(DEFAULT)
    legacy.chmod(0o600)
    return home


def write_store(home, contents=None):
    path = home / ".cafe/credentials.yaml"
    if contents is None:
        contents = yaml.safe_dump(
            {
                "version": 1,
                "slack": {
                    "destinations": {
                        "default": {"webhook_url": DEFAULT},
                        "openfun": {"webhook_url": OPENFUN},
                        "operations": {"webhook_url": OPERATIONS},
                    }
                },
            }
        ).encode()
    path.write_bytes(contents)
    path.chmod(0o600)
    return path


def write_routes(home, projects):
    path = home / ".cafe/config.yaml"
    path.write_text(yaml.safe_dump({"notifications": {"human_tasks": {"projects": projects}}}))
    path.chmod(0o600)
    return path


def assert_error(code, repository_root=None):
    with pytest.raises(SlackNotificationError) as caught:
        load_slack_webhook_url(repository_root=repository_root)
    assert caught.value.code == code
    return caught.value


def test_routes_select_one_named_destination_or_default(home, tmp_path):
    write_store(home)
    a, b, c = (tmp_path / name for name in ("a", "b", "c"))
    write_routes(
        home,
        {
            str(a): {"destination": "openfun"},
            str(b): {"destination": "operations"},
            str(c): {"destination": "openfun"},
        },
    )
    assert load_slack_webhook_url(repository_root=a) == OPENFUN
    assert load_slack_webhook_url(repository_root=b) == OPERATIONS
    assert load_slack_webhook_url(repository_root=c) == OPENFUN
    assert load_slack_webhook_url(repository_root=tmp_path / "unmatched") == DEFAULT
    assert load_slack_webhook_url() == DEFAULT


@pytest.mark.parametrize("v1", [False, True])
@pytest.mark.parametrize("projects", [None, {}, [], False, 1, "routes"])
def test_projects_null_and_empty_are_compatible_but_other_types_fail(home, tmp_path, v1, projects):
    if v1:
        write_store(home)
    write_routes(home, projects)
    if projects is None or projects == {}:
        assert load_slack_webhook_url(repository_root=tmp_path) == DEFAULT
    else:
        assert_error("human_task_notification_config_invalid", tmp_path)


@pytest.mark.parametrize("v1", [False, True])
@pytest.mark.parametrize(
    "route",
    [
        None,
        True,
        [],
        {},
        {"destination": None},
        {"destination": False},
        {"destination": 1},
        {"destination": "Openfun"},
        {"destination": "../openfun"},
        {"destination": "a" * 65},
        {"destination": ["openfun"]},
        {"destination": "openfun", "webhook_url": OPENFUN},
        {"destination": "openfun", "path": "/tmp/secret"},
    ],
)
def test_selected_malformed_route_fails_closed_in_either_mode(home, tmp_path, v1, route):
    if v1:
        write_store(home)
    write_routes(home, {str(tmp_path): route})
    assert_error("human_task_notification_config_invalid", tmp_path)


@pytest.mark.parametrize("v1", [False, True])
def test_route_mode_is_exclusive_and_unmatched_siblings_are_isolated(home, tmp_path, v1):
    if v1:
        write_store(home)
    valid = {"destination": "openfun"} if v1 else {"webhook_url": OPENFUN}
    wrong = {"webhook_url": DEFAULT} if v1 else {"destination": "default"}
    write_routes(
        home, {str(tmp_path): valid, str(tmp_path / "sibling"): wrong, "relative": None, 42: None}
    )
    assert load_slack_webhook_url(repository_root=tmp_path) == OPENFUN
    assert load_slack_webhook_url(repository_root=tmp_path / "unmatched") == DEFAULT
    assert_error("human_task_notification_config_invalid", tmp_path / "sibling")


@pytest.mark.parametrize("conflict", [False, True])
def test_normalized_route_collision_is_deterministic(home, tmp_path, conflict):
    write_store(home)
    write_routes(
        home,
        {
            str(tmp_path): {"destination": "openfun"},
            str(tmp_path / "child/.."): {"destination": "operations" if conflict else "openfun"},
        },
    )
    if conflict:
        assert_error("human_task_notification_config_invalid", tmp_path)
    else:
        assert load_slack_webhook_url(repository_root=tmp_path) == OPENFUN


def test_valid_but_missing_destination_is_not_default_or_legacy(home, tmp_path):
    write_store(home)
    write_routes(home, {str(tmp_path): {"destination": "missing"}})
    assert_error("slack_credentials_destination_missing", tmp_path)


@pytest.mark.parametrize("name", ["default", "a" * 64])
def test_reserved_default_and_maximum_name_are_valid(home, tmp_path, name):
    write_store(
        home,
        yaml.safe_dump(
            {
                "version": 1,
                "slack": {
                    "destinations": {
                        "default": {"webhook_url": DEFAULT},
                        name: {"webhook_url": OPENFUN},
                    }
                },
            }
        ).encode(),
    )
    write_routes(home, {str(tmp_path): {"destination": name}})
    assert load_slack_webhook_url(repository_root=tmp_path) == OPENFUN


@pytest.mark.parametrize(
    "contents",
    [
        b"",
        b" \n\t ",
    ],
)
def test_empty_store_has_distinct_error_and_never_falls_back(home, contents):
    write_store(home, contents)
    assert_error("slack_credentials_empty")


@pytest.mark.parametrize(
    "contents",
    [
        b"\xff",
        b"null",
        b"[]",
        b"# only a comment",
        b"version: [",
        b"version: 1\n",
        b"version: true\nslack: {}",
        b"version: '1'\nslack: {}",
        b"version: 2\nslack: {}",
        b"version: 1\nslack: {destinations: {}}",
        b"version: 1\nslack: {destinations: {default: {webhook_url: false}}}",
        b"version: 1\nslack: {destinations: {default: {webhook_url: null}}}",
        b"version: 1\nslack: {destinations: {default: {webhook_url: 42}}}",
    ],
)
def test_invalid_encoding_yaml_version_or_schema_fails_closed(home, contents):
    write_store(home, contents)
    assert_error("slack_credentials_invalid")


@pytest.mark.parametrize("level", ["root", "slack", "destinations", "destination"])
@pytest.mark.parametrize("key", ["extra", 42, None, True])
def test_all_mapping_levels_reject_extra_or_non_string_keys(home, level, key):
    doc = {"version": 1, "slack": {"destinations": {"default": {"webhook_url": DEFAULT}}}}
    mapping = {
        "root": doc,
        "slack": doc["slack"],
        "destinations": doc["slack"]["destinations"],
        "destination": doc["slack"]["destinations"]["default"],
    }[level]
    # A new destination is schema-valid only when its value is a credential mapping.
    mapping[key] = None
    write_store(home, yaml.safe_dump(doc).encode())
    assert_error("slack_credentials_invalid")


@pytest.mark.parametrize(
    "contents",
    [
        "version: 1\nversion: 1\nslack: {destinations: {default: {webhook_url: %s}}}" % DEFAULT,
        "version: 1\nslack:\n  destinations: {default: {webhook_url: %s}}\n  destinations: {}"
        % DEFAULT,
        (
            "version: 1\nslack:\n  destinations:\n"
            "    default: {webhook_url: %s}\n    default: {webhook_url: %s}"
        )
        % (DEFAULT, OPENFUN),
        "version: 1\nslack:\n  destinations:\n    default: {webhook_url: %s, webhook_url: %s}"
        % (DEFAULT, OPENFUN),
        "version: 1\nslack: &slack {destinations: {default: {webhook_url: %s}}}" % DEFAULT,
        "version: 1\nslack: {destinations: {default: &d {webhook_url: %s}, other: *d}}" % DEFAULT,
        "version: !!int 1\nslack: {destinations: {default: {webhook_url: %s}}}" % DEFAULT,
        "version: 1\nslack: {destinations: {default: {<<: {webhook_url: %s}}}}" % DEFAULT,
    ],
)
def test_duplicate_keys_anchors_aliases_tags_and_merge_are_rejected(home, contents):
    write_store(home, contents.encode())
    assert_error("slack_credentials_invalid")


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/services/T/B/value",
        "https://evil.test/services/T/B/value",
        "https://user@hooks.slack.com/services/T/B/value",
        "https://hooks.slack.com:444/services/T/B/value",
        "https://hooks.slack.com/services/T/B/value?secret=value",
        "https://hooks.slack.com/services/T/B/value#secret",
        "https://hooks.slack.com/services/T/B/value;param",
        "https://hooks.slack.com:bad/services/T/B/value",
        "https://hooks.slack.com:/services/T/B/value",
        "https://hooks.slack.com/services/T/B/value?",
        "https://hooks.slack.com/services/T/B/value#",
        "https://hooks.slack.com/services/T/B/val\nue",
    ],
)
def test_unselected_invalid_destination_invalidates_the_entire_store(home, tmp_path, url):
    write_store(
        home,
        yaml.safe_dump(
            {
                "version": 1,
                "slack": {
                    "destinations": {
                        "default": {"webhook_url": DEFAULT},
                        "unused": {"webhook_url": url},
                    }
                },
            }
        ).encode(),
    )
    write_routes(home, {str(tmp_path): {"destination": "default"}})
    assert_error("slack_credentials_invalid", tmp_path)


def test_explicit_https_port_is_valid(home):
    url = DEFAULT.replace("hooks.slack.com", "hooks.slack.com:443")
    write_store(
        home,
        yaml.safe_dump(
            {"version": 1, "slack": {"destinations": {"default": {"webhook_url": url}}}}
        ).encode(),
    )
    assert load_slack_webhook_url() == url


@pytest.mark.parametrize("extra", [0, 1])
def test_raw_byte_limit_includes_whitespace(home, extra):
    path = write_store(home)
    raw = path.read_bytes()
    path.write_bytes(raw + b" " * (65536 + extra - len(raw)))
    if extra:
        assert_error("slack_credentials_invalid")
    else:
        assert load_slack_webhook_url() == DEFAULT


@pytest.mark.parametrize("count", [128, 129])
def test_destination_count_is_bounded_including_default(home, count):
    destinations = {"default": {"webhook_url": DEFAULT}}
    destinations.update({f"d{i}": {"webhook_url": OPENFUN} for i in range(count - 1)})
    write_store(
        home, yaml.safe_dump({"version": 1, "slack": {"destinations": destinations}}).encode()
    )
    if count == 129:
        assert_error("slack_credentials_invalid")
    else:
        assert load_slack_webhook_url() == DEFAULT


@pytest.mark.parametrize(
    "kind",
    ["mode", "owner", "symlink", "broken_symlink", "hardlink", "directory", "fifo", "socket"],
)
def test_unsafe_store_never_reads_legacy(home, monkeypatch, kind):
    import socket

    path = write_store(home)
    if kind == "mode":
        path.chmod(0o644)
    elif kind == "owner":
        metadata = path.stat()
        monkeypatch.setattr(
            notifications.os,
            "fstat",
            lambda fd: SimpleNamespace(
                st_mode=metadata.st_mode,
                st_uid=metadata.st_uid + 1,
                st_nlink=1,
                st_size=metadata.st_size,
            ),
        )
    elif kind == "hardlink":
        os.link(path, home / "copy")
    else:
        path.unlink()
        if kind == "directory":
            path.mkdir()
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
        elif kind == "socket":
            with socket.socket(socket.AF_UNIX) as sock:
                with monkeypatch.context() as context:
                    context.chdir(path.parent)
                    sock.bind(path.name)
        else:
            path.symlink_to(home / (".slack-webhook" if kind == "symlink" else "missing"))
    assert_error("slack_credentials_unsafe")


@pytest.mark.parametrize("error", [errno.EACCES, errno.EIO])
def test_non_enoent_open_error_does_not_trigger_legacy(home, monkeypatch, error):
    path = write_store(home)
    original = os.open
    calls = []

    def fail_open(candidate, flags, *args, **kwargs):
        calls.append(Path(candidate))
        if Path(candidate) == path:
            raise OSError(error, "unreadable")
        return original(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(notifications.os, "open", fail_open)
    assert_error("slack_credentials_unreadable")
    assert calls.count(path) == 1
    assert home / ".slack-webhook" not in calls


@pytest.mark.parametrize("mutation", ["remove", "replace"])
def test_successful_open_descriptor_remains_authority_after_entry_mutation(
    home, monkeypatch, mutation
):
    path = write_store(home)
    original = os.open
    calls = []

    def mutate_after_open(candidate, flags, *args, **kwargs):
        fd = original(candidate, flags, *args, **kwargs)
        if Path(candidate) == path:
            calls.append(flags)
            path.unlink()
            if mutation == "replace":
                write_store(home, b"invalid replacement")
        return fd

    monkeypatch.setattr(notifications.os, "open", mutate_after_open)
    assert load_slack_webhook_url() == DEFAULT
    assert len(calls) == 1
    assert calls[0] & os.O_NOFOLLOW
    assert calls[0] & os.O_NONBLOCK


def test_enoent_observation_keeps_legacy_for_this_invocation(home, monkeypatch):
    path = home / ".cafe/credentials.yaml"
    original = os.open
    calls = []

    def create_after_enoent(candidate, flags, *args, **kwargs):
        if Path(candidate) == path:
            calls.append(path)
            if len(calls) == 1:
                write_store(home, b"invalid new store")
                raise FileNotFoundError(errno.ENOENT, "absent")
        return original(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(notifications.os, "open", create_after_enoent)
    assert load_slack_webhook_url() == DEFAULT
    assert_error("slack_credentials_invalid")
    assert len(calls) == 2


@pytest.mark.parametrize("operation", ["fstat", "read"])
def test_descriptor_io_failure_is_unreadable_without_fallback(home, monkeypatch, operation):
    write_store(home)
    if operation == "fstat":

        def fail(fd):
            raise OSError(errno.EIO, "metadata unavailable")

        monkeypatch.setattr(notifications.os, "fstat", fail)
    else:
        original = os.fdopen

        class BrokenRead:
            def __init__(self, fd, *args, **kwargs):
                self.stream = original(fd, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def read(self, limit):
                raise OSError(errno.EIO, "read unavailable")

        monkeypatch.setattr(notifications.os, "fdopen", BrokenRead)
    assert_error("slack_credentials_unreadable")


@pytest.mark.parametrize("source", ["store", "config", "legacy"])
def test_decoder_parser_errors_never_cross_trusted_boundary_with_secret(
    home, tmp_path, source, caplog
):
    if source == "store":
        write_store(home, f"version: 1\nslack: [{MARKER}".encode())
        expected = "slack_credentials_invalid"
    elif source == "config":
        (home / ".cafe/config.yaml").write_text(f"notifications: [{MARKER}")
        expected = "human_task_notification_config_invalid"
    else:
        (home / ".slack-webhook").write_bytes(MARKER.encode() + b"\xff")
        expected = "slack_credentials_invalid"
    error = assert_error(expected, tmp_path)
    chain = []
    current = error
    while current is not None:
        chain.append(repr(current))
        current = current.__cause__ or current.__context__
    assert MARKER not in json.dumps(chain)
    assert MARKER not in "".join(traceback.format_exception(error))
    assert MARKER not in caplog.text


def test_home_environment_project_content_and_config_cannot_select_store(
    home, tmp_path, monkeypatch
):
    write_store(home)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".cafe").mkdir()
    write_store(project, b"invalid project store")
    write_routes(project, {str(project): {"destination": "operations"}})
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(project))
    monkeypatch.setenv("CAFE_SLACK_DESTINATION", "operations")
    monkeypatch.setenv("CAFE_SLACK_CREDENTIAL_PATH", str(project / ".cafe/credentials.yaml"))
    monkeypatch.setenv("CAFE_SLACK_WEBHOOK", OPERATIONS)
    assert load_slack_webhook_url(repository_root=project) == DEFAULT


@pytest.mark.parametrize("test_credential", [None, "invalid", OPENFUN])
def test_test_run_skips_all_normal_sources_even_invalid_store_and_routes(
    home, tmp_path, monkeypatch, test_credential
):
    write_store(home, b"invalid normal store")
    write_routes(home, {str(tmp_path): {"destination": "missing"}})
    monkeypatch.setenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", "1")
    if test_credential is not None:
        path = home / ".cafe/test-slack-webhook"
        path.write_text(test_credential)
        path.chmod(0o600)
    if test_credential == OPENFUN:
        assert load_slack_webhook_url(repository_root=tmp_path) == OPENFUN
    else:
        assert_error(
            "slack_credentials_missing" if test_credential is None else "slack_credentials_invalid",
            tmp_path,
        )


def test_named_routes_still_require_private_machine_config(home, tmp_path):
    write_store(home)
    config = write_routes(home, {str(tmp_path): {"destination": "default"}})
    config.chmod(0o644)
    assert_error("human_task_notification_config_unsafe", tmp_path)


@pytest.mark.parametrize("location", ["root", "slack", "destinations", "destination"])
@pytest.mark.parametrize("value", [None, True, 1, "text", []])
def test_required_store_mapping_shapes_are_strict(home, location, value):
    doc = {"version": 1, "slack": {"destinations": {"default": {"webhook_url": DEFAULT}}}}
    if location == "root":
        doc = value
    elif location == "slack":
        doc["slack"] = value
    elif location == "destinations":
        doc["slack"]["destinations"] = value
    else:
        doc["slack"]["destinations"]["default"] = value
    write_store(home, yaml.safe_dump(doc).encode())
    assert_error("slack_credentials_invalid")


@pytest.mark.parametrize(
    "name", ["", "Default", "a" * 65, "two words", "../default", "_hidden", "1first"]
)
def test_unselected_destination_names_are_always_validated(home, name):
    write_store(
        home,
        yaml.safe_dump(
            {
                "version": 1,
                "slack": {
                    "destinations": {
                        "default": {"webhook_url": DEFAULT},
                        name: {"webhook_url": OPENFUN},
                    }
                },
            }
        ).encode(),
    )
    assert_error("slack_credentials_invalid")


def test_complete_store_validation_precedes_route_selection(home, tmp_path):
    write_store(home, b"version: 1\nslack: {destinations: {openfun: {webhook_url: invalid}}}")
    write_routes(home, {str(tmp_path): {"destination": "INVALID_NAME"}})
    assert_error("slack_credentials_invalid", tmp_path)


@pytest.mark.parametrize("v1", [False, True])
def test_omitted_projects_preserves_default_resolution(home, tmp_path, v1):
    if v1:
        write_store(home)
    (home / ".cafe/config.yaml").write_text("notifications:\n  human_tasks:\n    enabled: true\n")
    assert load_slack_webhook_url(repository_root=tmp_path) == DEFAULT


def test_store_growth_during_read_is_still_bounded_and_invalid(home, monkeypatch):
    path = write_store(home)
    original = os.fstat

    def grow_after_stat(fd):
        metadata = original(fd)
        path.write_bytes(path.read_bytes() + b" " * 65536)
        return metadata

    monkeypatch.setattr(notifications.os, "fstat", grow_after_stat)
    assert_error("slack_credentials_invalid")


@pytest.mark.parametrize("source", ["yaml", "utf8"])
def test_secret_bearing_config_decoder_and_parser_exception_chain_is_clean(home, tmp_path, source):
    contents = (
        f"notifications:\n  human_tasks:\n    webhook: {MARKER}\n   bad: indentation\n".encode()
        if source == "yaml"
        else MARKER.encode() + b"\xff"
    )
    (home / ".cafe/config.yaml").write_bytes(contents)
    error = assert_error("human_task_notification_config_invalid", tmp_path)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert MARKER not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize("kind", ["http", "timeout", "transport"])
def test_transport_exception_chain_never_contains_url_or_secret(home, monkeypatch, kind):
    from urllib.error import HTTPError, URLError

    url = DEFAULT.replace("default-secret", MARKER)
    errors = {
        "http": HTTPError(url, 302, MARKER, {}, None),
        "timeout": TimeoutError(url),
        "transport": URLError(url),
    }

    def fail(request, *, timeout):
        raise errors[kind]

    monkeypatch.setattr(notifications, "_open_slack_request", fail)
    message = notifications.build_workflow_callback_failure_message(
        repository="repo",
        issue="issue",
        step="develop",
        event_type="phase_terminal",
        error_code="callback_ValueError",
    )
    with pytest.raises(SlackNotificationError) as caught:
        notifications.post_slack_notification(url, message, timeout_sec=4.0)
    error = caught.value
    assert (
        error.code
        == {
            "http": "slack_http_error",
            "timeout": "slack_timeout",
            "transport": "slack_transport_error",
        }[kind]
    )
    assert error.__context__ is None
    assert error.__cause__ is None
    assert MARKER not in "".join(traceback.format_exception(error))


def test_v1_success_never_opens_legacy_fallback(home, monkeypatch):
    path = write_store(home)
    original = os.open
    opened = []

    def record(candidate, flags, *args, **kwargs):
        opened.append(Path(candidate))
        return original(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(notifications.os, "open", record)
    assert load_slack_webhook_url() == DEFAULT
    assert opened.count(path) == 1
    assert home / ".slack-webhook" not in opened


def test_legacy_bounded_text_read_preserves_whitespace_compatibility(home):
    legacy = home / ".slack-webhook"
    legacy.write_text(DEFAULT + " " * 8192)
    assert load_slack_webhook_url() == DEFAULT


def test_legacy_without_repository_identity_preserves_config_independence(home):
    (home / ".cafe/config.yaml").write_text("malformed: [")
    assert load_slack_webhook_url() == DEFAULT

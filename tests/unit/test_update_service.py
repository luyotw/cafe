"""U7: read-only runtime update decisions and exact approved apply."""

import json
from dataclasses import dataclass

import pytest

from cafe.updates.service import (
    LatestRelease,
    UpdateApplyError,
    UpdateService,
    _latest_github_release,
)


def _release(version: str) -> LatestRelease:
    tag = f"v{version}"
    return LatestRelease(
        version=version,
        tag=tag,
        release_url=f"https://github.com/luyotw/cafe/releases/tag/{tag}",
        install_url=f"https://github.com/luyotw/cafe/archive/refs/tags/{tag}.tar.gz",
    )


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_latest_release_uses_the_canonical_github_release(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[object, int]] = []

    def urlopen(request: object, timeout: int) -> _Response:
        requests.append((request, timeout))
        return _Response(
            {
                "tag_name": "v0.3.3",
                "html_url": "https://github.com/luyotw/cafe/releases/tag/v0.3.3",
                "draft": False,
                "prerelease": False,
            }
        )

    monkeypatch.setattr("cafe.updates.service.urllib.request.urlopen", urlopen)

    release = _latest_github_release()

    assert release == _release("0.3.3")
    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.full_url == "https://api.github.com/repos/luyotw/cafe/releases/latest"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert timeout == 2


@pytest.mark.parametrize(
    "payload",
    [
        {
            "tag_name": "v0.3.3",
            "html_url": "https://github.com/luyotw/cafe/releases/tag/v0.3.3",
            "draft": True,
            "prerelease": False,
        },
        {
            "tag_name": "v0.3.3",
            "html_url": "https://github.com/luyotw/cafe/releases/tag/v0.3.3",
            "draft": False,
            "prerelease": True,
        },
        {
            "tag_name": "0.3.3",
            "html_url": "https://github.com/luyotw/cafe/releases/tag/0.3.3",
            "draft": False,
            "prerelease": False,
        },
        {
            "tag_name": "v0.3.3",
            "html_url": "https://example.test/v0.3.3",
            "draft": False,
            "prerelease": False,
        },
    ],
)
def test_latest_release_fails_closed_for_noncanonical_responses(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]
) -> None:
    monkeypatch.setattr(
        "cafe.updates.service.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )

    with pytest.raises(ValueError):
        _latest_github_release()


@pytest.mark.parametrize(
    ("installed", "latest", "status"),
    [
        ("1.2.0", "1.2.0", "current"),
        ("1.2.0", "1.10.0", "update_available"),
        ("2.0.0rc1", "2.0.0", "update_available"),
        ("2.0.0", "2.0.0rc1", "current"),
    ],
)
def test_read_only_check_uses_pep440_without_installing(
    installed: str, latest: str, status: str
) -> None:
    calls: list[list[str]] = []
    service = UpdateService(
        installed_version=lambda: installed,
        latest_release=lambda: _release(latest),
        runner=lambda command: calls.append(command),
    )

    result = service.check()

    assert result.status == status
    assert result.installed_version == installed
    assert result.latest_version == latest
    assert calls == []


def test_unavailable_check_is_explicit_and_non_blocking() -> None:
    service = UpdateService(
        installed_version=lambda: "1.0.0",
        latest_release=lambda: (_ for _ in ()).throw(OSError("offline")),
    )

    result = service.check()

    assert result.status == "unavailable"
    assert result.installed_version == "1.0.0"
    assert result.latest_version is None
    assert result.error


@dataclass
class _RunResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def test_apply_targets_exact_approved_release_and_mandatorily_rechecks() -> None:
    state = {"installed": "1.0.0", "checks": 0}
    commands: list[list[str]] = []

    def installed() -> str:
        state["checks"] += 1
        return state["installed"]

    def run(command: list[str]) -> _RunResult:
        commands.append(command)
        state["installed"] = "1.1.0"
        return _RunResult()

    service = UpdateService(
        installed_version=installed,
        latest_release=lambda: _release("1.1.0"),
        runner=run,
        python_executable="/approved/python",
    )
    preview = service.check()

    result = service.apply(preview.token)

    assert commands == [
        [
            "/approved/python",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "https://github.com/luyotw/cafe/archive/refs/tags/v1.1.0.tar.gz",
        ]
    ]
    assert result.installed_version == "1.1.0"
    assert result.status == "current"
    assert state["checks"] >= 3


def test_apply_rejects_absent_or_stale_approval_before_install() -> None:
    latest = {"version": "1.1.0"}
    commands: list[list[str]] = []
    service = UpdateService(
        installed_version=lambda: "1.0.0",
        latest_release=lambda: _release(latest["version"]),
        runner=lambda command: commands.append(command),
    )
    preview = service.check()
    latest["version"] = "1.2.0"

    with pytest.raises(UpdateApplyError):
        service.apply("")
    with pytest.raises(UpdateApplyError):
        service.apply(preview.token)
    assert commands == []


def test_apply_fails_when_post_check_does_not_observe_approved_version() -> None:
    service = UpdateService(
        installed_version=lambda: "1.0.0",
        latest_release=lambda: _release("1.1.0"),
        runner=lambda _command: _RunResult(),
    )
    preview = service.check()

    with pytest.raises(UpdateApplyError, match="post-update check"):
        service.apply(preview.token)

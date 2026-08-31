"""U7: read-only runtime update decisions and exact approved apply."""

from dataclasses import dataclass

import pytest

from cafe.updates.service import UpdateApplyError, UpdateService


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
        latest_release=lambda: (latest, f"https://example.test/{latest}"),
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
        latest_release=lambda: ("1.1.0", "https://example.test/1.1.0"),
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
            "cafe-engine==1.1.0",
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
        latest_release=lambda: (
            latest["version"],
            f"https://example.test/{latest['version']}",
        ),
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
        latest_release=lambda: ("1.1.0", "https://example.test/1.1.0"),
        runner=lambda _command: _RunResult(),
    )
    preview = service.check()

    with pytest.raises(UpdateApplyError, match="post-update check"):
        service.apply(preview.token)

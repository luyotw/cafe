"""U7: read-only runtime update decisions and exact approved apply."""

import json
from dataclasses import dataclass

import pytest

from cafe.updates.service import (
    LatestRelease,
    UpdateApplyError,
    UpdateService,
    SYNC_INSTALLED_HELPERS,
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


@pytest.mark.parametrize("user_site", [None, "/custom/user-base/site-packages"])
def test_apply_targets_exact_approved_release_and_mandatorily_rechecks(
    monkeypatch, user_site,
) -> None:
    monkeypatch.setattr("cafe.updates.service.site.ENABLE_USER_SITE", user_site is not None)
    monkeypatch.setattr("cafe.updates.service.site.getusersitepackages", lambda: user_site)
    state = {"installed": "1.0.0", "checks": 0}
    commands: list[list[str]] = []

    def installed() -> str:
        state["checks"] += 1
        return state["installed"]

    def run(command: list[str]) -> _RunResult:
        commands.append(command)
        state["installed"] = "1.1.0"
        if "-I" in command:
            return _RunResult(stdout=json.dumps({
                "schema_version": 1, "installed_version": "1.1.0",
                "post_change_verified": True,
            }))
        return _RunResult()

    service = UpdateService(
        installed_version=installed,
        latest_release=lambda: _release("1.1.0"),
        runner=run,
        python_executable="/approved/python",
    )
    preview = service.check()

    result = service.apply(preview.token)

    assert commands[0] == (
        [
            "/approved/python",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "https://github.com/luyotw/cafe/archive/refs/tags/v1.1.0.tar.gz",
        ]
    )
    assert commands[1] == [
        "/approved/python", "-I", "-c", SYNC_INSTALLED_HELPERS, "1.1.0",
    ] + ([user_site] if user_site is not None else [])
    assert result.installed_version == "1.1.0"
    assert result.status == "current"
    assert state["checks"] >= 3
    assert result.helper_sync["post_change_verified"] is True


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


@pytest.mark.parametrize("failure", ["exit", "exception", "malformed", "version", "unverified"])
def test_helper_sync_failure_reports_the_installed_runtime_as_partial(failure):
    state = {"installed": "1.0.0"}
    commands = []

    def run(command):
        commands.append(command)
        if "pip" in command:
            state["installed"] = "1.1.0"
            return _RunResult()
        if failure == "exception":
            raise OSError("locked helper directory")
        if failure == "exit":
            return _RunResult(returncode=1, stderr="publication failed")
        if failure == "malformed":
            return _RunResult(stdout="not JSON")
        return _RunResult(stdout=json.dumps({
            "schema_version": 1,
            "installed_version": "wrong" if failure == "version" else "1.1.0",
            "post_change_verified": False if failure == "unverified" else True,
        }))

    service = UpdateService(
        installed_version=lambda: state["installed"],
        latest_release=lambda: _release("1.1.0"), runner=run,
    )
    with pytest.raises(UpdateApplyError, match="was installed, but bundled helper") as error:
        service.apply(service.check().token)
    assert error.value.runtime_installed is True
    assert "--installed-bundle --expected-version 1.1.0" in str(error.value)
    assert "existing CAFE helper destinations" in str(error.value)
    assert "cafe skill sync-global" not in str(error.value)
    assert state["installed"] == "1.1.0"
    assert len(commands) == 2


def test_failed_install_never_synchronizes_helpers():
    commands = []

    def run(command):
        commands.append(command)
        return _RunResult(returncode=1, stderr="installer failed")

    service = UpdateService(
        installed_version=lambda: "1.0.0", latest_release=lambda: _release("1.1.0"), runner=run,
    )
    with pytest.raises(UpdateApplyError, match="installer failed"):
        service.apply(service.check().token)
    assert len(commands) == 1 and "pip" in commands[0]


def test_isolated_helper_loads_custom_user_install_and_excludes_checkout(tmp_path):
    import os
    import subprocess
    import sys

    environment = os.environ.copy()
    environment["PYTHONUSERBASE"] = str(tmp_path / "custom-user-base")
    checkout = tmp_path / "checkout"
    decoy = checkout / "cafe"
    decoy.mkdir(parents=True)
    (decoy / "__init__.py").write_text("raise RuntimeError('stale checkout loaded')\n")
    environment["PYTHONPATH"] = str(checkout)
    discovery = subprocess.run(
        [sys.executable, "-c", "import site; print(site.getusersitepackages())"],
        env=environment, text=True, capture_output=True, check=True,
    )
    from pathlib import Path

    user_site = Path(discovery.stdout.strip())
    package = user_site / "cafe"
    skills = package / "skills"
    skills.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (skills / "__init__.py").write_text("")
    # No destinations are selected: this smoke test verifies interpreter/source
    # selection without writing global helpers. Publication has separate tests.
    (skills / "global_installer.py").write_text(
        "DEFAULT_GLOBAL_SKILLS = ()\nGLOBAL_CLI_SKILL_DIRS = {}\n"
        "def detect_global_skill_clis(**kwargs): return []\n"
        "def sync_global_skills(**kwargs): raise AssertionError('unexpected publication')\n"
        "def _trees_equal(source, destination): return True\n"
    )
    metadata = user_site / "cafe_engine-1.1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: cafe-engine\nVersion: 1.1.0\n")
    result = subprocess.run(
        [sys.executable, "-I", "-c", SYNC_INSTALLED_HELPERS, "1.1.0", str(user_site)],
        env=environment, cwd=checkout, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["installed_version"] == "1.1.0"
    assert receipt["source_root"] == str(package / "data" / "skills")
    assert receipt["post_change_verified"] is True


@pytest.fixture
def installed_helper_bundle(tmp_path, monkeypatch):
    """Use real staged publication with an installed bundle and a stale source decoy."""
    import cafe
    from cafe.skills import global_installer

    package = tmp_path / "installed" / "cafe"
    source = package / "data" / "skills"
    home = tmp_path / "user"
    for name in global_installer.DEFAULT_GLOBAL_SKILLS:
        folder = source / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\nnew release\n"
        )
    monkeypatch.setattr(cafe, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr("importlib.metadata.version", lambda name: "1.1.0")
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    monkeypatch.setattr(global_installer, "_default_source_root", lambda: tmp_path / "stale")
    monkeypatch.setattr(global_installer, "detect_global_skill_clis", lambda **kw: ["claude"])
    monkeypatch.setattr("sys.argv", ["-c", "1.1.0"])
    old = home / ".codex" / "skills" / "use-cafe-workflow"
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text("old release")
    custom = home / ".codex" / "skills" / "my-skill"
    custom.mkdir()
    (custom / "SKILL.md").write_text("user owned")
    return source, home


def test_update_refreshes_installed_and_detected_clis_from_new_bundle(
    installed_helper_bundle, capsys,
):
    from cafe.skills.global_installer import DEFAULT_GLOBAL_SKILLS

    source, home = installed_helper_bundle
    with pytest.raises(SystemExit) as result:
        exec(SYNC_INSTALLED_HELPERS, {})
    assert result.value.code == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["clis"] == ["claude", "codex"]
    assert receipt["source_root"] == str(source)
    assert receipt["post_change_verified"] is True
    for cli in ("claude", "codex"):
        for name in DEFAULT_GLOBAL_SKILLS:
            assert (home / f".{cli}" / "skills" / name / "SKILL.md").read_bytes() == (
                source / name / "SKILL.md"
            ).read_bytes()
    assert (home / ".codex/skills/my-skill/SKILL.md").read_text() == "user owned"
    assert not (home / ".gemini").exists()
    with pytest.raises(SystemExit) as again:
        exec(SYNC_INSTALLED_HELPERS, {})
    assert again.value.code == 0
    assert {r["status"] for r in json.loads(capsys.readouterr().out)["results"]} == {"unchanged"}


def test_update_helper_batch_failure_restores_previous_copy(
    installed_helper_bundle, monkeypatch, capsys,
):
    from cafe.skills import global_installer

    _source, home = installed_helper_bundle
    publish = global_installer._publish_staged_replacement

    def fail(operation):
        if operation.cli == "codex" and operation.skill == "use-cafe-workflow":
            raise OSError("locked destination")
        return publish(operation)

    monkeypatch.setattr(global_installer, "_publish_staged_replacement", fail)
    with pytest.raises(SystemExit) as result:
        exec(SYNC_INSTALLED_HELPERS, {})
    assert result.value.code == 1
    assert json.loads(capsys.readouterr().out)["post_change_verified"] is False
    assert (home / ".codex/skills/use-cafe-workflow/SKILL.md").read_text() == "old release"
    assert not (home / ".claude/skills/use-cafe-workflow").exists()


def test_update_helper_postcheck_detects_changed_destination(
    installed_helper_bundle, monkeypatch, capsys,
):
    from cafe.skills import global_installer

    sync = global_installer.sync_global_skills

    def change_after_sync(**kwargs):
        result = sync(**kwargs)
        (result.results[0].destination / "SKILL.md").write_text("changed after publication")
        return result

    monkeypatch.setattr(global_installer, "sync_global_skills", change_after_sync)
    with pytest.raises(SystemExit) as result:
        exec(SYNC_INSTALLED_HELPERS, {})
    assert result.value.code == 1
    assert json.loads(capsys.readouterr().out)["post_change_verified"] is False


def test_update_helper_checks_runtime_identity_before_writes(
    installed_helper_bundle, monkeypatch,
):
    _source, home = installed_helper_bundle
    monkeypatch.setattr("sys.argv", ["-c", "wrong-version"])
    with pytest.raises(SystemExit, match="approved version"):
        exec(SYNC_INSTALLED_HELPERS, {})
    assert (home / ".codex/skills/use-cafe-workflow/SKILL.md").read_text() == "old release"
    assert not (home / ".claude").exists()

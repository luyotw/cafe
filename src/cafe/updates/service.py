"""Read-only runtime update checks with content-bound approval."""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import json
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Optional, Sequence

from packaging.version import InvalidVersion, Version


class UpdateApplyError(RuntimeError):
    """Raised when an update approval is absent, stale, or cannot be applied."""


@dataclass(frozen=True)
class LatestRelease:
    """One stable CAFE release and its exact source installation target."""

    version: str
    tag: str
    release_url: str
    install_url: str


@dataclass(frozen=True)
class UpdateCheckResult:
    """Bounded result returned by the trusted update service."""

    status: str
    installed_version: Optional[str]
    latest_version: Optional[str]
    release_url: Optional[str]
    token: Optional[str]
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Optional[str]]:
        return asdict(self)


def _installed_cafe_version() -> str:
    return importlib.metadata.version("cafe-engine")


def _latest_github_release() -> LatestRelease:
    request = urllib.request.Request(
        "https://api.github.com/repos/luyotw/cafe/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "cafe-engine-update-check",
        },
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub latest release response must be an object")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("GitHub latest release is not a stable published release")

    tag = payload.get("tag_name")
    release_url = payload.get("html_url")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("GitHub latest release has an invalid tag")
    version = tag[1:]
    Version(version)

    expected_release_url = f"https://github.com/luyotw/cafe/releases/tag/{tag}"
    if release_url != expected_release_url:
        raise ValueError("GitHub latest release URL does not match its tag")

    return LatestRelease(
        version=version,
        tag=tag,
        release_url=release_url,
        install_url=f"https://github.com/luyotw/cafe/archive/refs/tags/{tag}.tar.gz",
    )


def _run_pip(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=120,
    )


class UpdateService:
    """Compare and apply one exact GitHub release after explicit approval."""

    TOKEN_SCHEMA = 2

    def __init__(
        self,
        *,
        installed_version: Callable[[], str] = _installed_cafe_version,
        latest_release: Callable[[], LatestRelease] = _latest_github_release,
        runner: Callable[[Sequence[str]], object] = _run_pip,
        python_executable: str = sys.executable,
    ) -> None:
        self._installed_version = installed_version
        self._latest_release = latest_release
        self._runner = runner
        self._python_executable = python_executable

    @classmethod
    def _token(cls, installed: str, release: LatestRelease) -> str:
        payload = json.dumps(
            {
                "schema": cls.TOKEN_SCHEMA,
                "package": "github:luyotw/cafe",
                "installed": installed,
                "latest": release.version,
                "tag": release.tag,
                "release_url": release.release_url,
                "install_url": release.install_url,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _read_check(self) -> tuple[UpdateCheckResult, Optional[LatestRelease]]:
        installed: Optional[str] = None
        try:
            installed = self._installed_version()
            release = self._latest_release()
            installed_parsed = Version(installed)
            latest_parsed = Version(release.version)
        except (Exception, InvalidVersion) as exc:
            return (
                UpdateCheckResult(
                    status="unavailable",
                    installed_version=installed,
                    latest_version=None,
                    release_url=None,
                    token=None,
                    error=str(exc) or exc.__class__.__name__,
                ),
                None,
            )

        status = "update_available" if latest_parsed > installed_parsed else "current"
        return (
            UpdateCheckResult(
                status=status,
                installed_version=installed,
                latest_version=release.version,
                release_url=release.release_url,
                token=self._token(installed, release),
            ),
            release,
        )

    def check(self) -> UpdateCheckResult:
        """Read installed/GitHub release state without invoking an installer."""
        result, _release = self._read_check()
        return result

    def apply(self, approval_token: str) -> UpdateCheckResult:
        """Install the exact freshly compared release and return a post-check."""
        if not approval_token:
            raise UpdateApplyError("An update approval token is required")

        fresh, release = self._read_check()
        if (
            fresh.status != "update_available"
            or fresh.token is None
            or not hmac.compare_digest(approval_token, fresh.token)
            or fresh.latest_version is None
            or release is None
        ):
            raise UpdateApplyError("Update approval is stale or no approved update is available")

        approved_version = release.version
        command = [
            self._python_executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            release.install_url,
        ]
        try:
            result = self._runner(command)
        except Exception as exc:
            raise UpdateApplyError(f"Approved update failed: {exc}") from exc
        if getattr(result, "returncode", 0) != 0:
            detail = getattr(result, "stderr", "") or "installer returned a failure"
            raise UpdateApplyError(f"Approved update failed: {detail}")

        post_check = self.check()
        if post_check.installed_version != approved_version:
            raise UpdateApplyError(
                "Required post-update check did not observe the approved version"
            )
        return post_check

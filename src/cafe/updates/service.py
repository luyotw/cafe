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


def _latest_pypi_release() -> tuple[str, str]:
    with urllib.request.urlopen(
        "https://pypi.org/pypi/cafe-engine/json", timeout=2
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = str(payload["info"]["version"])
    return version, f"https://pypi.org/project/cafe-engine/{version}/"


def _run_pip(command: Sequence[str]):
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=120,
    )


class UpdateService:
    """Compare and apply one exact cafe-engine release after explicit approval."""

    TOKEN_SCHEMA = 1

    def __init__(
        self,
        *,
        installed_version: Callable[[], str] = _installed_cafe_version,
        latest_release: Callable[[], tuple[str, str]] = _latest_pypi_release,
        runner: Callable[[Sequence[str]], object] = _run_pip,
        python_executable: str = sys.executable,
    ) -> None:
        self._installed_version = installed_version
        self._latest_release = latest_release
        self._runner = runner
        self._python_executable = python_executable

    @classmethod
    def _token(cls, installed: str, latest: str, release_url: str) -> str:
        payload = json.dumps(
            {
                "schema": cls.TOKEN_SCHEMA,
                "package": "cafe-engine",
                "installed": installed,
                "latest": latest,
                "release_url": release_url,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def check(self) -> UpdateCheckResult:
        """Read installed/PyPI state without invoking an installer."""
        installed: Optional[str] = None
        try:
            installed = self._installed_version()
            latest, release_url = self._latest_release()
            installed_parsed = Version(installed)
            latest_parsed = Version(latest)
        except (Exception, InvalidVersion) as exc:
            return UpdateCheckResult(
                status="unavailable",
                installed_version=installed,
                latest_version=None,
                release_url=None,
                token=None,
                error=str(exc) or exc.__class__.__name__,
            )

        status = "update_available" if latest_parsed > installed_parsed else "current"
        return UpdateCheckResult(
            status=status,
            installed_version=installed,
            latest_version=latest,
            release_url=release_url,
            token=self._token(installed, latest, release_url),
        )

    def apply(self, approval_token: str) -> UpdateCheckResult:
        """Install the exact freshly compared release and return a post-check."""
        if not approval_token:
            raise UpdateApplyError("An update approval token is required")

        fresh = self.check()
        if (
            fresh.status != "update_available"
            or fresh.token is None
            or not hmac.compare_digest(approval_token, fresh.token)
            or fresh.latest_version is None
        ):
            raise UpdateApplyError(
                "Update approval is stale or no approved update is available"
            )

        approved_version = fresh.latest_version
        command = [
            self._python_executable,
            "-m",
            "pip",
            "install",
            f"cafe-engine=={approved_version}",
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

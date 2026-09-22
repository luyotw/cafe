#!/usr/bin/env python3
"""Inventory repository CI/CD configuration for a kickoff closeout proposal.

This is deliberately read-only and conservative.  It reports only known file
paths and coarse trigger/action tokens; it never treats a pipeline definition
as authority to merge, deploy, release, or delete anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


_SINGLE_CONFIGS = {
    ".gitlab-ci.yml": "gitlab_ci",
    "Jenkinsfile": "jenkins",
    "bitbucket-pipelines.yml": "bitbucket_pipelines",
    "netlify.toml": "netlify",
    "render.yaml": "render",
    "fly.toml": "fly",
    "railway.toml": "railway",
    "vercel.json": "vercel",
}
_GLOB_CONFIGS = {
    "azure-pipelines*.yml": "azure_pipelines",
    "azure-pipelines*.yaml": "azure_pipelines",
    "Jenkinsfile.*": "jenkins",
}
_TOKEN_PATTERNS = {
    "pull_request": re.compile(r"\bpull[_ -]?request\b", re.IGNORECASE),
    "merge_request": re.compile(r"\bmerge[_ -]?request\b", re.IGNORECASE),
    "push": re.compile(r"\bpush\b", re.IGNORECASE),
    "deploy": re.compile(r"\bdeploy(?:ment)?\b", re.IGNORECASE),
    "release": re.compile(r"\brelease\b", re.IGNORECASE),
    "publish": re.compile(r"\bpublish\b", re.IGNORECASE),
}


def _regular_files(project_root: Path) -> list[tuple[Path, str]]:
    """Return known CI/CD files without following symlinks outside the project."""
    found: dict[Path, str] = {}
    for relative, system in _SINGLE_CONFIGS.items():
        candidate = project_root / relative
        if candidate.exists() and not candidate.is_symlink():
            if not candidate.is_file():
                raise ValueError(f"CI/CD configuration is not a regular file: {relative}")
            found[candidate] = system
    for pattern, system in _GLOB_CONFIGS.items():
        for candidate in project_root.glob(pattern):
            if candidate.is_symlink():
                continue
            if candidate.is_file():
                found[candidate] = system
    github_workflows = project_root / ".github" / "workflows"
    if github_workflows.exists() and not github_workflows.is_symlink():
        if not github_workflows.is_dir():
            raise ValueError("GitHub Actions workflow path is not a directory")
        for pattern in ("*.yml", "*.yaml"):
            for candidate in github_workflows.glob(pattern):
                if candidate.is_symlink():
                    continue
                if candidate.is_file():
                    found[candidate] = "github_actions"
    circle = project_root / ".circleci" / "config.yml"
    if circle.exists() and not circle.is_symlink():
        if not circle.is_file():
            raise ValueError("CircleCI configuration is not a regular file")
        found[circle] = "circleci"
    buildkite = project_root / ".buildkite" / "pipeline.yml"
    if buildkite.exists() and not buildkite.is_symlink():
        if not buildkite.is_file():
            raise ValueError("Buildkite configuration is not a regular file")
        found[buildkite] = "buildkite"
    return sorted(found.items(), key=lambda item: item[0].as_posix())


def _signals(text: str) -> list[str]:
    return [name for name, pattern in _TOKEN_PATTERNS.items() if pattern.search(text)]


def infer_closeout_scope(project_root: Path) -> dict[str, Any]:
    """Return deterministic, non-authoritative CI/CD closeout suggestions."""
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {project_root}")

    digest = hashlib.sha256(b"cafe-closeout-inference-v1\n")
    configurations: list[dict[str, Any]] = []
    observed: set[str] = set()
    for path, system in _regular_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"CI/CD configuration is unreadable: {relative}") from exc
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        text = payload.decode("utf-8", errors="replace")
        signals = _signals(text)
        observed.update(signals)
        configurations.append({"path": relative, "system": system, "signals": signals})

    if {"pull_request", "merge_request"} & observed:
        deliver_scope = [
            "publish_pull_request",
            "wait_for_ci_checks",
            "merge_pull_request",
        ]
    elif "push" in observed:
        deliver_scope = ["publish_branch", "wait_for_ci_checks"]
    else:
        deliver_scope = ["manual_delivery_handoff"]
    cleanup_scope = [
        "archive_cafe_issue_after_integration",
        "remove_feature_worktree",
        "delete_feature_branch",
    ]
    requires_confirmation = ["merge_pull_request"]
    for action in ("deploy", "release", "publish"):
        if action in observed:
            requires_confirmation.append(action)

    return {
        "schema_version": 1,
        "fingerprint_sha256": digest.hexdigest(),
        "configurations": configurations,
        "suggested_deliver_scope": deliver_scope,
        "suggested_cleanup_scope": cleanup_scope,
        "requires_explicit_confirmation": requires_confirmation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect repository CI/CD files for a non-authoritative closeout proposal."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        print(json.dumps(infer_closeout_scope(args.project_root), sort_keys=True))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

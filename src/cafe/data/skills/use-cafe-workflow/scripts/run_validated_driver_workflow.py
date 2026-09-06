#!/usr/bin/env python3
"""Validate a Driver contract projection immediately before generic execution."""

from __future__ import annotations

import argparse
import hashlib
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping

import yaml

from validate_driver_entry import _mapping_json, validate_entry


MAX_DERIVED_INPUT_BYTES = 256 * 1024


def _read_yaml_mapping(path: Path, *, label: str) -> tuple[Mapping[str, Any], str]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} is unsafe")
    if metadata.st_size > MAX_DERIVED_INPUT_BYTES:
        raise ValueError(f"{label} exceeds the maximum bounded size")
    with path.open("rb") as handle:
        content = handle.read(MAX_DERIVED_INPUT_BYTES + 1)
    if len(content) > MAX_DERIVED_INPUT_BYTES:
        raise ValueError(f"{label} exceeds the maximum bounded size")
    try:
        document = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(document, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return document, hashlib.sha256(content).hexdigest()


def _verify_generic_projection(
    projection: Mapping[str, Any], *, issue_config: Path, phase_config: Path
) -> tuple[str, str]:
    """Prove generic CAFE will consume the exact validated narrow projection."""
    generic = projection.get("generic_inputs")
    if not isinstance(generic, Mapping):
        raise ValueError("validated Driver projection has no generic inputs")
    issue, issue_digest = _read_yaml_mapping(issue_config, label="derived issue configuration")
    if issue.get("playbook_id") != generic.get("playbook_id"):
        raise ValueError("derived issue configuration does not match the Driver playbook")
    if "pr_auto_create" in generic:
        pr = issue.get("pr")
        if not isinstance(pr, Mapping) or pr.get("auto_create") is not generic["pr_auto_create"]:
            raise ValueError("derived issue PR choice does not match the Driver contract")
    phase, phase_digest = _read_yaml_mapping(phase_config, label="derived phase configuration")
    chains = generic.get("phase_chains")
    if not isinstance(chains, Mapping):
        raise ValueError("validated Driver projection has invalid phase chains")
    for step, raw_chain in chains.items():
        if not isinstance(step, str) or not isinstance(raw_chain, list):
            raise ValueError("validated Driver projection has invalid phase chains")
        expected: list[tuple[str, str]] = []
        for entry in raw_chain:
            if not isinstance(entry, Mapping):
                raise ValueError("validated Driver projection has invalid phase chains")
            cli, model = entry.get("cli"), entry.get("model")
            if not isinstance(cli, str) or not isinstance(model, str):
                raise ValueError("validated Driver projection has invalid phase chains")
            expected.append((cli, model))
        configured_step = phase.get(step)
        configured_chain = (
            configured_step.get("clis") if isinstance(configured_step, Mapping) else None
        )
        if not isinstance(configured_chain, list):
            raise ValueError(f"derived phase chain for '{step}' is missing")
        actual: list[tuple[str, str]] = []
        for entry in configured_chain:
            if not isinstance(entry, Mapping):
                raise ValueError(f"derived phase chain for '{step}' is invalid")
            cli, model = entry.get("cli"), entry.get("model")
            if not isinstance(cli, str) or not isinstance(model, str):
                raise ValueError(f"derived phase chain for '{step}' is invalid")
            actual.append((cli, model))
        if actual != expected:
            raise ValueError(f"derived phase chain for '{step}' does not match the Driver contract")
    return issue_digest, phase_digest


def _canonical_generic_paths(issue_dir: Path, *, issue_name: str) -> tuple[Path, Path, Path]:
    """Return the ambient generic inputs that the launched CAFE process will read."""
    if (
        issue_dir.name != issue_name
        or issue_dir.parent.name != "issues"
        or issue_dir.parent.parent.name != ".cafe"
    ):
        raise ValueError("Driver issue directory is not the canonical generic workflow location")
    project_root = issue_dir.parent.parent.parent
    return project_root, issue_dir / "issue.yaml", project_root / ".cafe" / "phases.yaml"


def _absolute_path(path: Path) -> Path:
    """Keep caller-supplied paths lexical so a symlink cannot hide from validation."""
    return path if path.is_absolute() else Path.cwd() / path


def _reject_symlink_ancestors(path: Path, *, label: str) -> None:
    """Ensure generic execution cannot consume a projection through an alias."""
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"{label} must not traverse a symlink")


def run_validated_workflow(args: argparse.Namespace) -> int:
    issue_dir = _absolute_path(args.issue_dir)
    _reject_symlink_ancestors(issue_dir, label="Driver issue directory")
    project_root, expected_issue_config, expected_phase_config = _canonical_generic_paths(
        issue_dir, issue_name=args.issue_name
    )
    issue_config = _absolute_path(args.issue_config)
    phase_config = _absolute_path(args.phase_config)
    _reject_symlink_ancestors(issue_config, label="Driver issue configuration")
    _reject_symlink_ancestors(phase_config, label="Driver phase configuration")
    if issue_config != expected_issue_config:
        raise ValueError(
            "Driver launcher must validate the issue configuration generic CAFE will use"
        )
    if phase_config != expected_phase_config:
        raise ValueError(
            "Driver launcher must validate the phase configuration generic CAFE will use"
        )
    projection = validate_entry(
        issue_dir=issue_dir,
        issue_name=args.issue_name,
        workflow_id=args.workflow_id,
        fresh_facts=args.fresh_facts,
    )
    issue_digest, phase_digest = _verify_generic_projection(
        projection,
        issue_config=expected_issue_config,
        phase_config=expected_phase_config,
    )
    command = [
        "cafe",
        "workflow",
        "--issue",
        args.issue_name,
        "--execute",
        "--mute-agent-output",
        "--expected-issue-config-sha256",
        issue_digest,
        "--expected-phase-config-sha256",
        phase_digest,
    ]
    if args.background:
        command.append("--background")
    if args.on_workflow_event:
        command.extend(["--on-workflow-event", args.on_workflow_event])
    result = subprocess.run(command, check=False, cwd=project_root).returncode
    current_issue_digest = _read_yaml_mapping(
        expected_issue_config, label="derived issue configuration"
    )[1]
    current_phase_digest = _read_yaml_mapping(
        expected_phase_config, label="derived phase configuration"
    )[1]
    if (current_issue_digest, current_phase_digest) != (issue_digest, phase_digest):
        raise ValueError("validated generic inputs changed during generic launch")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Driver projection and launch generic CAFE without Driver imports."
    )
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--fresh-facts", type=_mapping_json, required=True)
    parser.add_argument("--issue-config", type=Path, required=True)
    parser.add_argument("--phase-config", type=Path, required=True)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--on-workflow-event")
    args = parser.parse_args()
    try:
        return run_validated_workflow(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

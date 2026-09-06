#!/usr/bin/env python3
"""Validate a Driver contract projection immediately before generic execution."""

from __future__ import annotations

import argparse
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping

import yaml

from cafe.utils.phase_config import load_phase_step_model

from validate_driver_entry import _mapping_json, validate_entry


MAX_DERIVED_INPUT_BYTES = 256 * 1024


def _read_yaml_mapping(path: Path, *, label: str) -> Mapping[str, Any]:
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
    return document


def _verify_generic_projection(
    projection: Mapping[str, Any], *, issue_config: Path, phase_config: Path
) -> None:
    """Prove generic CAFE will consume the exact validated narrow projection."""
    generic = projection.get("generic_inputs")
    if not isinstance(generic, Mapping):
        raise ValueError("validated Driver projection has no generic inputs")
    issue = _read_yaml_mapping(issue_config, label="derived issue configuration")
    if issue.get("playbook_id") != generic.get("playbook_id"):
        raise ValueError("derived issue configuration does not match the Driver playbook")
    if "pr_auto_create" in generic:
        pr = issue.get("pr")
        if not isinstance(pr, Mapping) or pr.get("auto_create") is not generic["pr_auto_create"]:
            raise ValueError("derived issue PR choice does not match the Driver contract")
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
        resolved = load_phase_step_model(step_name=step, local_path=phase_config)
        if list(resolved.clis) != expected:
            raise ValueError(f"derived phase chain for '{step}' does not match the Driver contract")


def run_validated_workflow(args: argparse.Namespace) -> int:
    issue_dir = args.issue_dir.resolve()
    projection = validate_entry(
        issue_dir=issue_dir,
        issue_name=args.issue_name,
        workflow_id=args.workflow_id,
        fresh_facts=args.fresh_facts,
    )
    _verify_generic_projection(
        projection,
        issue_config=args.issue_config.resolve(),
        phase_config=args.phase_config.resolve(),
    )
    command = ["cafe", "workflow", "--issue", args.issue_name, "--execute", "--mute-agent-output"]
    if args.background:
        command.append("--background")
    if args.on_workflow_event:
        command.extend(["--on-workflow-event", args.on_workflow_event])
    return subprocess.run(command, check=False).returncode


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

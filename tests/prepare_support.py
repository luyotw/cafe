"""Explicit delivery choices for setup-only tests of bundled prepare journeys."""

from pathlib import Path

import yaml


def with_delivery_choices(arguments):
    args = list(arguments)
    if not args or args[0] != "prepare":
        return args
    playbook = "standard"
    config = Path.cwd() / ".cafe/config.yaml"
    if config.is_file():
        data = yaml.safe_load(config.read_text())
        if isinstance(data, dict):
            playbook = data.get("playbook", playbook)
    for index, value in enumerate(args):
        if value == "--playbook":
            playbook = args[index + 1]
        elif value.startswith("--playbook="):
            playbook = value.split("=", 1)[1]
    adopting = {
        "bug",
        "direct",
        "direct-qa",
        "direct-subagent-review",
        "hotfix",
        "simple",
        "standard",
        "standard-qa",
        "subagent-flow",
        "subagent-flow-qa",
        "tdd",
        "tdd-qa",
    }
    if playbook not in adopting or any("deliver.mode=" in str(value) for value in args):
        return args
    destination = Path.cwd().parent / (Path.cwd().name + "-delivery-target")
    for value in (
        "deliver.mode=local",
        "deliver.target_branch=main",
        "deliver.strategy=ff-only",
        f"deliver.destination={destination}",
        'deliver.verification={"not_required_reason":"Explicit setup-only test scope"}',
    ):
        args.extend(["--set", value])
    return args

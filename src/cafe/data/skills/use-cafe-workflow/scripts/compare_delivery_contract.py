#!/usr/bin/env python3
"""Build a grounded comparison packet and check a Driver-authored assessment.

Semantic reading belongs to the Driver. This helper checks authority, exhaustive
evidence and freshness; it never answers a HumanTask or advances a workflow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cafe.driver import DriverEntryRequest
from cafe.driver.delivery_comparison import (
    comparison_packet,
    decide,
    evidence_sources,
)
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader


def _read_artifact(path: str) -> str:
    source = Path(path)
    if source.is_symlink():
        raise ValueError("comparison artifact exceeds the supported source bound")
    with source.open("rb") as handle:
        content = handle.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        raise ValueError("comparison artifact exceeds the supported source bound")
    return content.decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--assessment", type=Path)
    args = parser.parse_args()
    try:
        context = json.loads(args.context.read_text(encoding="utf-8"))
        project = Path(context["project_root"])
        model = PlaybookLoader(project_root=project).load_model(context["playbook_id"]).model
        paths = evidence_sources(
            model.steps[context["boundary"]["step"]], context["artifact_paths"]
        )
        packet = comparison_packet(
            entry=DriverEntryRequest(
                Path(context["issue_dir"]),
                context["issue_name"],
                context["workflow_id"],
                context["fresh_facts"],
            ),
            model=model,
            skill_loader=SkillLoader(project_root=project),
            boundary=context["boundary"],
            artifacts={name: _read_artifact(path) for name, path in paths.items()},
        )
        output = (
            decide(packet, json.loads(args.assessment.read_text(encoding="utf-8")))
            if args.assessment
            else packet
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"decision": "user_handoff", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only JSON entry for Driver task authority and evidence diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[5]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from cafe.driver.task_inspection import inspect_task_authority  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--assessment", type=Path)
    parser.add_argument("--json", action="store_true", required=True)
    args = parser.parse_args()
    assessment = None
    if args.assessment:
        if args.assessment.is_symlink() or args.assessment.stat().st_size > 256 * 1024:
            raise ValueError("assessment file is unsafe or oversized")
        assessment = json.loads(args.assessment.read_text(encoding="utf-8"))
        if not isinstance(assessment, dict):
            raise ValueError("assessment must be a JSON object")
    result = inspect_task_authority(
        args.issue_dir,
        args.task_id,
        response=assessment.get("response") if assessment else None,
        evidence=assessment.get("evidence") if assessment else None,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

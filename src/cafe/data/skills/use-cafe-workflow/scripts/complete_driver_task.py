#!/usr/bin/env python3
"""Complete a Driver-owned task after an atomic authority recheck."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[5]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from cafe.driver.task_completion import complete_driver_task  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--assessment", required=True, type=Path)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--sources-sha256", required=True)
    parser.add_argument("--json", action="store_true", required=True)
    args = parser.parse_args()
    if args.assessment.is_symlink() or not args.assessment.is_file():
        raise ValueError("assessment file is unsafe or oversized")
    with args.assessment.open("rb") as handle:
        content = handle.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        raise ValueError("assessment file is unsafe or oversized")
    assessment = json.loads(content.decode("utf-8"))
    if (
        not isinstance(assessment, dict)
        or not isinstance(assessment.get("response"), dict)
        or not isinstance(assessment.get("evidence"), dict)
    ):
        raise ValueError("assessment requires response and evidence objects")
    result = complete_driver_task(
        args.issue_dir,
        args.task_id,
        response=assessment["response"],
        evidence=assessment["evidence"],
        contract_sha256=args.contract_sha256,
        sources_sha256=args.sources_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

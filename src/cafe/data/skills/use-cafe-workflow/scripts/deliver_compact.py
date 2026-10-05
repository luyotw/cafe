#!/usr/bin/env python3
"""Publish the confirmed compact PR through the host capability boundary."""
import argparse
import json
from pathlib import Path
from cafe.manager.delivery import publish_compact_pr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pr-output", type=Path, required=True)
    parser.add_argument("--approval-task-id")
    parser.add_argument("--correlation-id")
    args = parser.parse_args()
    try:
        result = publish_compact_pr(args.issue_dir, args.root, args.pr_output,
            approval_task_id=args.approval_task_id, correlation_id=args.correlation_id)
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"delivered": False, "error": str(exc)}))
        return 2
    print(json.dumps(result))
    return 0 if result.get("delivered") else 2


if __name__ == "__main__":
    raise SystemExit(main())

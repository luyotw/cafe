#!/usr/bin/env python3
"""Resolve current authority and issue a bounded native invocation checkpoint."""

import argparse
import json
from pathlib import Path

from cafe.core.execution_checkpoints import checkpoint
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.manager.file_scope import execution_scope_projection


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--boundary", choices=("before_review", "resume", "before_delivery"), required=True
    )
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--parent-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        context = execution_scope_projection(args.issue_dir, args.root)
        if context is None:
            raise ValueError("checkpoint requires confirmed compact authority")
        receipt = checkpoint(
            context, args.boundary, round_id=args.round_id, parent_id=args.parent_id
        )
        current = execution_scope_projection(args.issue_dir, args.root)
        if current != context:
            raise ValueError("authority changed during checkpoint")
        atomic_write_bytes(args.output, canonical_json(receipt))
        print(json.dumps(receipt))
        return 0 if receipt["passed"] else 2
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"passed": False, "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

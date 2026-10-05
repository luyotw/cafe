#!/usr/bin/env python3
"""Record release stages without changing the checked command's exit status."""

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def write_report(path, payload):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("clock", "init", "stage", "finish"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--started", type=float)
    parser.add_argument("--status", type=int)
    parser.add_argument("--name")
    parser.add_argument("--latest", type=Path)
    args = parser.parse_args()
    if args.operation == "clock":
        print(time.monotonic())
        return
    if args.report is None:
        parser.error("--report is required")
    if args.operation == "init":
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True
        )
        write_report(args.report, {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "revision": result.stdout.strip() if result.returncode == 0 else None,
            "report_dir": str(args.report.parent),
            "coverage_workers": os.environ.get("CAFE_TEST_WORKERS", "8"),
            "extended_workers": os.environ.get("CAFE_EXTENDED_TEST_WORKERS", "4"),
            "stages": [],
        })
        return
    if args.started is None or args.status is None:
        parser.error("--started and --status are required")
    data = json.loads(args.report.read_text())
    elapsed = round(time.monotonic() - args.started, 3)
    if args.operation == "stage":
        if args.name is None:
            parser.error("--name is required for a stage")
        data["stages"].append({
            "name": args.name, "elapsed_seconds": elapsed, "exit_code": args.status,
        })
        print(f"Stage {args.name}: {elapsed:.2f}s (exit {args.status})")
    else:
        data.update(elapsed_seconds=elapsed, exit_code=args.status)
        print(f"Release checks elapsed: {elapsed:.2f}s; report: {args.report}")
    write_report(args.report, data)
    if args.operation == "finish" and args.latest is not None:
        write_report(args.latest, data)


if __name__ == "__main__":
    main()

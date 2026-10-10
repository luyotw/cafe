#!/usr/bin/env python3
"""Inspect Manager closeout selection and its verified delivery prerequisites."""

import argparse
import json
from pathlib import Path

from cafe.manager._store import load_contract


def inspect(issue_dir, workflow_id, project_root=None):
    from cafe.manager.closeout import inspect_closeout
    from cafe.manager.costs import _archive
    archived = False
    if project_root is not None:
        contract, _ = load_contract(issue_dir, workflow_id=workflow_id)
        archived = Path(issue_dir).absolute() == _archive(
            project_root, contract["identity"]["issue_name"]).absolute()
    return inspect_closeout(issue_dir, workflow_id, archived=archived)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.issue_dir, args.workflow_id, args.project_root), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

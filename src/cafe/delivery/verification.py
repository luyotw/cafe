"""Read-only post-integration verification using existing GitHub Actions results."""

import re
import time
from urllib.parse import quote, urlencode

from cafe.delivery.operations import Commands, OperationError


def observe_delivery(snapshot, commit, *, timeout=30):
    plan = snapshot.proposal.verification
    if plan is None:
        return {"state": "missing", "error": "verification_scope_requires_fresh_pr_review"}
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        return {"state": "missing", "error": "integration_commit_unavailable"}
    if not plan.workflows:
        return {"state": "not_required", "reason": plan.not_required_reason, "commit": commit}
    commands = Commands(timeout)
    p = snapshot.proposal
    results = []
    try:
        for required in plan.workflows:
            query = urlencode(
                {"head_sha": commit, "branch": p.target_branch, "event": "push", "per_page": 1}
            )
            data = commands.api(
                f"repos/{p.repository}/actions/workflows/{quote(required.path.rsplit('/', 1)[1])}"
                f"/runs?{query}"
            )
            runs = data["workflow_runs"]
            if not runs:
                results.append({"workflow": required.path, "state": "pending"})
                continue
            row = runs[0]
            # Query filters are not proof: reject unrelated commits, branches and PR runs.
            if (
                row["head_sha"] != commit
                or row["head_branch"] != p.target_branch
                or row["event"] != "push"
                or row["path"]
                not in {
                    required.path,
                    f"{required.path}@{p.target_branch}",
                    f"{required.path}@refs/heads/{p.target_branch}",
                }
                or row["repository"]["full_name"] != p.repository
            ):
                raise ValueError("workflow_identity_mismatch")
            run_id, attempt = row["id"], row["run_attempt"]
            if type(run_id) is not int or type(attempt) is not int or min(run_id, attempt) < 1:
                raise ValueError("invalid_workflow_run_identity")
            state = (
                "pending"
                if row["status"] != "completed"
                else ("succeeded" if row["conclusion"] == "success" else "failed")
            )
            result = {
                "workflow": required.path,
                "run_id": run_id,
                "attempt": attempt,
                "state": state,
                "url": f"https://github.com/{p.repository}/actions/runs/{run_id}",
            }
            if state == "succeeded":
                jobs_data = commands.api(
                    f"repos/{p.repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
                )
                jobs = jobs_data["jobs"]
                if jobs_data["total_count"] != len(jobs) or len(jobs) > 100:
                    raise ValueError("incomplete_workflow_jobs")
                for name, steps in required.jobs.items():
                    matching = [job for job in jobs if job["name"] == name]
                    if (
                        len(matching) != 1
                        or matching[0]["status"] != "completed"
                        or matching[0]["conclusion"] != "success"
                    ):
                        state = "failed"
                        result["error"] = f"required_job_unsuccessful:{name}"
                        break
                    for step in steps:
                        matches = [item for item in matching[0]["steps"] if item["name"] == step]
                        if (
                            len(matches) != 1
                            or matches[0]["status"] != "completed"
                            or matches[0]["conclusion"] != "success"
                        ):
                            state = "failed"
                            result["error"] = f"required_step_unsuccessful:{name}/{step}"
                            break
                result["state"] = state
            results.append(result)
        state = (
            "failed"
            if any(row["state"] == "failed" for row in results)
            else ("pending" if any(row["state"] == "pending" for row in results) else "succeeded")
        )
        return {"state": state, "commit": commit, "workflows": results}
    except (OperationError, OSError, ValueError, KeyError, TypeError) as exc:
        return {"state": "unknown", "commit": commit, "error": str(exc)[:256]}


def wait_for_delivery(snapshot, commit, *, timeout=900):
    """Wait without invoking an agent or repeating integration; stop on failure/uncertainty."""
    deadline = time.monotonic() + timeout
    while True:
        result = observe_delivery(
            snapshot, commit, timeout=max(1, min(30, deadline - time.monotonic()))
        )
        remaining = deadline - time.monotonic()
        if result["state"] != "pending" or remaining <= 0:
            return result
        time.sleep(min(10, remaining))


def validate_verification(snapshot, report):
    shown = report.get("verification")
    if not shown or shown.get("state") not in {"succeeded", "not_required"}:
        raise ValueError("required post-integration verification is incomplete")
    current = observe_delivery(snapshot, report["actions"]["integration"].get("commit"))
    if current != shown:
        raise ValueError("post-integration verification changed or cannot be verified")

"""Self-contained read-only GitHub Actions tool owned by this delivery skill."""
import json
import re
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import quote, urlencode


class ObservationError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class Commands:
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout

    def api(self, endpoint):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationError("query_timeout", True)
        try:
            result = subprocess.run(
                ["gh", "api", endpoint, "--method", "GET"],
                capture_output=True, text=True, timeout=remaining, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ObservationError("query_timeout", True) from exc
        except OSError as exc:
            raise ObservationError("github_tool_unavailable") from exc
        if result.returncode:
            error = result.stderr.lower()
            status = re.search(r"http (\d{3})", error)
            code = int(status[1]) if status else None
            retryable = (
                code == 429 or (code is not None and code >= 500)
                or "rate limit" in error
                or (code is None and any(word in error for word in
                    ("timeout", "connection", "network", "dial tcp", "no such host")))
            )
            raise ObservationError(f"github_query_failed:{code or 'transport'}", retryable)
        return json.loads(result.stdout)


def validate_workflows(options):
    workflows = options["workflows"]
    if not isinstance(workflows, list) or not 1 <= len(workflows) <= 10:
        raise ValueError("bounded_required_workflows_missing")
    paths = []
    for required in workflows:
        path, jobs = required["path"], required["jobs"]
        if not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", path):
            raise ValueError("invalid_workflow_path")
        if path in paths or not isinstance(jobs, dict) or not 1 <= len(jobs) <= 20:
            raise ValueError("invalid_required_jobs")
        paths.append(path)
        for name, steps in jobs.items():
            if (not name.strip() or len(name) > 256 or not isinstance(steps, list)
                    or len(steps) > 30 or len(set(steps)) != len(steps)
                    or any(not step.strip() or len(step) > 256 for step in steps)):
                raise ValueError("invalid_required_steps")
    return workflows


def observe(inputs, timeout=25):
    commit = inputs['commit']
    commands = Commands(timeout)
    p = SimpleNamespace(repository=inputs['repository'], target_branch=inputs['target_branch'])
    results = []
    try:
        for required in validate_workflows(inputs['options']):
            required = SimpleNamespace(**required)
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
        return {"state": state, "commit": commit, "evidence": results}
    except (ObservationError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {"state": "unknown", "commit": commit, "error": str(exc)[:256],
                "retryable": isinstance(exc, ObservationError) and exc.retryable}



if __name__ == "__main__":
    print(json.dumps(observe(json.load(sys.stdin)), sort_keys=True))

"""Self-contained read-only release/deployment observer for approved host execution."""

import json
import re
import subprocess
import sys
import time
from urllib.parse import quote, urlencode, urlsplit


class ObservationError(Exception):
    def __init__(self, message, retryable=False, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Commands:
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout

    def api(self, endpoint):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationError("query_timeout", True)
        try:
            result = subprocess.run(
                ["gh", "api", endpoint, "--hostname", "github.com", "--method", "GET"],
                capture_output=True,
                text=True,
                timeout=remaining,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ObservationError("query_timeout", True) from exc
        except OSError as exc:
            raise ObservationError("github_tool_unavailable") from exc
        if result.returncode:
            error = result.stderr.lower()
            match = re.search(r"http (\d{3})", error)
            status = int(match[1]) if match else None
            retryable = (
                status == 429
                or (status is not None and status >= 500)
                or "rate limit" in error
                or (
                    status is None
                    and any(
                        value in error
                        for value in (
                            "timeout",
                            "connection",
                            "network",
                            "dial tcp",
                            "no such host",
                        )
                    )
                )
            )
            raise ObservationError(
                f"github_query_failed:{status or 'transport'}", retryable, status
            )
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ObservationError("github_response_invalid") from exc


def optional_api(commands, endpoint):
    try:
        return commands.api(endpoint)
    except ObservationError as exc:
        if exc.status == 404:
            return None
        raise


def positive(value):
    if type(value) is not int or value < 1:
        raise ValueError("invalid_observation_id")
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("invalid_commit_identity")
    return value


def latest(rows):
    if not isinstance(rows, list) or len(rows) > 1:
        raise ValueError("invalid_latest_observation")
    return rows[0] if rows else None


def validate(inputs):
    if not isinstance(inputs, dict) or set(inputs) != {
        "repository",
        "target_branch",
        "commit",
        "options",
    }:
        raise ValueError("invalid_host_input")
    repository, branch, options = inputs["repository"], inputs["target_branch"], inputs["options"]
    if (
        not isinstance(repository, str)
        or len(repository) > 201
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository)
    ):
        raise ValueError("invalid_repository")
    if (
        not isinstance(branch, str)
        or not 1 <= len(branch) <= 256
        or branch.startswith("-")
        or any(ord(char) < 32 for char in branch)
    ):
        raise ValueError("invalid_target_branch")
    sha(inputs["commit"])
    if (
        not isinstance(options, dict)
        or set(options) != {"environment", "workflow", "deployment_job", "jobs"}
        or len(json.dumps(options).encode()) > 16384
    ):
        raise ValueError("invalid_release_observer_options")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", options["environment"]):
        raise ValueError("invalid_environment")
    if not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", options["workflow"]):
        raise ValueError("invalid_workflow_path")
    jobs = options["jobs"]
    if (
        not isinstance(jobs, dict)
        or not 1 <= len(jobs) <= 10
        or options["deployment_job"] not in jobs
    ):
        raise ValueError("invalid_required_jobs")
    for name, steps in jobs.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 256
            or not isinstance(steps, list)
            or not 1 <= len(steps) <= 20
            or any(
                not isinstance(step, str) or not step.strip() or len(step) > 256 for step in steps
            )
            or len(set(steps)) != len(steps)
        ):
            raise ValueError("invalid_required_steps")
    return repository, branch, options


def observe(inputs, timeout=25):
    commit = inputs.get("commit") if isinstance(inputs, dict) else None
    evidence = {}

    def finish(state, reason=None):
        if reason:
            evidence["reason"] = reason
        return {"state": state, "commit": commit, "evidence": evidence}

    try:
        repository, branch, options = validate(inputs)
        commands = Commands(timeout)
        prefix = f"repos/{repository}"
        evidence.update(
            repository=repository, target_branch=branch, environment=options["environment"]
        )
        query = urlencode({"environment": options["environment"], "per_page": 1})
        deployment = latest(commands.api(f"{prefix}/deployments?{query}"))
        if deployment is None:
            return finish("pending", "deployment_missing")
        deployment_id, deployed = positive(deployment["id"]), sha(deployment["sha"])
        if (
            deployment["environment"] != options["environment"]
            or deployment["repository_url"] != f"https://api.github.com/{prefix}"
        ):
            raise ValueError("deployment_identity_mismatch")
        evidence["deployment"] = {
            "id": deployment_id,
            "sha": deployed,
            "url": f"https://api.github.com/{prefix}/deployments/{deployment_id}",
        }
        relation = "identical"
        if deployed != commit:
            comparison = commands.api(f"{prefix}/compare/{commit}...{deployed}?per_page=1")
            if comparison["base_commit"]["sha"] != commit:
                raise ValueError("comparison_identity_mismatch")
            relation = comparison["status"]
            if relation in {"behind", "diverged"}:
                evidence["ancestry"] = {"base": commit, "head": deployed, "relation": relation}
                return finish("pending", "deployment_does_not_include_integration")
            if (
                relation != "ahead"
                or comparison["merge_base_commit"]["sha"] != commit
                or type(comparison["ahead_by"]) is not int
                or comparison["ahead_by"] < 1
                or comparison["behind_by"] != 0
            ):
                raise ValueError("comparison_ancestry_unproven")
        evidence["ancestry"] = {"base": commit, "head": deployed, "relation": relation}
        status = latest(commands.api(f"{prefix}/deployments/{deployment_id}/statuses?per_page=1"))
        if status is None:
            return finish("pending", "deployment_status_missing")
        if (
            status["environment"] != options["environment"]
            or status["deployment_url"] != evidence["deployment"]["url"]
        ):
            raise ValueError("deployment_status_identity_mismatch")
        evidence["deployment"].update(status_id=positive(status["id"]), state=status["state"])
        if status["state"] in {"error", "failure"}:
            return finish("failed", "deployment_failed")
        if status["state"] in {"queued", "pending", "in_progress", "inactive"}:
            return finish("pending", "deployment_not_successful_yet")
        if status["state"] != "success":
            raise ValueError("invalid_deployment_state")
        link = re.fullmatch(
            re.escape(f"https://github.com/{repository}/actions/runs/")
            + r"([1-9][0-9]*)(?:/attempts/([1-9][0-9]*))?/job/([1-9][0-9]*)",
            status.get("log_url", ""),
        )
        if not link:
            raise ValueError("deployment_workflow_link_missing_or_foreign")
        run_id, linked_job = int(link[1]), int(link[3])
        environment_url = status["environment_url"]
        url = urlsplit(environment_url)
        if (
            len(environment_url) > 2048
            or url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
        ):
            raise ValueError("invalid_deployment_environment_url")
        evidence["deployment"]["environment_url"] = environment_url
        tag = deployment["ref"]
        if not isinstance(tag, str) or not 1 <= len(tag) <= 256:
            raise ValueError("invalid_release_ref")
        if tag.startswith("refs/tags/"):
            tag = tag[len("refs/tags/") :]
        if not tag or any(ord(char) < 32 for char in tag):
            raise ValueError("invalid_release_tag")
        release = optional_api(commands, f"{prefix}/releases/tags/{quote(tag, safe='')}")
        if release is None:
            return finish("pending", "release_missing")
        if (
            release["tag_name"] != tag
            or release["draft"] is not False
            or not release["published_at"]
        ):
            raise ValueError("release_identity_mismatch")
        resolved = optional_api(commands, f"{prefix}/commits/{quote('tags/' + tag, safe='')}")
        if resolved is None:
            return finish("pending", "release_tag_missing")
        if sha(resolved["sha"]) != deployed:
            raise ValueError("release_tag_commit_mismatch")
        evidence["release"] = {
            "id": positive(release["id"]),
            "tag": tag,
            "version": tag[1:] if tag.startswith("v") else tag,
            "url": f"https://github.com/{repository}/releases/tag/{quote(tag, safe='')}",
        }
        run = optional_api(commands, f"{prefix}/actions/runs/{run_id}")
        if run is None:
            return finish("pending", "release_workflow_missing")
        if (
            positive(run["id"]) != run_id
            or run["repository"]["full_name"] != repository
            or run["head_sha"] != deployed
            or run["event"] != "release"
            or run["head_branch"] not in {tag, f"refs/tags/{tag}"}
            or run["path"]
            not in {
                options["workflow"],
                options["workflow"] + "@" + tag,
                options["workflow"] + "@refs/tags/" + tag,
            }
        ):
            raise ValueError("release_workflow_identity_mismatch")
        attempt = positive(run["run_attempt"])
        evidence["workflow"] = {
            "path": options["workflow"],
            "run_id": run_id,
            "attempt": attempt,
            "sha": deployed,
            "status": run["status"],
            "conclusion": run["conclusion"],
            "url": f"https://github.com/{repository}/actions/runs/{run_id}",
        }
        if link[2] and int(link[2]) != attempt:
            return finish("pending", "deployment_attempt_not_current")
        if run["status"] in {"queued", "waiting", "pending", "requested", "in_progress"}:
            return finish("pending", "release_workflow_running")
        if run["status"] != "completed":
            raise ValueError("invalid_workflow_state")
        if run["conclusion"] != "success":
            return finish("failed", "release_workflow_failed")
        job_data = optional_api(
            commands, f"{prefix}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        if job_data is None:
            return finish("pending", "release_jobs_missing")
        jobs = job_data["jobs"]
        if (
            not isinstance(jobs, list)
            or len(jobs) > 100
            or type(job_data["total_count"]) is not int
            or job_data["total_count"] != len(jobs)
        ):
            raise ValueError("incomplete_workflow_jobs")
        evidence["jobs"] = []
        for name in sorted(options["jobs"]):
            matches = [job for job in jobs if job["name"] == name]
            if len(matches) != 1:
                return finish("failed", "required_job_missing_or_duplicate")
            job = matches[0]
            if (
                job["run_id"] != run_id
                or job["run_attempt"] != attempt
                or job["head_sha"] != deployed
            ):
                raise ValueError("required_job_identity_mismatch")
            record = {
                "id": positive(job["id"]),
                "name": name,
                "status": job["status"],
                "conclusion": job["conclusion"],
                "steps": [],
            }
            evidence["jobs"].append(record)
            if name == options["deployment_job"] and job["id"] != linked_job:
                return finish("pending", "deployment_job_not_current")
            if job["status"] != "completed" or job["conclusion"] != "success":
                return finish("failed", "required_job_unsuccessful")
            for required in options["jobs"][name]:
                matches = [step for step in job["steps"] if step["name"] == required]
                if len(matches) != 1:
                    return finish("failed", "required_step_missing_or_duplicate")
                step = matches[0]
                record["steps"].append(
                    {
                        "number": positive(step["number"]),
                        "name": required,
                        "status": step["status"],
                        "conclusion": step["conclusion"],
                    }
                )
                if step["status"] != "completed" or step["conclusion"] != "success":
                    return finish("failed", "required_step_unsuccessful")
        return finish("succeeded")
    except (ObservationError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result = finish("unknown")
        result.update(
            error=str(exc)[:256], retryable=isinstance(exc, ObservationError) and exc.retryable
        )
        return result


if __name__ == "__main__":
    print(json.dumps(observe(json.load(sys.stdin)), sort_keys=True))

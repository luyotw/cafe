"""Shared Git endpoint identity for resolved delivery authority."""

import hashlib
from pathlib import Path
import subprocess


def git_text(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, timeout=20).stdout.strip()


def remote_configuration(root: Path, remote: str, route: str) -> dict:
    push = git_text(root, "remote", "get-url", "--push", "--all", remote).splitlines()
    if len(push) != 1 or not push[0]:
        raise ValueError("delivery requires one exact remote push endpoint")
    if route == "direct":
        return {"push": push[0]}
    fetch = git_text(root, "remote", "get-url", "--all", remote).splitlines()
    if len(fetch) != 1 or not fetch[0]:
        raise ValueError("publication requires one exact fetch endpoint")
    return {"fetch": fetch[0], "push": push[0]}


def configuration_identity(configuration: dict, route: str) -> str:
    if route == "direct":
        return hashlib.sha256(configuration["push"].encode()).hexdigest()
    from cafe.core.packet_io import canonical_json
    return hashlib.sha256(canonical_json(configuration)).hexdigest()


def remote_identity(root: Path, remote: str, route: str) -> str:
    return configuration_identity(remote_configuration(root, remote, route), route)


PUBLICATION_TARGET_FIELDS = ("push_url", "remote_identity", "source_branch", "head_oid")


def delivery_target(context):
    """Resolve one checked target; its identity and URL use the same observation."""
    endpoint = context["delivery_endpoint"]
    route = endpoint["route"]
    if route not in {"pr", "direct"}:
        raise ValueError("unsupported delivery route")
    root = Path(context["root"])
    configuration = remote_configuration(root, endpoint["remote"], route)
    if configuration_identity(configuration, route) != endpoint["remote_identity"]:
        raise ValueError("delivery remote endpoint changed")
    branch = endpoint["source_branch"] if route == "pr" else endpoint["branch"]
    if git_text(root, "symbolic-ref", "--short", "HEAD") != branch:
        raise ValueError("delivery source branch changed")
    return {"push_url": configuration["push"], "remote_identity": endpoint["remote_identity"],
            "source_branch": branch, "head_oid": git_text(root, "rev-parse", "HEAD")}


def require_delivery_target(context, target):
    current = delivery_target(context)
    if any(target.get(key) != value for key, value in current.items()):
        raise ValueError("delivery target changed since authorization")


def pinned_remote_command(operation, push_url, *args):
    """Consume an already resolved URL exactly once, despite Git URL rewrites."""
    import uuid
    alias = "cafe-pinned-" + uuid.uuid4().hex + ":"
    return ["git", "-c", f"url.{push_url}.insteadOf={alias}", operation, alias, *args]


def publication_target(context, args):
    endpoint = context["delivery_endpoint"]
    if (endpoint["route"] != "pr" or args.get("remote") != endpoint["remote"]
            or args.get("base") != endpoint["target_branch"]):
        raise ValueError("publication request differs from resolved delivery endpoint")
    target = delivery_target(context)
    if any(key in args and args[key] != value for key, value in target.items()):
        raise ValueError("publication target changed since authorization")
    return target


def bind_publication_request(context, request):
    args = request.get("args", {})
    return {**request, "args": {**args, **publication_target(context, args)}}


def require_publication_target(context, args):
    target = publication_target(context, args)
    if any(args.get(key) != value for key, value in target.items()):
        raise ValueError("publication requires the bound authorized target")

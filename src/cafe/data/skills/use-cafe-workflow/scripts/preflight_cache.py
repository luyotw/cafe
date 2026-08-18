#!/usr/bin/env python3
"""Cache successful model probes and deterministic CAFE fallback smoke tests."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_VERSION = 1
CANDIDATE_MAX_AGE_SECONDS = 24 * 60 * 60
FALLBACK_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MISS_EXIT_CODE = 3
SMOKE_PROTOCOL = "agent-manager-model-not-found-v1"
PROBE_MARKER = "CAFE_PREFLIGHT_OK"
PROBE_PROMPT = (
    f"Reply with exactly {PROBE_MARKER}. Do not inspect files, call tools, or make any changes."
)


class PreflightCacheError(ValueError):
    """Raised when cache evidence cannot be safely produced or consumed."""


def default_cache_file() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return base / "cafe" / "use-cafe-workflow" / "preflight-v1.json"


def _json_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_text(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _empty_cache() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_success": {},
        "fallback_success": {},
    }


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return _empty_cache()
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return _empty_cache()
    candidates = raw.get("candidate_success")
    fallbacks = raw.get("fallback_success")
    if not isinstance(candidates, dict) or not isinstance(fallbacks, dict):
        return _empty_cache()
    return raw


def _save_cache(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _cache_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except ImportError:
            pass
        os.close(descriptor)


def _cli_fingerprint(cli: str) -> dict[str, Any]:
    executable = shutil.which(cli)
    if executable is None:
        raise PreflightCacheError(f"CLI is not installed: {cli}")
    resolved = Path(executable).resolve()
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise PreflightCacheError(f"Cannot inspect CLI executable: {cli}") from exc
    try:
        version_result = subprocess.run(
            [str(resolved), "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        version_text = (version_result.stdout or version_result.stderr).strip()[:500]
        version_exit_code: int | None = version_result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        version_text = type(exc).__name__
        version_exit_code = None
    evidence = {
        "path": str(resolved),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "version_exit_code": version_exit_code,
        "version": version_text,
    }
    return {**evidence, "digest": _json_digest(evidence)}


def _candidate_key(cli: str, model: str, fingerprint: dict[str, Any]) -> str:
    return _json_digest(
        {
            "schema_version": SCHEMA_VERSION,
            "cli": cli,
            "model": model,
            "cli_fingerprint": fingerprint["digest"],
        }
    )


def candidate_check(
    *, cache_file: Path, cli: str, model: str, max_age_seconds: int, now: float
) -> tuple[bool, dict[str, Any]]:
    fingerprint = _cli_fingerprint(cli)
    key = _candidate_key(cli, model, fingerprint)
    with _cache_lock(cache_file):
        entry = _load_cache(cache_file)["candidate_success"].get(key)
    if not isinstance(entry, dict):
        return False, {
            "kind": "candidate",
            "status": "miss",
            "reason": "not_cached",
            "cli": cli,
            "model": model,
        }
    checked_at = entry.get("checked_at")
    if not isinstance(checked_at, (int, float)) or now - float(checked_at) > max_age_seconds:
        return False, {
            "kind": "candidate",
            "status": "miss",
            "reason": "expired",
            "cli": cli,
            "model": model,
        }
    return True, {
        "kind": "candidate",
        "status": "hit",
        "cli": cli,
        "model": model,
        "resolved_model": entry.get("resolved_model", model),
        "checked_at": entry.get("checked_at_text"),
        "expires_at": _utc_text(float(checked_at) + max_age_seconds),
    }


def candidate_record(
    *, cache_file: Path, cli: str, model: str, resolved_model: str | None, now: float
) -> dict[str, Any]:
    fingerprint = _cli_fingerprint(cli)
    canonical_model = (resolved_model or model).strip()
    if not canonical_model:
        raise PreflightCacheError("resolved model must not be empty")
    models = tuple(dict.fromkeys((model, canonical_model)))
    with _cache_lock(cache_file):
        document = _load_cache(cache_file)
        for exact_model in models:
            key = _candidate_key(cli, exact_model, fingerprint)
            document["candidate_success"][key] = {
                "cli": cli,
                "model": exact_model,
                "requested_model": model,
                "resolved_model": canonical_model,
                "cli_fingerprint": fingerprint,
                "checked_at": now,
                "checked_at_text": _utc_text(now),
            }
        _save_cache(cache_file, document)
    return {
        "kind": "candidate",
        "status": "recorded",
        "cli": cli,
        "models": list(models),
        "resolved_model": canonical_model,
    }


def candidate_probe(
    *, cache_file: Path, cli: str, model: str, max_age_seconds: int, now: float
) -> dict[str, Any]:
    hit, cached = candidate_check(
        cache_file=cache_file,
        cli=cli,
        model=model,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if hit:
        return cached

    from cafe.agents.executor import AgentExecutionError, AgentExecutor
    from cafe.core.types import AgentCLI, AgentConfig

    try:
        agent_cli = AgentCLI(cli)
    except ValueError as exc:
        raise PreflightCacheError(f"unsupported CAFE CLI: {cli}") from exc

    executor = AgentExecutor(
        AgentConfig(name="PreflightCandidateProbe", cli=agent_cli, model=model)
    )
    diagnostics = io.StringIO()
    original_directory = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix="cafe-preflight-") as temporary_directory:
            os.chdir(temporary_directory)
            with redirect_stdout(diagnostics), redirect_stderr(diagnostics):
                response = executor.execute(PROBE_PROMPT, allowed_tools=[])
    except AgentExecutionError as exc:
        detail = exc.display_message or str(exc)
        raise PreflightCacheError(f"candidate probe failed for {cli}:{model}: {detail}") from exc
    finally:
        os.chdir(original_directory)

    if PROBE_MARKER not in response.response:
        raise PreflightCacheError(
            f"candidate probe returned an unexpected response for {cli}:{model}"
        )
    resolved_model = response.model or model
    candidate_record(
        cache_file=cache_file,
        cli=cli,
        model=model,
        resolved_model=resolved_model,
        now=now,
    )
    return {
        "kind": "candidate",
        "status": "fresh",
        "cli": cli,
        "model": model,
        "resolved_model": resolved_model,
    }


def candidate_invalidate(*, cache_file: Path, cli: str, model: str) -> dict[str, Any]:
    removed = 0
    with _cache_lock(cache_file):
        document = _load_cache(cache_file)
        entries = document["candidate_success"]
        for key, entry in list(entries.items()):
            if (
                isinstance(entry, dict)
                and entry.get("cli") == cli
                and (entry.get("model") == model or entry.get("resolved_model") == model)
            ):
                del entries[key]
                removed += 1
        _save_cache(cache_file, document)
    return {"kind": "candidate", "status": "invalidated", "removed": removed}


def _parse_chain(raw_entries: Sequence[str]) -> tuple[tuple[str, str], ...]:
    chain: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        cli, separator, model = raw.partition(":")
        cli, model = cli.strip(), model.strip()
        if not separator or not cli or not model:
            raise PreflightCacheError("fallback entries must use CLI:MODEL")
        if cli in seen:
            raise PreflightCacheError("fallback smoke requires distinct CLIs")
        seen.add(cli)
        chain.append((cli, model))
    if len(chain) < 2:
        raise PreflightCacheError("fallback smoke requires a primary and fallback")
    return tuple(chain)


def _runtime_fingerprint() -> dict[str, Any]:
    import cafe.agents.executor as executor_module
    import cafe.agents.manager as manager_module
    import cafe.core.types as types_module

    modules = (manager_module, executor_module, types_module)
    files: dict[str, str] = {}
    for module in modules:
        path = Path(str(module.__file__)).resolve()
        files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        package_version = metadata.version("cafe-engine")
    except metadata.PackageNotFoundError:
        package_version = "unknown"
    evidence = {
        "package_version": package_version,
        "modules": files,
        "smoke_protocol": SMOKE_PROTOCOL,
    }
    return {**evidence, "digest": _json_digest(evidence)}


def _exercise_fallback_chain(chain: tuple[tuple[str, str], ...]) -> None:
    from unittest.mock import patch

    from cafe.agents.executor import AgentExecutionError, AgentExecutor
    from cafe.agents.manager import AgentManager
    from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, CliEntry, TokenUsage

    entries: list[CliEntry] = []
    for cli_name, model in chain:
        try:
            cli = AgentCLI(cli_name)
        except ValueError as exc:
            raise PreflightCacheError(f"unsupported CAFE CLI: {cli_name}") from exc
        entries.append(CliEntry(cli=cli, model=model))

    for success_index in range(1, len(entries)):
        config = AgentConfig(
            name="PreflightFallbackProbe",
            cli=entries[0].cli,
            model=entries[0].model,
            clis=entries,
        )
        manager = AgentManager(issue_name=None)
        manager.agents[config.name] = AgentExecutor(config)
        calls: list[tuple[str, str | None]] = []
        successful_cli = entries[success_index].cli

        def fake_execute(self: AgentExecutor, *_args: Any, **_kwargs: Any) -> AgentResponse:
            calls.append((self.config.cli.value, self.config.model))
            if self.config.cli != successful_cli:
                raise AgentExecutionError(
                    "preflight model unavailable", error_type="model_not_found"
                )
            return AgentResponse(
                response="FALLBACK_OK",
                token_usage=TokenUsage(),
                cli=self.config.cli,
                model=self.config.model,
            )

        diagnostics = io.StringIO()
        with (
            patch.object(AgentExecutor, "execute", new=fake_execute),
            redirect_stdout(diagnostics),
            redirect_stderr(diagnostics),
        ):
            response, *_ = manager.execute(config.name, "fallback smoke")
        expected = [(entry.cli.value, entry.model) for entry in entries[: success_index + 1]]
        if (
            response != "FALLBACK_OK"
            or calls != expected
            or manager.get_last_cli() != successful_cli
        ):
            raise PreflightCacheError("CAFE fallback smoke produced an unexpected result")


def fallback_smoke(
    *,
    cache_file: Path,
    raw_entries: Sequence[str],
    max_age_seconds: int,
    force: bool,
    now: float,
) -> dict[str, Any]:
    chain = _parse_chain(raw_entries)
    runtime = _runtime_fingerprint()
    key = _json_digest(
        {
            "schema_version": SCHEMA_VERSION,
            "chain": chain,
            "runtime_fingerprint": runtime["digest"],
            "smoke_protocol": SMOKE_PROTOCOL,
        }
    )
    with _cache_lock(cache_file):
        entry = _load_cache(cache_file)["fallback_success"].get(key)
    if not force and isinstance(entry, dict):
        checked_at = entry.get("checked_at")
        if isinstance(checked_at, (int, float)) and now - float(checked_at) <= max_age_seconds:
            return {
                "kind": "fallback",
                "status": "hit",
                "chain": [f"{cli}:{model}" for cli, model in chain],
                "checked_at": entry.get("checked_at_text"),
            }

    _exercise_fallback_chain(chain)
    with _cache_lock(cache_file):
        document = _load_cache(cache_file)
        document["fallback_success"][key] = {
            "chain": [f"{cli}:{model}" for cli, model in chain],
            "runtime_fingerprint": runtime,
            "checked_at": now,
            "checked_at_text": _utc_text(now),
        }
        _save_cache(cache_file, document)
    return {
        "kind": "fallback",
        "status": "fresh",
        "chain": [f"{cli}:{model}" for cli, model in chain],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reuse successful model availability and CAFE fallback preflight evidence."
    )
    parser.add_argument("--cache-file", type=Path, default=default_cache_file())
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("candidate-check")
    check.add_argument("--cli", required=True)
    check.add_argument("--model", required=True)
    check.add_argument("--max-age-seconds", type=int, default=CANDIDATE_MAX_AGE_SECONDS)

    probe = subparsers.add_parser("candidate-probe")
    probe.add_argument("--cli", required=True)
    probe.add_argument("--model", required=True)
    probe.add_argument("--max-age-seconds", type=int, default=CANDIDATE_MAX_AGE_SECONDS)

    invalidate = subparsers.add_parser("candidate-invalidate")
    invalidate.add_argument("--cli", required=True)
    invalidate.add_argument("--model", required=True)

    smoke = subparsers.add_parser("fallback-smoke")
    smoke.add_argument("--entry", action="append", required=True)
    smoke.add_argument("--max-age-seconds", type=int, default=FALLBACK_MAX_AGE_SECONDS)
    smoke.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    now = time.time()
    try:
        if args.command == "candidate-check":
            if args.max_age_seconds < 0:
                raise PreflightCacheError("max age must not be negative")
            hit, result = candidate_check(
                cache_file=args.cache_file,
                cli=args.cli,
                model=args.model,
                max_age_seconds=args.max_age_seconds,
                now=now,
            )
            print(json.dumps(result, sort_keys=True))
            return 0 if hit else MISS_EXIT_CODE
        if args.command == "candidate-probe":
            if args.max_age_seconds < 0:
                raise PreflightCacheError("max age must not be negative")
            result = candidate_probe(
                cache_file=args.cache_file,
                cli=args.cli,
                model=args.model,
                max_age_seconds=args.max_age_seconds,
                now=now,
            )
        elif args.command == "candidate-invalidate":
            result = candidate_invalidate(
                cache_file=args.cache_file, cli=args.cli, model=args.model
            )
        else:
            if args.max_age_seconds < 0:
                raise PreflightCacheError("max age must not be negative")
            result = fallback_smoke(
                cache_file=args.cache_file,
                raw_entries=args.entry,
                max_age_seconds=args.max_age_seconds,
                force=args.force,
                now=now,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, PreflightCacheError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

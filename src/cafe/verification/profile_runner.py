"""Execute flat profiles and recheck suite-local applicability on the same HEAD."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from cafe.verification.profiles import ProfileRequest, fingerprint, load_profile, repository_path
from cafe.verification.receipt import (
    ReceiptCheck,
    VerificationReceiptError,
    _check_verification_receipt,
    _git_state,
    _sha256_file,
    _write_json_atomic,
    receipt_path_for_output,
)


def _fresh(request: ProfileRequest) -> ProfileRequest:
    return load_profile(
        request.root,
        Path(request.source),
        request.name,
        [x for x in request.selected if x not in request.mandatory],
    )


def _context(request: ProfileRequest, suite: dict, env: dict[str, str]) -> tuple[dict, list[str]]:
    declaration = suite["context"]
    context: dict[str, Any] = {"files": {}, "env": {}, "probes": []}
    reasons = []
    for value in declaration["files"]:
        try:
            path = repository_path(request.root, value)
            if not path.is_file():
                raise OSError("not a file")
            context["files"][value] = _sha256_file(path)
        except (OSError, VerificationReceiptError):
            context["files"][value] = None
            reasons.append(f"required file unavailable: {value}")
    for name in declaration["env"]:
        context["env"][name] = env.get(name)
        if name not in env:
            reasons.append(f"required environment unavailable: {name}")
    for argv in declaration["probes"]:
        with tempfile.TemporaryFile() as log:
            try:
                result = subprocess.run(
                    argv,
                    cwd=request.root,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                    check=False,
                )
                code = result.returncode
            except (OSError, subprocess.TimeoutExpired):
                code = None
            log.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: log.read(65536), b""):
                digest.update(chunk)
        context["probes"].append({"argv": argv, "exit_code": code, "sha256": digest.hexdigest()})
        if code != 0:
            reasons.append("required capability probe did not succeed")
    return context, reasons


def _segment_reason(row: dict, path: Path) -> str | None:
    segment = row.get("log")
    if not isinstance(segment, dict):
        return "suite log segment is missing"
    start, end = segment.get("start"), segment.get("end")
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start <= end <= path.stat().st_size
    ):
        return "suite log bounds are invalid"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        stream.seek(start)
        remaining = end - start
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                return "suite log segment is truncated"
            digest.update(chunk)
            remaining -= len(chunk)
    if segment.get("sha256") != digest.hexdigest():
        return "suite log digest does not match"
    return None


def _inspect(
    request: ProfileRequest, output_file: Path, *, for_reuse: bool
) -> tuple[ReceiptCheck, dict[str, list[str]]]:
    checked = _check_verification_receipt(
        output_file=output_file, cwd=request.root, supported_schemas=frozenset({3})
    )
    data = checked.receipt or {}
    common = list(checked.reasons)
    rows = data.get("suites")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows
    ):
        common.append("suite results are missing or malformed")
        rows = []
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        common.append("duplicate suite results")
    recorded = data.get("profile", {})
    if (
        not isinstance(recorded, dict)
        or recorded.get("source") != request.source
        or recorded.get("name") != request.name
        or not isinstance(recorded.get("identity"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", recorded["identity"])
    ):
        common.append("profile binding does not match")
    if (
        not isinstance(data.get("selected"), list)
        or not all(isinstance(x, str) for x in data["selected"])
        or len(set(data["selected"])) != len(data["selected"])
        or set(ids) != set(data["selected"])
    ):
        common.append("recorded selection does not match suite results")
    if not for_reuse and (
        data.get("mandatory") != list(request.mandatory)
        or data.get("selected") != list(request.selected)
    ):
        common.append("current mandatory/selected membership does not match")
    by_id = {row["id"]: row for row in rows}
    applicability = {}
    for suite_id, suite in request.suites.items():
        reasons = list(common)
        row = by_id.get(suite_id)
        if row is None:
            reasons.append("required suite result is missing")
        else:
            if (
                row.get("completion") != "completed"
                or row.get("action") not in ("executed", "reused")
                or type(row.get("exit_code")) is not int
                or row["exit_code"] != 0
            ):
                reasons.append("suite did not complete successfully")
            if (
                row.get("argv") != suite["argv"]
                or row.get("definition") != fingerprint(suite)
                or row.get("suite_definition") != suite
            ):
                reasons.append("suite definition changed")
            if not common:
                try:
                    reason = _segment_reason(row, checked.receipt_path.parent / "verification.log")
                except OSError:
                    reason = "suite log is unreadable"
                if reason:
                    reasons.append(reason)
            context, context_reasons = _context(request, suite, dict(os.environ))
            reasons.extend(context_reasons)
            if (
                row.get("context") != context
                or row.get("context_errors")
                or row.get("context_identity")
                != fingerprint({"definition": suite, "context": context})
            ):
                reasons.append("required execution context changed or was unverifiable")
            if for_reuse and not suite["reuse"]:
                reasons.append("suite has not opted into reuse")
        applicability[suite_id] = reasons
    all_reasons = common + [
        f"{key}: {reason}" for key, reasons in applicability.items() for reason in reasons
    ]
    return (
        ReceiptCheck(
            not all_reasons,
            tuple(dict.fromkeys(all_reasons)),
            checked.receipt_path,
            checked.receipt,
        ),
        applicability,
    )


def check_profile(
    request: ProfileRequest, output_file: Path, *, for_reuse: bool = False
) -> ReceiptCheck:
    """Re-evaluate the current mandatory contract, results, logs and required context."""
    checked, applicability = _inspect(_fresh(request), output_file, for_reuse=for_reuse)
    payload = copy.deepcopy(checked.receipt or {})
    payload["applicability"] = applicability
    payload["pending"] = [key for key, reasons in applicability.items() if reasons]
    return ReceiptCheck(checked.valid, checked.reasons, checked.receipt_path, payload)


def _copy_segment(source: Path, row: dict, target) -> None:
    handle = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(handle, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise VerificationReceiptError("source log is not a regular file")
        digest = hashlib.sha256()
        stream.seek(row["log"]["start"])
        remaining = row["log"]["end"] - row["log"]["start"]
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise VerificationReceiptError("source log changed during reuse")
            target.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != row["log"]["sha256"]:
            raise VerificationReceiptError("source log changed during reuse")


def run_profile(
    request: ProfileRequest,
    output_file: Path,
    reuse_from: Path | None = None,
    *,
    reuse_only: bool = False,
) -> tuple[int, Path, dict]:
    """Run selected mandatory-plus-extra suites; reuse only checked successful rows."""
    request = _fresh(request)
    root, head, status = _git_state(request.root)
    if root != request.root or status:
        raise VerificationReceiptError("profile verification requires the clean Git worktree root")
    receipt_path = receipt_path_for_output(output_file)
    source_check = None
    applicability: dict[str, list[str]] = {}
    if reuse_from is not None:
        if receipt_path_for_output(reuse_from) == receipt_path:
            raise VerificationReceiptError(
                "source and target receipts must be in different iterations"
            )
        source_check, applicability = _inspect(request, reuse_from, for_reuse=True)
    if reuse_only and (source_check is None or not source_check.valid):
        detail = "; ".join(source_check.reasons) if source_check else "source is missing"
        raise VerificationReceiptError(f"cannot reuse incomplete profile evidence: {detail}")
    source_results = (source_check.receipt or {}).get("suites", []) if source_check else []
    source_rows = (
        {
            row["id"]: row
            for row in source_results
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        if isinstance(source_results, list)
        else {}
    )
    source_digest = (
        _sha256_file(source_check.receipt_path) if source_check and source_check.receipt else None
    )
    started = time.monotonic()
    started_at = datetime.now().astimezone().isoformat()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".verification.", dir=receipt_path.parent)
    rows = []
    exit_code = 0
    # Replace any prior target success before a new attempt can be interrupted.
    checkpoint = {
        "schema_version": 3,
        "scope": "full",
        "valid": False,
        "status": "incomplete",
        "exit_code": None,
        "profile": {"source": request.source, "name": request.name, "identity": request.identity},
        "mandatory": list(request.mandatory),
        "selected": list(request.selected),
        "started_at": started_at,
        "suites": rows,
    }
    _write_json_atomic(receipt_path, checkpoint)
    try:
        with os.fdopen(handle, "w+b") as log:
            for suite_id, suite in request.suites.items():
                row = {
                    "id": suite_id,
                    "argv": suite["argv"],
                    "definition": fingerprint(suite),
                    "suite_definition": suite,
                    "completion": "pending",
                    "exit_code": None,
                    "action": "pending",
                    "applicability_reasons": applicability.get(suite_id, ["no source evidence"]),
                }
                rows.append(row)
                if exit_code:
                    continue
                row["completion"] = "started"
                _write_json_atomic(receipt_path, checkpoint)
                offset = log.tell()
                row_start = time.monotonic()
                if source_check and not applicability[suite_id]:
                    source_row = source_rows[suite_id]
                    row.update(copy.deepcopy(source_row))
                    row["action"] = "reused"
                    row["applicability_reasons"] = []
                    row["provenance"] = {
                        "receipt": str(source_check.receipt_path),
                        "receipt_sha256": source_digest,
                        "profile_identity": source_check.receipt["profile"]["identity"],
                        "head": head,
                    }
                    _copy_segment(
                        source_check.receipt_path.parent / "verification.log", source_row, log
                    )
                else:
                    env = dict(os.environ)
                    context, errors = _context(request, suite, env)
                    try:
                        log.flush()
                        result = subprocess.run(
                            suite["argv"],
                            cwd=root,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            check=False,
                        )
                        code = result.returncode
                        log.seek(0, os.SEEK_END)
                    except OSError as exc:
                        code = 127
                        log.write(f"cannot start suite: {exc}\n".encode())
                    after_context, after_errors = _context(request, suite, env)
                    if after_context != context:
                        errors.append("context changed during execution")
                    errors.extend(after_errors)
                    row.update(
                        context=context,
                        context_identity=fingerprint({"definition": suite, "context": context}),
                        context_errors=errors,
                        completion="completed",
                        exit_code=code,
                        action="executed",
                        duration_seconds=time.monotonic() - row_start,
                    )
                    exit_code = code
                end = log.tell()
                log.flush()
                log.seek(offset)
                digest = hashlib.sha256()
                remaining = end - offset
                while remaining:
                    chunk = log.read(min(65536, remaining))
                    digest.update(chunk)
                    remaining -= len(chunk)
                log.seek(end)
                row["log"] = {"start": offset, "end": end, "sha256": digest.hexdigest()}
            log.flush()
            os.fsync(log.fileno())
        os.replace(temporary, receipt_path.parent / "verification.log")
    except BaseException:
        # Keep partial output diagnostic-only; the checkpoint cannot pass check.
        if Path(temporary).exists():
            os.replace(temporary, receipt_path.parent / "verification.log")
        raise
    if source_check and any(row["action"] == "reused" for row in rows):
        if _sha256_file(source_check.receipt_path) != source_digest:
            raise VerificationReceiptError("source receipt changed during reuse")
    root_after, head_after, status_after = _git_state(root)
    stable = root_after == root and head_after == head and not status_after
    try:
        stable = stable and _fresh(request) == request
    except VerificationReceiptError:
        stable = False
    passed = not exit_code and stable and all(row["completion"] == "completed" for row in rows)
    log_path = receipt_path.parent / "verification.log"
    payload = {
        "schema_version": 3,
        "scope": "full",
        "command": ["profile", request.source, request.name],
        "exit_code": exit_code,
        "valid": passed,
        "status": "passed" if passed else "failed" if exit_code else "incomplete",
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "duration_seconds": time.monotonic() - started,
        "profile": {"source": request.source, "name": request.name, "identity": request.identity},
        "mandatory": list(request.mandatory),
        "selected": list(request.selected),
        "suites": rows,
        "executed": sum(row["action"] == "executed" for row in rows),
        "reused": sum(row["action"] == "reused" for row in rows),
        "pending": [row["id"] for row in rows if row["completion"] == "pending"],
        "git": {
            "root": str(root),
            "cwd": str(root),
            "cwd_relative_to_root": ".",
            "head": head,
            "clean_before": True,
            "clean_after": not status_after,
            "head_unchanged": head_after == head,
        },
        "output_log": {
            "path": "verification.log",
            "sha256": _sha256_file(log_path),
            "size_bytes": log_path.stat().st_size,
            "stdout_stderr_combined": True,
        },
    }
    _write_json_atomic(receipt_path, payload)
    return exit_code or (0 if passed else 3), receipt_path, payload


def reuse_profile(
    request: ProfileRequest, source_output_file: Path, output_file: Path
) -> tuple[Path, dict]:
    """Materialize an aggregate only when every required suite is reusable."""
    _, path, payload = run_profile(request, output_file, source_output_file, reuse_only=True)
    return path, payload

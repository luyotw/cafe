"""Reuse existing iteration telemetry without resolving caller authority."""

import ctypes
import errno
import json
import math
import os
import stat
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Dict

import yaml

from cafe.core.types import TokenUsage
from cafe.core.cost import merge_cost_records
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.utils.issue_config import issue_config_lock
from cafe.utils.yaml_utils import safe_load


def merge_token_usage_stats(existing: Any, incoming: TokenUsage) -> Dict[str, Any]:
    """Merge one raw attempt into the existing iteration stats shape."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    prior_records = merged.get("cost_records", [])
    if any("native_usage" in r for r in [*prior_records, *incoming.cost_records]):
        return _merge_native_usage(merged, incoming)
    if incoming.cost_records and all(
        record.get("invocation_id") in {prior.get("invocation_id") for prior in prior_records}
        for record in incoming.cost_records
    ):
        return merged
    incoming_data = incoming.model_dump()
    duplicate_ids = {record.get("invocation_id") for record in prior_records}
    for record in incoming.cost_records:
        if record.get("invocation_id") not in duplicate_ids:
            continue
        for key, value in record.get("usage", {}).items():
            if isinstance(incoming_data.get(key), (int, float)) and isinstance(value, (int, float)):
                incoming_data[key] -= value
        if record.get("amount_usd") is not None:
            incoming_data["total_cost_usd"] -= float(record["amount_usd"])
    additive_fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "cache_read_input_tokens",
        "reasoning_output_tokens",
        "total_cost_usd",
    )
    for field in additive_fields:
        prior = merged.get(field, 0)
        value = incoming_data.get(field, 0)
        merged[field] = (prior if isinstance(prior, (int, float)) else 0) + (
            value if isinstance(value, (int, float)) else 0
        )

    for field in ("duration_ms", "duration_api_ms"):
        prior = merged.get(field)
        value = incoming_data.get(field)
        if isinstance(value, int):
            merged[field] = (prior if isinstance(prior, int) else 0) + value
        elif field not in merged:
            merged[field] = None

    prior_turns = merged.get("turn_usages")
    incoming_turns = incoming_data.get("turn_usages")
    merged["turn_usages"] = (list(prior_turns) if isinstance(prior_turns, list) else []) + (
        list(incoming_turns) if isinstance(incoming_turns, list) else []
    )
    merged["cost_records"] = merge_cost_records(prior_records, incoming.cost_records)
    return merged


def _merge_native_usage(existing, incoming):
    """Replace represented native endpoints while retaining historical residuals."""
    from cafe.core.native_accounting import accounting_admission, unrepresented_totals

    old_records = existing.get("cost_records", [])
    old = accounting_admission(old_records)
    added = accounting_admission(incoming.cost_records)
    admission = accounting_admission([*old_records, *incoming.cost_records])
    records = admission["records"]
    merged = dict(existing)
    raw = incoming.model_dump(exclude_unset=True)
    residuals = unrepresented_totals(existing, old)
    incoming_residuals = unrepresented_totals(raw, added)
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
        "reasoning_output_tokens",
        "total_cost_usd",
    ):
        canonical = "cache_write_input_tokens" if key == "cache_creation_input_tokens" else key
        residual = residuals.get(key, 0)
        incoming_residual = incoming_residuals.get(key, 0)
        if incoming.cost_records and all(
            r["invocation_id"] in {r["invocation_id"] for r in old_records}
            for r in incoming.cost_records
        ):
            incoming_residual = 0
        residuals[key] = residual + incoming_residual
        if canonical == "total_cost_usd":
            admitted = sum(
                float(r["amount_usd"])
                for r in admission["admitted"]
                if r.get("amount_usd") is not None
            )
        else:
            admitted = admission["native_usage"]["tokens"].get(canonical, 0)
        if key in existing or key in raw or admitted or residuals[key]:
            merged[key] = residuals[key] + admitted
    # Durable independent residuals must not be re-derived from admitted scalars.
    if any(residuals.values()):
        merged["accounting_residual"] = residuals
    else:
        merged.pop("accounting_residual", None)
    seen = {r["invocation_id"] for r in old_records}
    new_call = not incoming.cost_records or any(
        r["invocation_id"] not in seen for r in incoming.cost_records
    )
    if new_call:
        for key in ("duration_ms", "duration_api_ms"):
            if raw.get(key) is not None:
                merged[key] = (merged.get(key) or 0) + raw[key]
        merged["turn_usages"] = [*existing.get("turn_usages", []), *incoming.turn_usages]
    merged["cost_records"] = records
    return merged


def _inode(info):
    return info.st_dev, info.st_ino


@contextmanager
def _usage_parent(target: Path, *, expected_parents=None):
    """Reuse no-follow descriptor traversal across the complete absolute path."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.sep, flags)
    identities = [_inode(os.fstat(descriptor))]
    try:
        for part in target.parent.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            identities.append(_inode(os.fstat(descriptor)))
        if expected_parents is not None and tuple(identities) != expected_parents:
            raise ValueError("usage target parent changed")
        yield descriptor, tuple(identities)
    finally:
        os.close(descriptor)


def _read_usage_file(parent_fd, name, *, issue_metadata=False):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("usage target must be a regular file")
        try:
            data = safe_load(handle) if issue_metadata else json.load(handle)
        except yaml.YAMLError as error:
            raise ValueError("invalid accounting metadata") from error
        return data, _inode(info)


def _exchange_usage_file(parent_fd, source, destination, destination_parent_fd):
    """Atomically publish while retaining the displaced inode for validation.

    Ordinary replace cannot conditionally protect a destination substituted at
    the syscall boundary. Exchange keeps that object intact for verification and
    rollback, including symlinks, without following it or publishing partial JSON.
    Unsupported platforms/filesystems fail before modifying either file.
    """
    library = ctypes.CDLL(None, use_errno=True)
    if hasattr(library, "renameat2"):
        operation, flag = library.renameat2, 2  # Linux RENAME_EXCHANGE
    elif hasattr(library, "renameatx_np"):
        operation, flag = library.renameatx_np, 2  # Darwin RENAME_SWAP
    else:
        raise OSError(errno.ENOTSUP, "atomic usage exchange is unavailable")
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_uint]
    operation.restype = ctypes.c_int
    if operation(
        parent_fd, os.fsencode(source), destination_parent_fd, os.fsencode(destination), flag
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def iteration_usage_sink(repository_root: Path, context_file: Path, *, workspace_locked=False):
    """Pin an existing caller-admitted metadata target; never create an iteration."""
    return _metadata_usage_sink(repository_root, context_file, workspace_locked=workspace_locked)


def _metadata_usage_sink(
    repository_root,
    context_file,
    *,
    issue_metadata=False,
    workspace_locked=False,
    update=None,
    validate=None,
):
    """Publish an update through the same pinned metadata and staging boundary."""
    root = Path(repository_root).resolve()
    target = Path(os.path.abspath(context_file))
    if target.resolve() != target or not target.is_relative_to(root):
        raise ValueError("usage target must remain within its admitted workspace")
    try:
        with _usage_parent(target) as (parent_fd, parents):
            original, _ = _read_usage_file(parent_fd, target.name, issue_metadata=issue_metadata)
    except FileNotFoundError:
        return None
    if not isinstance(original, dict) or (
        not issue_metadata and not isinstance(original.get("iteration"), int)
    ):
        return None
    if validate is not None:
        validate(original)

    def metadata_identity(data):
        if issue_metadata:
            return tuple(
                data.get(key)
                for key in ("issue_name", "initial_input", "feature_branch", "worktree_path")
            )
        return data.get("iteration"), data.get("timestamp")

    identity = metadata_identity(original)

    def persist(usage):
        with (
            nullcontext() if workspace_locked else workspace_execution_lock(root),
            _usage_parent(target, expected_parents=parents) as (parent_fd, _parents),
            issue_config_lock(target, parent_fd=parent_fd) if issue_metadata else nullcontext(),
        ):
            # A cooperating writer can atomically replace this same iteration.
            # Read its latest counts under the shared lock; pin this read's inode
            # only through publication, not across independent provider calls.
            current, current_inode = _read_usage_file(
                parent_fd, target.name, issue_metadata=issue_metadata
            )
            if not isinstance(current, dict) or metadata_identity(current) != identity:
                raise ValueError("admitted metadata identity changed")
            if validate is not None:
                validate(current)
            if update is None:
                current["stats"] = merge_token_usage_stats(current.get("stats"), usage)
            else:
                update(current, usage)
            if validate is not None:
                validate(current)
            # Exclusive creation prevents consuming an abandoned recovery object.
            # Cooperating writers hold the workspace lock throughout publication
            # and reclamation. Same-account hostile namespace mutation requires
            # an isolation boundary beyond this private staging directory.
            temporary = ".usage-" + target.name
            os.mkdir(temporary, 0o700, dir_fd=parent_fd)
            private_fd = None
            staging_fd = None
            cleanup_inode = None
            source = ".usage-publish.json"
            try:
                private_fd = os.open(
                    temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                )
                staging_fd = os.open(
                    source,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=private_fd,
                )
                staged = os.fstat(staging_fd)
                published_inode = _inode(staged)
                cleanup_inode = published_inode
                with os.fdopen(os.dup(staging_fd), "w", encoding="utf-8") as handle:
                    if issue_metadata:
                        yaml.safe_dump(current, handle, sort_keys=False, allow_unicode=True)
                    else:
                        json.dump(current, handle, ensure_ascii=False, indent=2)
                _exchange_usage_file(private_fd, source, target.name, parent_fd)
                cleanup_inode = None
                displaced = os.stat(source, dir_fd=private_fd, follow_symlinks=False)
                published = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                if _inode(displaced) != current_inode or _inode(published) != published_inode:
                    _exchange_usage_file(private_fd, source, target.name, parent_fd)
                    restored = os.stat(source, dir_fd=private_fd, follow_symlinks=False)
                    if _inode(restored) == published_inode:
                        cleanup_inode = published_inode
                    raise ValueError("usage target changed during publication")
                cleanup_inode = current_inode
            finally:
                try:
                    if cleanup_inode is not None:
                        remaining = os.stat(source, dir_fd=private_fd, follow_symlinks=False)
                        if _inode(remaining) != cleanup_inode:
                            raise ValueError("usage staging entry changed during cleanup")
                        # Remove our link only; published bytes remain intact for
                        # already-open readers and hardlinks, including late ones.
                        os.unlink(source, dir_fd=private_fd)
                    if private_fd is not None and not os.listdir(private_fd):
                        directory = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                        if _inode(directory) != _inode(os.fstat(private_fd)):
                            raise ValueError("usage staging directory changed during cleanup")
                        os.rmdir(temporary, dir_fd=parent_fd)
                finally:
                    if staging_fd is not None:
                        os.close(staging_fd)
                    if private_fd is not None:
                        os.close(private_fd)

    return persist


CHAT_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_write_input_tokens",
    "cache_read_input_tokens",
    "reasoning_output_tokens",
    "total_cost_usd",
)


def _bounded_chat_identity(value):
    """Keep persisted identities within the existing scalar accounting limit."""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _validate_chat_usage(metadata):
    """Validate existing aggregates before admission and each locked update."""
    if "chat_usage" not in metadata:
        return  # Existing metadata without the optional field remains compatible.
    groups = metadata["chat_usage"]
    if not isinstance(groups, list):
        raise ValueError("invalid existing chat accounting")
    seen = set()
    identity_fields = ("cli", "requested_model", "reported_model", "mode", "phase")
    for group in groups:
        if not isinstance(group, dict) or not set(identity_fields).issubset(group):
            raise ValueError("invalid chat accounting group")
        for field in identity_fields:
            value = group[field]
            if value is None and field in {"requested_model", "reported_model"}:
                continue
            if not _bounded_chat_identity(value):
                raise ValueError("invalid chat accounting identity")
        key = tuple(group[field] for field in identity_fields)
        if group["mode"] not in {"interactive", "one_shot"} or key in seen:
            raise ValueError("invalid or duplicate chat accounting group")
        seen.add(key)
        calls, incomplete = group.get("calls"), group.get("incomplete_calls")
        if type(calls) is not int or type(incomplete) is not int or not 0 <= incomplete <= calls:
            raise ValueError("invalid chat accounting call counts")
        stats, unknown = group.get("stats"), group.get("unknown_fields")
        if "cost_records" in group:
            from cafe.core.cost import summarize_cost
            records = group["cost_records"]
            if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                raise ValueError("invalid chat cost records")
            summarize_cost(records)
        if (
            not isinstance(stats, dict)
            or not isinstance(unknown, list)
            or any(
                not isinstance(field, str) or field not in CHAT_USAGE_FIELDS for field in unknown
            )
        ):
            raise ValueError("invalid chat accounting coverage")
        for field, value in stats.items():
            if field == "accounting_residual":
                from cafe.core.native_accounting import validate_accounting_residual

                validate_accounting_residual(value)
                continue
            if (
                field not in CHAT_USAGE_FIELDS
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError("invalid chat accounting statistics")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite or (field != "total_cost_usd" and not isinstance(value, int)):
                raise ValueError("invalid chat accounting statistics")
        missing = set(CHAT_USAGE_FIELDS) - stats.keys()
        if not missing.issubset(unknown) or (
            calls
            and (unknown or group["reported_model"] is None or group["mode"] == "interactive")
            and not incomplete
        ):
            raise ValueError("incomplete chat accounting coverage is unmarked")


def chat_usage_sink(
    repository_root, metadata_file, *, cli, requested_model, mode, phase, issue_metadata=False
):
    """Store bounded aggregates, including missing evidence, without new authority.

    Known values are subtotals. Unknown fields remain unknown even when a later
    call reports them. Requested model is never used as evidence of actual model.
    """
    # Configuration accepts arbitrary model strings. Preserve provider selection,
    # but omit a requested label that cannot fit the durable scalar contract;
    # truncating it would invent a different model identity.
    if requested_model is not None and not _bounded_chat_identity(requested_model):
        requested_model = None

    def update(current, results):
        groups = current.setdefault("chat_usage", [])
        if not isinstance(groups, list):
            raise ValueError("invalid existing chat accounting")
        by_model = {}
        for result in results:
            by_model.setdefault(result.reported_model, []).append(result)
        for model, records in by_model.items():
            key = (cli, requested_model, model, mode, phase)
            incoming_attempts = {
                c["native_usage"]["attempt_id"]
                for r in records
                if r.usage is not None
                for c in r.usage.cost_records
                if "native_usage" in c
            }
            # Entry checkpoints precede model observation. Refine their group rather
            # than leaving a permanently open copy under the unknown model.
            if model is not None and incoming_attempts:
                for item in groups:
                    if (
                        item.get("reported_model") is None
                        and tuple(item.get(k) for k in ("cli", "requested_model", "mode", "phase"))
                        == (cli, requested_model, mode, phase)
                        and item.get("native_attempts")
                        and set(item["native_attempts"]) <= incoming_attempts
                        and item["calls"] == len(item["native_attempts"])
                        and not item.get("legacy_incomplete_calls")
                        and not item.get("legacy_unknown_fields")
                    ):
                        item["reported_model"] = model
            group = next(
                (
                    item
                    for item in groups
                    if isinstance(item, dict)
                    and tuple(
                        item.get(k)
                        for k in ("cli", "requested_model", "reported_model", "mode", "phase")
                    )
                    == key
                ),
                None,
            )
            if group is None:
                group = dict(
                    zip(("cli", "requested_model", "reported_model", "mode", "phase"), key)
                )
                group.update(stats={}, calls=0, incomplete_calls=0, unknown_fields=[])
                groups.append(group)
            native_results = [
                r
                for r in records
                if r.usage is not None and any("native_usage" in c for c in r.usage.cost_records)
            ]
            if native_results:
                group.setdefault("legacy_unknown_fields", list(group["unknown_fields"]))
                group.setdefault("legacy_incomplete_calls", group["incomplete_calls"])
                prior_attempts = set(group.get("native_attempts", []))
                attempts = set(prior_attempts)
                for result in native_results:
                    usage = result.usage
                    attempts.update(
                        c["native_usage"]["attempt_id"]
                        for c in usage.cost_records
                        if "native_usage" in c
                    )
                    previous = dict(group["stats"], cost_records=group.get("cost_records", []))
                    merged = merge_token_usage_stats(previous, usage)
                    group["stats"] = {
                        k: v
                        for k, v in merged.items()
                        if k in CHAT_USAGE_FIELDS or k == "accounting_residual"
                    }
                    group["cost_records"] = merged["cost_records"]
                    if not issue_metadata:
                        current["stats"] = merge_token_usage_stats(current.get("stats"), usage)
                group["calls"] += len(attempts - prior_attempts)
                group["native_attempts"] = sorted(attempts)
                missing = set(group["legacy_unknown_fields"])
                physical = [
                    c
                    for c in group["cost_records"]
                    if c.get("native_usage", {}).get("kind") != "scope"
                ]
                for field in CHAT_USAGE_FIELDS:
                    if not physical or any(field not in c.get("usage", {}) for c in physical):
                        if (
                            field != "total_cost_usd"
                            or any(c.get("amount_usd") is None for c in physical)
                            or not physical
                        ):
                            missing.add(field)
                from cafe.core.native_accounting import native_projection

                view = native_projection(group["cost_records"])
                incomplete = bool(missing) or not view["complete"] or model is None
                group["incomplete_calls"] = group["legacy_incomplete_calls"] + (
                    len(attempts) if incomplete else 0
                )
                group["unknown_fields"] = sorted(missing)
                records = [r for r in records if r not in native_results]
            existing_ids = {record.get("invocation_id") for record in group.get("cost_records", [])}
            records = [record for record in records if not (
                record.usage is not None and record.usage.cost_records
                and all(cost.get("invocation_id") in existing_ids for cost in record.usage.cost_records)
            )]
            if not records:
                continue
            missing = set(group["unknown_fields"])
            incomplete = model is None or mode == "interactive"
            for record in records:
                usage = record.usage
                known = {}
                if usage is not None:
                    known = {
                        field: getattr(usage, field)
                        for field in CHAT_USAGE_FIELDS
                        if field in usage.model_fields_set
                    }
                    if any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value < 0
                        for value in known.values()
                    ):
                        raise ValueError("invalid reported chat statistics")
                absent = set(CHAT_USAGE_FIELDS) - known.keys()
                missing.update(absent)
                incomplete = incomplete or bool(absent) or bool(record.failure_code)
                previous = dict(group["stats"], cost_records=group.get("cost_records", []))
                merged = merge_token_usage_stats(
                    previous, TokenUsage(**known, cost_records=usage.cost_records if usage else [])
                )
                # The shared merge defaults are legacy iteration compatibility,
                # not evidence that a provider reported missing counters as zero.
                group["stats"] = {
                    field: merged[field]
                    for field in set(group["stats"]) | known.keys()
                    if field in CHAT_USAGE_FIELDS or field == "accounting_residual"
                }
                if "accounting_residual" in merged:
                    group["stats"]["accounting_residual"] = merged["accounting_residual"]
                if not issue_metadata and usage is not None:
                    current["stats"] = merge_token_usage_stats(current.get("stats"), usage)
                if usage is not None and usage.cost_records:
                    group["cost_records"] = merge_cost_records(group.get("cost_records"), usage.cost_records)
            group["calls"] += 1
            group["incomplete_calls"] += int(incomplete)
            group["unknown_fields"] = sorted(missing)

    return _metadata_usage_sink(
        repository_root, metadata_file, issue_metadata=issue_metadata, update=update,
        validate=_validate_chat_usage,
    )


def phase_stats_without_chat(stats, groups):
    """Accounting consumers must not also bill chat under phase/model metadata."""
    remaining = dict(stats) if isinstance(stats, dict) else {}
    native = any("native_usage" in r for r in remaining.get("cost_records", []))
    if native:
        from cafe.core.native_accounting import accounting_admission, unrepresented_totals

        def residuals(values, records):
            return unrepresented_totals(values, accounting_admission(records))

        independent = residuals(remaining, remaining.get("cost_records", []))
        for group in groups or ():
            for key, value in residuals(
                group.get("stats", {}), group.get("cost_records", [])
            ).items():
                independent[key] = max(0, independent.get(key, 0) - value)
    money = [remaining["total_cost_usd"]] if "total_cost_usd" in remaining else []
    cost_count = len(remaining.get("cost_records", []))
    for group in groups or ():
        for key, value in group.get("stats", {}).items():
            if key in CHAT_USAGE_FIELDS and isinstance(value, (int, float)) and key in remaining:
                if key == "total_cost_usd":
                    money.append(-value)
                else:
                    remaining[key] -= value
        if group.get("cost_records") and remaining.get("cost_records"):
            chat_ids = {record.get("invocation_id") for record in group["cost_records"]}
            remaining["cost_records"] = [record for record in remaining["cost_records"]
                                         if record.get("invocation_id") not in chat_ids]
    if len(money) > 1:
        amount = math.fsum(money)
        # Grouped float totals can differ from the phase's accumulation order.
        # Judge zero against the original operands, not the tiny residual;
        # preserve meaningful legacy amounts and genuinely invalid negatives.
        tolerance = sum(math.ulp(value) for value in money) * max(2, cost_count)
        remaining["total_cost_usd"] = 0.0 if abs(amount) <= tolerance else amount
    if native:
        admission = accounting_admission(remaining.get("cost_records", []))
        remaining["accounting_residual"] = independent
        for key in CHAT_USAGE_FIELDS:
            if key not in remaining and key not in independent:
                continue
            canonical = "cache_write_input_tokens" if key == "cache_creation_input_tokens" else key
            if key == "total_cost_usd":
                admitted = math.fsum(
                    float(r["amount_usd"])
                    for r in admission["admitted"]
                    if r.get("amount_usd") is not None
                )
            else:
                admitted = admission["native_usage"]["tokens"].get(canonical, 0)
            remaining[key] = independent.get(key, 0) + admitted
    return remaining

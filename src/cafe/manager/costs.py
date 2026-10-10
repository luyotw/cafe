"""Manager-owned attribution and retained, identity-bound accounting evidence."""

from __future__ import annotations

import copy
import fcntl
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cafe.core.cost import combine_cost_summaries, merge_cost_records, summarize_cost
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.services.cost_summary import (
    accounting_source_versions,
    collect_cost_sources,
    read_accounting_file,
    summarize_sources,
)
from cafe.utils.file_lock import open_lock_file

MAX_ACCOUNTING_BYTES = 16 * 1024 * 1024
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")


def common_dir(project_root: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve(strict=True)


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _valid_cutoff(value):
    try:
        offset = datetime.fromisoformat(value).utcoffset()
        return offset is not None and offset.total_seconds() == 0
    except (TypeError, ValueError):
        return False


class CostStore:
    """Accounting has its own lock/envelope; it cannot grant closeout authority."""

    def __init__(self, project_root: Path, issue_name: str, workflow_id: str):
        if not all(
            isinstance(v, str) and _COMPONENT.fullmatch(v) for v in (issue_name, workflow_id)
        ):
            raise ValueError("invalid accounting identity component")
        self.common = common_dir(project_root)
        self.identity = dict(
            project_common_dir=str(self.common), issue_name=issue_name, workflow_id=workflow_id
        )
        self.path = self.common / "cafe/costs" / issue_name / f"{workflow_id}.json"
        self.lock = self.path.with_suffix(".lock")

    def _safe(self, create=False):
        for parent in reversed((self.path.parent, *self.path.parent.parents)):
            if parent.is_symlink():
                raise ValueError("unsafe retained accounting directory")
            if create:
                parent.mkdir(mode=0o700, exist_ok=True)

    @contextmanager
    def locked(self, *, write=False):
        self._safe(create=write)
        if self.lock.is_symlink():
            raise ValueError("unsafe accounting lock")
        if not write and not self.lock.exists():
            if self.path.exists() or self.path.is_symlink():
                raise ValueError("retained accounting lock is missing")
            yield
            return
        if write:
            handle = open_lock_file(self.lock)
        else:
            fd = os.open(self.lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            handle = os.fdopen(fd, "rb")
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                handle.close()
                raise ValueError("unsafe retained accounting lock")
        with handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self):
        self._safe()
        if not self.path.exists() and not self.path.is_symlink():
            return dict(
                version=1,
                identity=self.identity,
                manager_sources=[],
                manager_gaps={
                    "unattested_manager_coverage": (
                        "historical or host/native Manager usage coverage is unavailable"
                    )
                },
                worker_snapshot=None,
            )
        if self.path.lstat().st_size > MAX_ACCOUNTING_BYTES:
            raise ValueError("retained accounting is oversized")
        try:
            data = read_accounting_file(self.path)
        except (OSError, ValueError) as exc:
            raise ValueError("retained accounting is unsafe or corrupt") from exc
        if (
            set(data)
            != {"version", "identity", "manager_sources", "manager_gaps", "worker_snapshot"}
            or data["version"] != 1
            or data["identity"] != self.identity
            or not isinstance(data["manager_sources"], list)
            or not isinstance(data["manager_gaps"], dict)
        ):
            raise ValueError("retained accounting identity or schema is invalid")
        if any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in data["manager_gaps"].items()
        ):
            raise ValueError("invalid retained coverage gaps")
        self._validate_sources(data["manager_sources"])
        snapshot = data["worker_snapshot"]
        if snapshot is not None:
            if (
                not isinstance(snapshot, dict)
                or set(snapshot) != {"captured_at", "sources", "ambiguous_sources", "excluded_ids"}
                or not _valid_cutoff(snapshot["captured_at"])
                or not isinstance(snapshot["ambiguous_sources"], list)
                or not isinstance(snapshot["excluded_ids"], list)
                or any(
                    not isinstance(v, str)
                    for field in ("ambiguous_sources", "excluded_ids")
                    for v in snapshot[field]
                )
            ):
                raise ValueError("invalid worker snapshot")
            self._validate_sources(snapshot["sources"])
        return data

    @staticmethod
    def _validate_sources(sources):
        if not isinstance(sources, list):
            raise ValueError("invalid accounting sources")
        seen = set()
        for source in sources:
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("source_id"), str)
                or not isinstance(source.get("records"), list)
            ):
                raise ValueError("invalid accounting source")
            if source["source_id"] in seen:
                raise ValueError("duplicate retained accounting source")
            seen.add(source["source_id"])
            aggregate = source.get("legacy_cost")
            try:
                if aggregate is not None and (
                    not Decimal(str(aggregate)).is_finite() or Decimal(str(aggregate)) < 0
                ):
                    raise ValueError("invalid retained aggregate")
            except InvalidOperation as exc:
                raise ValueError("invalid retained aggregate") from exc
            # Validate calculations independently of projection; malformed retained
            # evidence is an error rather than a silently discarded amount.
            for record in source["records"]:
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("invocation_id"), str)
                    or not record["invocation_id"]
                ):
                    raise ValueError("invalid retained invocation")
            summarize_cost(
                source["records"],
                legacy_cost=source.get("legacy_cost"),
                legacy_residual=source.get("legacy_residual"),
            )

    def read(self):
        with self.locked():
            return self._read()

    def _write(self, data):
        self._validate_sources(data["manager_sources"])
        content = canonical_json(data)
        if len(content) > MAX_ACCOUNTING_BYTES:
            raise ValueError("retained accounting exceeds lossless storage limit")
        if self.path.is_symlink():
            raise ValueError("unsafe accounting destination")
        atomic_write_bytes(self.path, content)
        os.chmod(self.path, 0o600)


class ManagerUsageSink:
    def __init__(self, store, caller):
        self.store, self.caller = store, caller
        self.current = caller
        self.observed = False

    def __call__(self, usage):
        self.observed = True
        raw = usage.model_dump(exclude_unset=True)
        with self.store.locked(write=True):
            data = self.store._read()
            sources = data["manager_sources"]
            source = next((s for s in sources if s["source_id"] == self.current), None)
            if source is None:
                source = dict(source_id=self.current, records=[], legacy_cost=None, gap=False)
                sources.append(source)
            known = {r["invocation_id"]: r for s in sources for r in s["records"]}
            for record in raw.get("cost_records", []):
                if (
                    record["invocation_id"] in known
                    and known[record["invocation_id"]] != record
                    and "native_usage" not in record
                ):
                    raise ValueError("conflicting retained invocation evidence")
            source["records"] = merge_cost_records(source["records"], raw.get("cost_records", []))
            # Repeated provider telemetry is the same evidence, never extra spend.
            if "total_cost_usd" in raw:
                source["legacy_cost"] = raw["total_cost_usd"]
            source["gap"] = not bool(source["records"])
            data["manager_gaps"].pop(self.current, None)
            self.store._write(data)

    def accounting_scope(self):
        from cafe.agents.transport_types import AccountingScope

        return AccountingScope(self.store.identity["workflow_id"], self.current, self)

    def gap(self, identity=None):
        with self.store.locked(write=True):
            data = self.store._read()
            data["manager_gaps"][
                identity or self.current
            ] = "usage unavailable or not yet persisted"
            self.store._write(data)

    @contextmanager
    def attempt(self, identity):
        previous = self.current
        self.current, self.observed = identity, False
        # A process interruption must leave durable unknown coverage.
        self.gap()
        try:
            yield self
        finally:
            if not self.observed:
                self.gap()
            self.current = previous


def manager_usage_sink(project_root, issue_name, workflow_id, caller):
    return ManagerUsageSink(CostStore(project_root, issue_name, workflow_id), caller)


def validate_issue_identity(issue_dir, issue_name, workflow_id):
    issue_dir = Path(issue_dir)
    if issue_dir.name != issue_name:
        raise ValueError("accounting issue identity differs")
    identities = []
    for path in (issue_dir / "audit_events/workflow.json", issue_dir / "blackboard.json"):
        if path.exists() or path.is_symlink():
            identities.append(read_accounting_file(path).get("workflow_id"))
    for role in ("manager", "driver"):
        path = issue_dir / role / "contract.json"
        if path.exists() or path.is_symlink():
            bound = read_accounting_file(path).get("identity", {})
            if bound.get("issue_name") != issue_name:
                raise ValueError("accounting contract issue identity differs")
            identities.append(bound.get("workflow_id"))
    if not identities or any(identity != workflow_id for identity in identities):
        raise ValueError("accounting workflow identity differs or is missing")


def _source_versions(issue_dir):
    return accounting_source_versions(
        issue_dir,
        extra_paths=[
            issue_dir / "blackboard.json",
            issue_dir / "audit_events/workflow.json",
            *(issue_dir / role / "dispatch_state.json" for role in ("manager", "driver")),
        ],
    )


def _stable_sources(issue_dir):
    for _ in range(3):
        before = _source_versions(issue_dir)
        sources = collect_cost_sources(issue_dir)
        if before == _source_versions(issue_dir):
            return sources
    raise ValueError("accounting sources changed during read; retry read after quiescence")


def _attribution(issue_dir, sources, manager_sources, manager_gaps):
    excluded = {r["invocation_id"] for s in manager_sources for r in s["records"]}
    ambiguous = set()
    for source in sources:
        tagged = {
            r.get("invocation_id")
            for r in source.get("records", [])
            if isinstance(r, dict) and r.get("actor") == "manager"
        }
        excluded.update(tagged)
        if tagged or any(
            r.get("invocation_id") in excluded
            for r in source.get("records", [])
            if isinstance(r, dict)
        ):
            ambiguous.add(source["source_id"])
    # Historical callbacks wrote to phase aggregates without actor provenance.
    # Durable event identity identifies the affected phase, but cannot certify
    # individual amounts. Withhold unattributed calls there, retaining raw evidence.
    for role in ("manager", "driver"):
        path = issue_dir / role / "dispatch_state.json"
        if not path.exists() and not path.is_symlink():
            continue
        try:
            state = read_accounting_file(path)
            events = state.get("events", {})
            for key, event in events.items():
                captured = [s["source_id"] for s in manager_sources] + list(manager_gaps)
                if any(identity.startswith(f"callback:{key}:") for identity in captured):
                    continue
                if not event.get("attempts"):
                    continue
                phase = event.get("event", {}).get("step")
                for source in sources:
                    if phase is None or source["source_id"].startswith(f"{phase}/"):
                        ambiguous.add(source["source_id"])
                        excluded.update(
                            r.get("invocation_id")
                            for r in source.get("records", [])
                            if isinstance(r, dict) and not r.get("actor")
                        )
        except (OSError, ValueError, TypeError, AttributeError):
            ambiguous.update(s["source_id"] for s in sources)
            excluded.update(
                r.get("invocation_id")
                for s in sources
                for r in s.get("records", [])
                if isinstance(r, dict) and not r.get("actor")
            )
    return sorted(excluded - {None}), sorted(ambiguous)


def _snapshot(issue_dir, issue_name, workflow_id, manager_sources, manager_gaps):
    for _ in range(3):
        before = _source_versions(issue_dir)
        validate_issue_identity(issue_dir, issue_name, workflow_id)
        sources = _stable_sources(issue_dir)
        excluded, ambiguous = _attribution(issue_dir, sources, manager_sources, manager_gaps)
        validate_issue_identity(issue_dir, issue_name, workflow_id)
        if before == _source_versions(issue_dir):
            return dict(
                captured_at=_utc(),
                sources=sources,
                excluded_ids=excluded,
                ambiguous_sources=ambiguous,
            )
    raise ValueError("accounting identity or attribution changed during read")


def preserve_worker_cost(project_root, issue_dir, issue_name, workflow_id):
    store = CostStore(project_root, issue_name, workflow_id)
    with store.locked(write=True):
        data = store._read()
        validate_project_source(project_root, issue_dir, issue_name)
        snapshot = _snapshot(
            Path(issue_dir), issue_name, workflow_id, data["manager_sources"], data["manager_gaps"]
        )
        # Available corrupt/unreadable sources cannot be discarded before deletion.
        if any(s.get("read_error") for s in snapshot["sources"]):
            raise ValueError("cannot preserve unreadable accounting source")
        store._validate_sources(snapshot["sources"])
        snapshot = _prefer_current(snapshot, data["worker_snapshot"])
        data["worker_snapshot"] = snapshot
        store._write(data)
        if store._read() != data:
            raise ValueError("accounting publication did not verify")
    return snapshot


def _prefer_current(current, retained):
    if retained is None:
        return current
    current = copy.deepcopy(current)
    selected = {s["source_id"]: s for s in current["sources"]}
    for source in retained["sources"]:
        replacement = selected.get(source["source_id"])
        if (
            replacement is None
            or replacement.get("read_error")
            or (not replacement["records"] and replacement.get("legacy_cost") is None)
        ):
            fallback = copy.deepcopy(source)
            fallback["gap"] = True
            selected[source["source_id"]] = fallback
    current["sources"] = list(selected.values())
    current["excluded_ids"] = sorted(set(current["excluded_ids"]) | set(retained["excluded_ids"]))
    current["ambiguous_sources"] = sorted(
        set(current["ambiguous_sources"]) | set(retained["ambiguous_sources"])
    )
    return current


def _worker_summary(snapshot):
    if snapshot is None:
        return summarize_cost([])
    return summarize_sources(
        snapshot["sources"],
        exclude_ids=snapshot["excluded_ids"],
        ambiguous_sources=snapshot["ambiguous_sources"],
    )


def worker_cost(project_root, issue_dir, issue_name, workflow_id):
    store = CostStore(project_root, issue_name, workflow_id)
    validate_project_source(project_root, issue_dir, issue_name)
    data = store.read()
    snapshot = _snapshot(
        Path(issue_dir), issue_name, workflow_id, data["manager_sources"], data["manager_gaps"]
    )
    return _worker_summary(_prefer_current(snapshot, data["worker_snapshot"]))


def _archive(project_root, issue_name):
    result = subprocess.run(
        ["git", "-C", str(project_root), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    root = Path(result.stdout.splitlines()[0].removeprefix("worktree "))
    key = str(root.resolve()).lstrip("/").replace("/", "-")
    return Path.home() / ".cafe/projects" / key / "archived" / issue_name


def inclusive_report(project_root, issue_name, workflow_id, *, issue_dir=None):
    store = CostStore(project_root, issue_name, workflow_id)
    with store.locked():
        data = store._read()
        candidates = (
            [Path(issue_dir)]
            if issue_dir is not None
            else [
                Path(project_root) / ".cafe/issues" / issue_name,
                _archive(project_root, issue_name),
            ]
        )
        snapshot = data["worker_snapshot"]
        for candidate in candidates:
            if not candidate.exists() and not candidate.is_symlink():
                continue
            # Explicit or conflicting evidence must not silently select another run.
            validate_project_source(project_root, candidate, issue_name)
            validate_issue_identity(candidate, issue_name, workflow_id)
            snapshot = _prefer_current(
                _snapshot(
                    candidate,
                    issue_name,
                    workflow_id,
                    data["manager_sources"],
                    data["manager_gaps"],
                ),
                snapshot,
            )
            break
        captured_at = _utc()
        worker = _worker_summary(snapshot)
        manager_sources = list(data["manager_sources"])
        if snapshot:
            for source in snapshot["sources"]:
                tagged = [r for r in source["records"] if r.get("actor") == "manager"]
                if tagged:
                    manager_sources.append(
                        dict(source_id=f"identified:{source['source_id']}", records=tagged)
                    )
        manager = summarize_sources(manager_sources)
        if data["manager_gaps"]:
            manager = combine_cost_summaries([manager, summarize_cost([])])
        # Remove physical overlaps even when historical worker sources contain
        # retained Manager records. Snapshot residual uncertainty is preserved.
        if snapshot:
            snapshot = copy.deepcopy(snapshot)
            snapshot["excluded_ids"] = sorted(
                set(snapshot["excluded_ids"])
                | {r["invocation_id"] for s in data["manager_sources"] for r in s["records"]}
            )
            worker = _worker_summary(snapshot)
        return dict(
            captured_at=captured_at,
            worker=worker,
            manager=manager,
            combined=combine_cost_summaries([worker, manager]),
            limitations=["active/unpersisted Manager usage is outside this cutoff"],
            identity=store.identity,
        )


def accounted_call(sink, identity, operation, *args, **kwargs):
    """Retain an unknown boundary even when the transport never calls on_usage."""
    if sink is None:
        return operation(*args, **kwargs)
    with sink.attempt(identity):
        import inspect

        if "accounting_scope" in inspect.signature(operation).parameters:
            kwargs.setdefault("accounting_scope", sink.accounting_scope())
        return operation(*args, **kwargs)


def format_summary(summary, locale="en-US"):
    """Localize accounting provenance without consulting a mutable price source."""
    from cafe.core.runtime_locales import render_text

    parts = []
    for kind in ("reported", "estimated", "legacy"):
        if summary["counts"][kind] and (summary["known"] or not summary["incomplete"]):
            label = render_text(f"manager.progress.cost.{kind}", locale=locale)
            parts.append(f"${summary[kind]:.4f} {label}")
    if not parts:
        parts.append(render_text("manager.progress.cost.unknown", locale=locale))
    if summary["incomplete"]:
        parts.append(render_text("manager.progress.cost.partial", locale=locale))
    if summary["stale"]:
        parts.append(render_text("manager.progress.cost.stale", locale=locale))
    return " + ".join(parts)


def worker_footer(issue_dir=None, *, project_root=None, locale="en-US"):
    from cafe.core.runtime_locales import render_text

    summary = summarize_cost([])
    if issue_dir is not None:
        issue_dir = Path(issue_dir)
        try:
            if project_root is None:
                project_root = next(p for p in issue_dir.parents if (p / ".git").exists())
            identity = read_accounting_file(issue_dir / "blackboard.json")["workflow_id"]
            summary = worker_cost(project_root, issue_dir, issue_dir.name, identity)
        except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError):
            summary = summarize_cost([])
    return render_text(
        "manager.progress.cost_footer", locale=locale, cost=format_summary(summary, locale)
    )


@contextmanager
def quiescent_worker(issue_dir):
    """Hold the existing advancement lock; never stop or take over a worker."""
    with open_lock_file(Path(issue_dir) / ".workflow-advancement.lock") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("worker is not quiescent; cleanup cannot dispatch") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validate_project_source(project_root, issue_dir, issue_name):
    """Admit only this Git project or its exact lifecycle archive."""
    directory = Path(issue_dir).absolute()
    if directory == _archive(project_root, issue_name).absolute():
        return
    if common_dir(directory) != common_dir(project_root):
        raise ValueError("accounting source belongs to another Git project")


@contextmanager
def _native_claim_lock(store, root_session_id):
    """Serialize root claims; evidence remains in existing accounting envelopes."""
    from hashlib import sha256

    store._safe(create=True)
    key = sha256(root_session_id.encode()).hexdigest()
    path = store.common / "cafe/costs" / f"native-{key}.lock"
    with open_lock_file(path) as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def native_delegation_begin(store, binding, correlation, home):
    """Persist entry before submission. Binding validation belongs to the adapter."""
    import time

    from cafe.agents.cli.codex_subagent_usage import NativeInterval
    from cafe.agents.transport_types import _validated_evidence_scalar

    _validated_evidence_scalar(correlation)
    root = binding["root_session_id"]
    with _native_claim_lock(store, root):
        deadline, read_bytes = time.monotonic() + 3, 0
        for index, path in enumerate((store.common / "cafe/costs").glob("*/*.json")):
            if index >= 512 or time.monotonic() > deadline:
                raise ValueError("native claim validation exceeds bound")
            read_bytes += path.lstat().st_size
            if read_bytes > MAX_ACCOUNTING_BYTES:
                raise ValueError("native claim evidence exceeds bound")
            data = read_accounting_file(path)
            for source in data.get("manager_sources", []):
                claim = source.get("native_delegation")
                if claim and claim["binding"]["root_session_id"] == root and not claim["finalized"]:
                    if data["identity"] == store.identity and source["source_id"] == correlation:
                        if claim["binding"] == binding:
                            return dict(status="open", correlation=correlation, replay=True)
                    raise ValueError("native root already has an open delegation claim")
        native = NativeInterval(
            home,
            workflow_id=store.identity["workflow_id"],
            caller_id=correlation,
            attempt_id=correlation,
            root_session_id=root,
            require_causal=True,
        )
        from cafe.core.cost import prepare_cost_accounting

        pricing = prepare_cost_accounting("codex", os.environ)
        with store.locked(write=True):
            data = store._read()
            if any(s["source_id"] == correlation for s in data["manager_sources"]):
                raise ValueError("native correlation already finalized or used")
            data["manager_sources"].append(
                dict(
                    source_id=correlation,
                    records=[native.open_record()],
                    legacy_cost=None,
                    gap=True,
                    native_delegation=dict(
                        binding=binding,
                        checkpoint=native.checkpoint(),
                        rate=pricing.native_state,
                        finalized=False,
                    ),
                )
            )
            data["manager_gaps"][correlation] = "native delegation open at entry"
            store._write(data)
        return dict(status="open", correlation=correlation)


def native_delegation_finalize(store, binding, correlation, home):
    """Freeze an as-of prefix, including available partial work, without waiting."""
    from cafe.agents.cli.codex_subagent_usage import NativeInterval

    with _native_claim_lock(store, binding["root_session_id"]), store.locked(write=True):
        data = store._read()
        source = next((s for s in data["manager_sources"] if s["source_id"] == correlation), None)
        if source is None or "native_delegation" not in source:
            raise ValueError("native delegation has no recorded entry boundary")
        claim = source["native_delegation"]
        if claim["binding"] != binding:
            raise ValueError("native delegation workflow binding changed")
        if claim["finalized"]:
            return dict(status="finalized", correlation=correlation, replay=True)
        native = NativeInterval(
            home,
            workflow_id=store.identity["workflow_id"],
            caller_id=correlation,
            attempt_id=correlation,
            root_session_id=binding["root_session_id"],
            checkpoint=claim["checkpoint"],
            require_causal=True,
        )
        native.rate = claim["rate"]
        records = native.collect(final=True)
        source["records"] = merge_cost_records(source["records"], records)
        source["gap"] = any(not r["complete"] for r in source["records"])
        claim["finalized"] = True
        if source["gap"]:
            data["manager_gaps"][correlation] = "native delegation partial at cutoff"
        else:
            data["manager_gaps"].pop(correlation, None)
        store._write(data)
        return dict(status="finalized", correlation=correlation, partial=source["gap"])

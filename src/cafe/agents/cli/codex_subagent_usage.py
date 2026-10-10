"""Bounded, read-only Codex descendant collection for caller-admitted work.

The native index is discovery only. Each child identity, turn, cumulative
endpoint and model is validated against its journal before accounting.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from cafe.agents.cli.codex_usage import _find_journal, _open_journal
from cafe.core.native_accounting import COUNTERS, interval_delta, normalized_counters, physical_id

SUPPORTED_VERSION = "0.159.3"
MAX_NODES = 256
MAX_DEPTH = 16
MAX_FILES = 8192
MAX_LINE = 256 * 1024
MAX_JOURNAL = 4 * 1024 * 1024
MAX_BYTES = 16 * 1024 * 1024
MAX_SECONDS = 3


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("unqualified native timestamp")
    return result


def session_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid native identity")
    return value


class NativeInterval:
    """An explicit exclusive caller bracket, never inferred from a directory."""

    def __init__(
        self,
        home,
        *,
        workflow_id,
        caller_id,
        attempt_id,
        root_session_id=None,
        started_at=None,
        checkpoint=None,
        require_causal=False,
    ):
        self.home = Path(os.path.abspath(home))
        self.workflow_id, self.caller_id, self.attempt_id = workflow_id, caller_id, attempt_id
        self.root = session_id(root_session_id) if root_session_id else None
        self.started = started_at or datetime.now(timezone.utc)
        self.baselines = {}
        self.entry_gaps = []
        self.rate = None
        self.require_causal = require_causal
        if checkpoint is not None:
            self.started = instant(checkpoint["started_at"])
            self.baselines = checkpoint["baselines"]
            self.entry_gaps = checkpoint["entry_gaps"]
        elif self.root:
            try:
                sources, gaps = self._discover()
                self.entry_gaps.extend(gaps)
                for identity, path in sources.items():
                    meta, rows, boundary = self._read(path)
                    latest = {}
                    counter_source = "token_count"
                    active = None
                    for row in rows:
                        payload = row["payload"]
                        if (
                            row["type"] == "token_usage_record"
                            and payload.get("thread_id") == identity
                        ):
                            latest = payload.get("thread_token_usage") or latest
                            counter_source = "token_usage_record"
                        elif (
                            payload.get("type") == "token_count" and counter_source == "token_count"
                        ):
                            latest = payload.get("info", {}).get("total_token_usage") or latest
                        if (
                            row["type"] == "token_usage_record"
                            and payload.get("thread_id") == identity
                        ):
                            active = payload.get("turn_id")
                        if payload.get("type") == "task_started":
                            active = payload.get("turn_id")
                        if payload.get("type") in {"task_complete", "turn_aborted"}:
                            active = None
                    self.entry_gaps.extend(boundary.get("source_gaps", []))
                    self.baselines[identity] = dict(
                        boundary, counters=latest, active_turn=active, counter_source=counter_source
                    )
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                self.entry_gaps.append("entry_unavailable")

    def checkpoint(self):
        return dict(
            started_at=self.started.isoformat(),
            baselines=self.baselines,
            entry_gaps=self.entry_gaps,
        )

    def bind(self, root):
        root = session_id(root)
        if self.root is not None and self.root != root:
            raise ValueError("native root mismatch")
        self.root = root

    def _safe_path(self, path):
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.home) or path.resolve() != path:
            raise ValueError("unsafe native source path")
        if any(parent.is_symlink() for parent in (self.home, *self.home.parents)):
            raise ValueError("unsafe native source home")
        return path

    def _header(self, path):
        path = self._safe_path(path)
        if getattr(self, "header_bytes", 0) >= MAX_BYTES:
            raise ValueError("native metadata read exceeds bound")
        with _open_journal(path) as handle:
            line = handle.readline(MAX_LINE + 1)
        if len(line) > MAX_LINE or not line.endswith(b"\n"):
            raise ValueError("invalid native metadata")
        self.header_bytes = getattr(self, "header_bytes", 0) + len(line)
        if self.header_bytes > MAX_BYTES:
            raise ValueError("native metadata read exceeds bound")
        row = json.loads(line)
        if row.get("type") != "session_meta" or not isinstance(row.get("payload"), dict):
            raise ValueError("invalid native metadata")
        meta = row["payload"]
        session_id(meta.get("id"))
        if not path.name.endswith(f"-{meta['id']}.jsonl"):
            raise ValueError("native filename identity mismatch")
        return meta

    @staticmethod
    def _parent(meta):
        source = meta.get("source")
        if not isinstance(source, dict):
            return None
        spawn = source.get("subagent", {}).get("thread_spawn", {})
        parent = spawn.get("parent_thread_id")
        if parent:
            session_id(parent)
        if meta.get("parent_thread_id") and meta["parent_thread_id"] != parent:
            raise ValueError("native ancestry conflict")
        return parent

    def _indexed(self):
        """Schema capabilities, parameterized reads, no migrations or writes."""
        databases = sorted(self.home.glob("state*.sqlite"))
        if len(databases) > 8:
            raise ValueError("native index discovery exceeds bound")
        for path in reversed(databases):
            self._safe_path(path)
            with sqlite3.connect(
                path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0.05
            ) as db:
                db.set_progress_handler(lambda: int(time.monotonic() > self.deadline), 1000)
                edges = {r[1] for r in db.execute("PRAGMA table_info(thread_spawn_edges)")}
                columns = {r[1] for r in db.execute("PRAGMA table_info(threads)")}
                if not {"parent_thread_id", "child_thread_id"} <= edges or "id" not in columns:
                    continue
                locator = next((v for v in ("rollout_path", "session_path") if v in columns), None)
                if locator is None:
                    continue
                found, parents, pending = {}, {}, [(self.root, 0)]
                while pending:
                    parent, depth = pending.pop()
                    if depth >= MAX_DEPTH:
                        raise ValueError("native tree depth exceeds bound")
                    rows = db.execute(
                        "SELECT child_thread_id FROM thread_spawn_edges "
                        "WHERE parent_thread_id = ? LIMIT ?",
                        (parent, MAX_NODES + 1),
                    ).fetchall()
                    for (child,) in rows:
                        session_id(child)
                        if child in parents or child == self.root:
                            raise ValueError("native index cycle or conflicting edge")
                        parents[child] = parent
                        if len(parents) > MAX_NODES:
                            raise ValueError("native tree nodes exceed bound")
                        row = db.execute(
                            f"SELECT {locator} FROM threads WHERE id = ? LIMIT 2", (child,)
                        ).fetchall()
                        if len(row) != 1:
                            raise ValueError("native index identity unavailable")
                        candidate = self._safe_path(row[0][0])
                        meta = self._header(candidate)
                        if meta["id"] != child or self._parent(meta) != parent:
                            raise ValueError("native index ancestry mismatch")
                        found[child] = candidate
                        pending.append((child, depth + 1))
                return found
        return None

    def _discover(self):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.header_bytes = 0
        gaps, indexed = [], None
        try:
            indexed = self._indexed() if self.root else None
        except (OSError, ValueError, TypeError, sqlite3.Error):
            gaps.append("native_index_unavailable")
        # Header fallback also finds the root and validates coverage/index omission.
        metadata, visited = {}, 0
        if indexed is not None:
            for identity, path in indexed.items():
                metadata[identity] = (path, self._header(path))
            try:
                path = _find_journal(self.home, self.root)
                metadata[self.root] = (path, self._header(path))
            except (OSError, ValueError):
                gaps.append("root_native_source_unavailable")
        pending = [self.home / "sessions", self.home / "archived_sessions"]
        while pending:
            directory = pending.pop()
            if directory.is_symlink():
                gaps.append("unsafe_native_directory")
                continue
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > MAX_FILES or time.monotonic() > self.deadline:
                            gaps.append("native_discovery_bound")
                            pending.clear()
                            break
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.name.endswith(".jsonl"):
                            if indexed is not None:
                                if any(
                                    Path(entry.path) == value[0]
                                    for value in metadata.values()
                                    if value
                                ):
                                    continue
                                if (
                                    entry.stat(follow_symlinks=False).st_mtime
                                    < self.started.timestamp()
                                ):
                                    continue
                            try:
                                meta = self._header(Path(entry.path))
                                identity = meta["id"]
                                if identity in metadata:
                                    gaps.append("duplicate_native_identity")
                                    metadata[identity] = None
                                else:
                                    metadata[identity] = (Path(entry.path), meta)
                            except (OSError, ValueError, TypeError, KeyError):
                                gaps.append("native_metadata_unavailable")
            except FileNotFoundError:
                continue
            except OSError:
                gaps.append("native_directory_unavailable")
        found = {}
        if self.root in metadata and metadata[self.root] is not None:
            found[self.root] = metadata[self.root][0]
        else:
            gaps.append("root_native_source_unavailable")
        parents = {
            identity: self._parent(value[1]) for identity, value in metadata.items() if value
        }
        for identity, value in metadata.items():
            if not value or identity == self.root:
                continue
            parent, visited = parents.get(identity), {identity}
            for _ in range(MAX_DEPTH):
                if parent == self.root:
                    found[identity] = value[0]
                    break
                if parent is None:
                    break
                if parent in visited:
                    gaps.append("native_ancestry_cycle")
                    break
                visited.add(parent)
                parent = parents.get(parent)
            else:
                gaps.append("native_tree_depth_bound")
            if len(found) > MAX_NODES:
                gaps.append("native_tree_node_bound")
                found.pop(identity)
        if indexed is not None and set(indexed) != set(found) - {self.root}:
            gaps.append("native_index_coverage_disagreement")
        return found, sorted(set(gaps))

    def _read(self, path):
        meta = self._header(path)
        with _open_journal(self._safe_path(path)) as handle:
            file_info = os.fstat(handle.fileno())
            size = file_info.st_size
            observed_bytes = min(size, MAX_JOURNAL)
            if getattr(self, "read_bytes", 0) + observed_bytes > MAX_BYTES:
                raise ValueError("native journal exceeds bounded observation")
            self.read_bytes = getattr(self, "read_bytes", 0) + observed_bytes
            rows = []
            source_gaps = []
            header = handle.readline(MAX_LINE + 1)
            if json.loads(header).get("payload") != meta:
                raise ValueError("native metadata changed during read")
            digest = sha256(header)
            tail_start = max(handle.tell(), size - MAX_JOURNAL)
            if tail_start > handle.tell():
                handle.seek(tail_start)
                discarded = handle.readline(MAX_LINE + 1)
                if len(discarded) > MAX_LINE:
                    raise ValueError("native tail record exceeds bound")
            observed_start = handle.tell()
            while handle.tell() < size:
                if time.monotonic() > self.deadline:
                    raise ValueError("native observation time exceeds bound")
                offset = handle.tell()
                line = handle.readline(min(MAX_LINE + 1, size - offset))
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    source_gaps.append("native_journal_truncated_or_oversized")
                    break
                digest.update(line)
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    source_gaps.append("malformed_native_record")
                    break
                if not isinstance(row, dict):
                    source_gaps.append("malformed_native_record")
                    break
                # Retain only accounting-relevant fields; discard transcript bodies.
                if row.get("type") not in {"event_msg", "turn_context", "token_usage_record"}:
                    continue
                payload = row.get("payload", {})
                if not isinstance(payload, dict):
                    raise ValueError("invalid native accounting payload")
                kind = payload.get("type")
                if row["type"] == "event_msg" and kind not in {
                    "token_count",
                    "task_started",
                    "task_complete",
                    "turn_aborted",
                    "collab_resume_end",
                    "collab_agent_interaction_end",
                    "collab_agent_spawn_end",
                }:
                    continue
                rows.append(
                    dict(
                        type=row["type"],
                        at=instant(row["timestamp"]),
                        offset=offset,
                        end_offset=handle.tell(),
                        payload=payload,
                    )
                )
            handle.seek(max(0, size - 128))
            anchor = sha256(handle.read(128)).hexdigest()
        return (
            meta,
            rows,
            dict(
                offset=size,
                identity=[file_info.st_dev, file_info.st_ino],
                anchor=anchor,
                digest=digest.hexdigest(),
                observed_start=observed_start,
                source_gaps=source_gaps,
            ),
        )

    def _record(self, identity, meta, rows, boundary, cutoff, final, causal, report_cutoff=None):
        gaps = list(boundary.get("source_gaps", []))
        born = instant(meta["timestamp"])
        baseline = self.baselines.get(identity)
        owned = self.started <= born <= cutoff or identity in causal
        if not owned:
            return None
        if meta.get("cli_version") != SUPPORTED_VERSION:
            gaps.append("unsupported_native_version")
        if baseline and baseline.get("active_turn"):
            gaps.append("entry_child_already_active")
        native_records = [
            row
            for row in rows
            if row["type"] == "token_usage_record" and row["payload"].get("thread_id") == identity
        ]
        source_kind = (
            baseline.get("counter_source", "token_count")
            if baseline
            else ("token_usage_record" if native_records else "token_count")
        )
        if baseline is None:
            # Generic fork/resume history can seed cumulative counters. Do not infer zero.
            baseline = dict(offset=0, anchor="birth", counters={})
            verified_birth = (
                self.started <= born <= cutoff and meta.get("cli_version") == SUPPORTED_VERSION
            )
            if source_kind == "token_usage_record" and verified_birth:
                # 0.159.3 spawn filtering removes inherited per-thread records;
                # a new child starts its own response accounting from zero.
                baseline["counters"] = dict.fromkeys(COUNTERS, 0)
            elif not meta.get("forked_from_id") and not meta.get("history_base") and verified_birth:
                baseline["counters"] = dict.fromkeys(COUNTERS, 0)
            elif (
                meta.get("forked_from_id")
                and verified_birth
                and boundary.get("observed_start", 0) < MAX_LINE
            ):
                inherited = [
                    row
                    for row in rows
                    if row["at"] < born and row["payload"].get("type") == "token_count"
                ]
                if inherited:
                    prior = inherited[-1]
                    baseline = dict(
                        offset=prior["end_offset"],
                        anchor="inherited",
                        counters=prior["payload"].get("info", {}).get("total_token_usage") or {},
                    )
        start = dict(
            at=self.started.isoformat(),
            offset=baseline["offset"],
            anchor=baseline["anchor"],
            counters=normalized_counters(baseline["counters"])[0],
        )
        latest, models, turns, active = {}, set(), [], None
        cutoff_offset = baseline["offset"]
        last_at = self.started
        prior_counters = start["counters"]
        for row in rows:
            if row["offset"] < baseline["offset"] or row["at"] > cutoff or row["at"] < self.started:
                continue
            payload = row["payload"]
            kind = payload.get("type")
            cutoff_offset = row["end_offset"]
            last_at = row["at"]
            if kind == "task_started":
                if active is not None:
                    gaps.append("concurrent_child_turns")
                active = payload.get("turn_id")
                if active:
                    turns.append(active)
            if row["type"] == "turn_context":
                model = payload.get("model")
                if isinstance(model, str) and 0 < len(model) <= 512:
                    models.add(model)
            own_record = (
                row["type"] == "token_usage_record" and payload.get("thread_id") == identity
            )
            if own_record:
                turn_id = payload.get("turn_id")
                if turn_id and turn_id not in turns:
                    turns.append(turn_id)
                    active = turn_id
            if (kind == "token_count" and source_kind == "token_count") or (
                own_record and source_kind == "token_usage_record"
            ):
                info = payload.get("info") or {}
                raw = (
                    payload.get("thread_token_usage")
                    if own_record
                    else info.get("total_token_usage")
                )
                current, invalid = normalized_counters(raw)
                gaps.extend(invalid)
                if any(current.get(k, v) < v for k, v in prior_counters.items()):
                    gaps.append("counter_reset")
                latest = current
                prior_counters = current
            if kind in {"task_complete", "turn_aborted"}:
                if kind == "turn_aborted":
                    gaps.append("child_interrupted")
                if payload.get("turn_id") == active:
                    active = None
        if boundary.get("observed_start", 0) > max(baseline["offset"], MAX_LINE):
            gaps.append("journal_range_incomplete")
        if not turns:
            gaps.append("owned_turn_unavailable")
        if active:
            gaps.append("child_active_at_cutoff")
        if not latest:
            gaps.append("endpoint_unavailable")
        delta, more = interval_delta(start["counters"], latest)
        gaps.extend(more)
        if any(
            g in gaps
            for g in (
                "unsupported_native_version",
                "entry_child_already_active",
                "counter_reset",
                "concurrent_child_turns",
                "owned_turn_unavailable",
            )
        ):
            delta = {}
        if len(models) != 1:
            gaps.append("actual_model_unavailable")
        parent = self._parent(meta)
        segment = physical_id(identity, start)
        inclusion = (
            "exclusive"
            if (
                meta.get("cli_version") == SUPPORTED_VERSION
                and getattr(self, "root_version", None) == SUPPORTED_VERSION
            )
            else "unknown"
        )
        native = dict(
            version=1,
            segment_id=segment,
            kind="child",
            session_id=identity,
            root_session_id=self.root,
            parent_session_id=parent,
            agent_path=meta.get("source", {})
            .get("subagent", {})
            .get("thread_spawn", {})
            .get("agent_path"),
            workflow_id=self.workflow_id,
            caller_id=self.caller_id,
            attempt_id=self.attempt_id,
            cli_version=meta.get("cli_version"),
            inclusion=inclusion,
            status="final" if final else "progress",
            start=start,
            end=dict(
                at=(report_cutoff or cutoff).isoformat(),
                observed_at=last_at.isoformat(),
                offset=cutoff_offset,
                counters=latest,
            ),
            ownership_cutoff=cutoff.isoformat(),
            turn_ids=sorted(set(turns)),
            known_fields=sorted(delta),
            gaps=sorted(set(gaps)),
            source=dict(
                kind="codex_jsonl",
                counter_source=source_kind,
                locator=str(boundary["path"]),
                digest=boundary["digest"],
                semantics="codex-rust-v0.159.3",
            ),
        )
        usage = {k: v for k, v in delta.items() if k != "total_tokens"}
        from cafe.core.cost import account_cost
        from cafe.core.types import TokenUsage

        priced = account_cost(
            TokenUsage(**usage),
            cli="codex",
            model=next(iter(models)) if len(models) == 1 else None,
            state=self.rate,
            invocation_id="native:" + segment,
            complete=not bool(gaps),
            valuation_date=self.started.date().isoformat(),
        )
        result = priced.cost_records[-1]
        result.update(session_id=identity, native_usage=native, model_source="native_turn_context")
        result["usage"] = delta
        if gaps and any(g.startswith(("invalid_", "reset_", "unknown_cache_write")) for g in gaps):
            result.update(
                amount_usd=None, provenance="unavailable", reason="incomplete_native_categories"
            )
        return result

    def collect(self, *, cutoff=None, final=False):
        cutoff = cutoff or datetime.now(timezone.utc)
        self.read_bytes = 0
        records, gaps = [], list(self.entry_gaps)
        if not self.root:
            gaps.append("root_identity_unavailable")
            sources = {}
        else:
            try:
                sources, more = self._discover()
                gaps.extend(more)
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                sources = {}
                gaps.append("native_discovery_unavailable")
        data, causal = {}, {}
        for identity, path in sources.items():
            try:
                meta, rows, boundary = self._read(path)
                entry = self.baselines.get(identity)
                if entry and boundary.get("observed_start", 0) > entry["offset"]:
                    gaps.append("owned_journal_range_unavailable")
                if entry and entry.get("identity") != boundary.get("identity"):
                    raise ValueError("native entry source replaced")
                if entry and entry["offset"]:
                    with _open_journal(path) as handle:
                        handle.seek(max(0, entry["offset"] - 128))
                        if (
                            sha256(handle.read(min(128, entry["offset"]))).hexdigest()
                            != entry["anchor"]
                        ):
                            raise ValueError("native entry replaced or truncated")
                boundary["path"] = path.relative_to(self.home).as_posix()
                gaps.extend(boundary.get("source_gaps", []))
                data[identity] = meta, rows, boundary
            except (OSError, ValueError, TypeError, KeyError):
                gaps.append("native_observation_unavailable")
        self.root_version = data[self.root][0].get("cli_version") if self.root in data else None
        if self.root_version != SUPPORTED_VERSION:
            gaps.append("root_inclusion_unavailable")
        # Host admission belongs to the exact entry turn, not its persistent session.
        ownership_cutoff = cutoff
        if self.require_causal:
            owner_turn = self.baselines.get(self.root, {}).get("active_turn")
            if owner_turn is None:
                gaps.append("host_owned_turn_unavailable")
            for row in data.get(self.root, ({}, [], {}))[1]:
                if not self.started <= row["at"] <= cutoff:
                    continue
                payload = row["payload"]
                kind, turn = payload.get("type"), payload.get("turn_id")
                if owner_turn is not None and (
                    (kind in {"task_complete", "turn_aborted"} and turn == owner_turn)
                    or (kind == "task_started" and turn != owner_turn)
                ):
                    ownership_cutoff = min(ownership_cutoff, row["at"])
                    gaps.append("host_owner_turn_ended")
                    break
        # A resumed old child needs explicit causal submission from owned parent work.
        admitted = {self.root}
        for _ in range(MAX_DEPTH):
            changed = False
            for parent in list(admitted):
                if parent not in data:
                    continue
                active_turn = self.baselines.get(parent, {}).get("active_turn")
                owner_turn = active_turn
                for row in data[parent][1]:
                    if row["at"] > ownership_cutoff:
                        continue
                    kind = row["payload"].get("type")
                    if kind == "task_started":
                        active_turn = row["payload"].get("turn_id")
                    if kind in {"task_complete", "turn_aborted"}:
                        active_turn = None
                    if not self.started <= row["at"] <= cutoff:
                        continue
                    if (
                        self.require_causal
                        and parent == self.root
                        and (owner_turn is None or active_turn != owner_turn)
                    ):
                        continue
                    p = row["payload"]
                    if p.get("type") in {"collab_resume_end", "collab_agent_interaction_end"}:
                        target = p.get("receiver_thread_id")
                        if target in data and self._parent(data[target][0]) == parent:
                            causal[target] = parent
                    if p.get("type") == "collab_agent_spawn_end":
                        target = p.get("new_thread_id")
                        if target in data and self._parent(data[target][0]) == parent:
                            causal[target] = parent
                for child, (meta, _, _) in data.items():
                    if (
                        child not in admitted
                        and self._parent(meta) == parent
                        and (
                            (
                                not self.require_causal
                                and self.started <= instant(meta["timestamp"]) <= cutoff
                            )
                            or child in causal
                        )
                    ):
                        admitted.add(child)
                        changed = True
            if not changed:
                break
        for identity in sorted(admitted - {self.root}):
            try:
                record = self._record(
                    identity, *data[identity], ownership_cutoff, final, causal, report_cutoff=cutoff
                )
                if record:
                    records.append(record)
            except (ValueError, TypeError, KeyError):
                gaps.append("child_interval_unavailable")
        records.insert(0, self.open_record(cutoff, final=final, gaps=gaps))
        return records

    def open_record(self, cutoff=None, *, final=False, gaps=None):
        segment = physical_id("scope:" + self.attempt_id, dict(at=self.started.isoformat()))
        gaps = sorted(set(gaps or ([] if final else ["collection_open"])))
        if self.require_causal:
            gaps = sorted(set([*gaps, "host_parent_usage_unavailable"]))
        return dict(
            invocation_id="native:" + segment,
            cli="codex",
            model=None,
            session_id=self.root,
            currency="USD",
            provenance="unavailable",
            amount_usd=None,
            complete=final and not gaps,
            reason="native_coverage",
            usage={},
            native_usage=dict(
                version=1,
                segment_id=segment,
                kind="scope",
                session_id=self.root,
                root_session_id=self.root,
                parent_session_id=None,
                workflow_id=self.workflow_id,
                caller_id=self.caller_id,
                attempt_id=self.attempt_id,
                inclusion="unknown",
                cli_version=getattr(self, "root_version", None),
                status="final" if final else "open",
                start=dict(at=self.started.isoformat(), offset=0, counters={}),
                end=dict(at=(cutoff or self.started).isoformat(), offset=0, counters={}),
                gaps=gaps,
                source=dict(kind="codex_jsonl"),
            ),
        )

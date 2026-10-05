"""Codex's native stream-activity adapter; retain no prompts or streamed text."""

import json
import re
import secrets
from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from time import time as wall_time

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


class CodexStreamActivity:
    """Run a bounded, loopback-only receiver for Codex's native OTel events."""

    # Native exporters batch 512 records; ordinary batches exceed one MiB.
    MAX_REQUEST_BYTES = 8 * 1_048_576

    def __init__(self):
        self._lock = Lock()
        self._pending = deque(maxlen=64)
        self._session = None
        self._latest_timestamp = 0.0
        self._latest_kinds = set()
        self._accepted = 0
        self._metric_points = {}
        self._rejected_requests = 0
        self._max_request_bytes = 0
        self._path = f"/{secrets.token_hex(24)}/v1/logs"
        self._server = None
        self._thread = None

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.connection.settimeout(1)
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    with owner._lock:
                        owner._max_request_bytes = max(owner._max_request_bytes, size)
                    if self.path != owner._path or not 0 < size <= owner.MAX_REQUEST_BYTES:
                        with owner._lock:
                            owner._rejected_requests += 1
                        self.send_error(400)
                        return
                    owner.receive(json.loads(self.rfile.read(size)))
                except (OSError, ValueError, TypeError, RecursionError):
                    with owner._lock:
                        owner._rejected_requests += 1
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_args):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def command(self, cmd, environment):
        """Add invocation-only export settings; never overwrite an existing exporter."""
        ignore_user_config = False
        profile = None
        arguments = iter(cmd[1:])
        for argument in arguments:
            if argument == "--":
                break
            override = None
            if argument == "--ignore-user-config":
                ignore_user_config = True
            elif argument in {"-p", "--profile"}:
                profile = next(arguments, None)
            elif argument.startswith("--profile="):
                profile = argument.removeprefix("--profile=")
            elif argument.startswith("-p") and len(argument) > 2:
                profile = argument[2:].removeprefix("=")
            elif argument in {"-c", "--config"}:
                override = next(arguments, None)
            elif argument.startswith("--config="):
                override = argument.removeprefix("--config=")
            elif argument.startswith("-c") and len(argument) > 2:
                override = argument[2:].removeprefix("=")
            if override:
                key = override.split("=", 1)[0].strip()
                if any(
                    key == name or key.startswith(name + ".")
                    for name in ("otel.exporter", "otel.metrics_exporter")
                ):
                    raise ValueError(
                        "Live Codex stream capture cannot replace an existing telemetry exporter."
                    )
        # Native --ignore-user-config excludes both base and profile files.
        # Otherwise the selected profile's exporter overrides the base value.
        if not ignore_user_config:
            home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
            config_files = [home / "config.toml"]
            if profile:
                config_files.append(home / f"{profile}.config.toml")
            exporter = "none"
            metrics_exporter = "none"
            for config_file in config_files:
                if config_file.exists():
                    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
                    exporter = config.get("otel", {}).get("exporter", exporter)
                    metrics_exporter = config.get("otel", {}).get(
                        "metrics_exporter", metrics_exporter
                    )
            if exporter != "none" or metrics_exporter != "none":
                raise ValueError(
                    "Live Codex stream capture cannot replace an existing telemetry exporter."
                )
        endpoint = f"http://127.0.0.1:{self._server.server_port}{self._path}"
        # WebSocket events are native counters, not log events. The SDK's
        # default 60-second collection interval is unsuitable for liveness.
        # This is the isolated child environment, never the parent's settings.
        environment["OTEL_METRIC_EXPORT_INTERVAL"] = "1000"
        # exec owns config overrides: global flags before exec are ignored by
        # exec --ignore-user-config. Append to the executed subcommand instead.
        return [
            *cmd,
            "-c",
            "otel.exporter={otlp-http={endpoint=" + json.dumps(endpoint) + ',protocol="json"}}',
            "-c",
            "otel.log_user_prompt=false",
            "-c",
            "otel.metrics_exporter={otlp-http={endpoint="
            + json.dumps(endpoint)
            + ',protocol="json"}}',
        ]

    @staticmethod
    def _attributes(record):
        attributes = record.get("attributes", {})
        if isinstance(attributes, list):
            attributes = {
                item["key"]: item.get("value", {})
                for item in attributes
                if isinstance(item, dict) and isinstance(item.get("key"), str)
            }
        if not isinstance(attributes, dict):
            return {}
        return {
            key: value.get("stringValue")
            for key, value in attributes.items()
            if isinstance(value, dict)
        }

    def receive(self, payload):
        """Retain only bounded stream kinds, times and counter fingerprints."""
        if not isinstance(payload, dict):
            return

        def entries(container, key, limit):
            value = container.get(key, [])
            return value[:limit] if isinstance(value, list) else []

        # Native WebSocket telemetry has no conversation label. Its private
        # invocation-only receiver binds these counters to the stdout-verified
        # child session; unrelated metrics and unchanged/replayed counters
        # cannot refresh the watchdog.
        for resource in entries(payload, "resourceMetrics", 128):
            if not isinstance(resource, dict):
                continue
            for scope in entries(resource, "scopeMetrics", 128):
                if not isinstance(scope, dict):
                    continue
                for metric in entries(scope, "metrics", 1024):
                    if (
                        not isinstance(metric, dict)
                        or metric.get("name") != "codex.websocket.event"
                    ):
                        continue
                    total = metric.get("sum")
                    if not isinstance(total, dict) or total.get("isMonotonic") is not True:
                        continue
                    temporality = total.get("aggregationTemporality")
                    if temporality in (1, "AGGREGATION_TEMPORALITY_DELTA"):
                        delta = True
                    elif temporality in (2, "AGGREGATION_TEMPORALITY_CUMULATIVE"):
                        delta = False
                    else:
                        continue
                    for point in entries(total, "dataPoints", 1024):
                        if not isinstance(point, dict):
                            continue
                        attributes = self._attributes(point)
                        kind = attributes.get("kind")
                        if (
                            attributes.get("success") != "true"
                            or not isinstance(kind, str)
                            or not re.fullmatch(r"response\.[a-zA-Z._]{1,96}", kind)
                        ):
                            continue
                        try:
                            values = [
                                point[name]
                                for name in ("timeUnixNano", "startTimeUnixNano", "asInt")
                            ]
                            if any(
                                isinstance(value, bool)
                                or not isinstance(value, (str, int))
                                or not re.fullmatch(r"[0-9]{1,20}", str(value))
                                for value in values
                            ):
                                continue
                            stamp, start, count = map(int, values)
                        except (KeyError, TypeError, ValueError, OverflowError):
                            continue
                        if not 0 < start <= stamp or not 0 < count <= 2**63 - 1:
                            continue
                        seconds = stamp / 1_000_000_000
                        if not -5 <= wall_time() - seconds <= 10:
                            continue
                        # Keep counters from different model/scope series apart
                        # without retaining their attribute values or content.
                        identity = json.dumps(attributes, sort_keys=True)
                        key = (kind, delta, sha256(identity.encode()).digest())
                        with self._lock:
                            previous = self._metric_points.get(key)
                            if previous is not None:
                                old_stamp, old_start, old_count = previous
                                if stamp <= old_stamp:
                                    continue
                                if delta:
                                    # Each positive delta must cover a new,
                                    # non-overlapping collection interval.
                                    if start < old_stamp:
                                        continue
                                elif start < old_start or (
                                    start == old_start and count <= old_count
                                ):
                                    # Fresh timestamps with unchanged cumulative
                                    # counts are not fresh provider activity.
                                    continue
                            if previous is None and len(self._metric_points) >= 128:
                                continue
                            self._metric_points[key] = (stamp, start, count)
                            self._pending.append((None, seconds, "codex.websocket_metric", kind))

        for resource in entries(payload, "resourceLogs", 128):
            if not isinstance(resource, dict):
                continue
            for scope in entries(resource, "scopeLogs", 128):
                if not isinstance(scope, dict):
                    continue
                for record in entries(scope, "logRecords", 1024):
                    if not isinstance(record, dict):
                        continue
                    attributes = self._attributes(record)
                    source = attributes.get("event.name")
                    kind = attributes.get("event.kind")
                    session = attributes.get("conversation.id")
                    timestamp = attributes.get("event.timestamp")
                    if not isinstance(source, str) or source not in {
                        "codex.sse_event",
                        "codex.websocket_event",
                    }:
                        continue
                    if not isinstance(kind, str) or not re.fullmatch(
                        r"response\.[a-zA-Z._]{1,96}", kind
                    ):
                        continue
                    if not isinstance(session, str) or not re.fullmatch(
                        r"[a-zA-Z0-9_-]{1,128}", session
                    ):
                        continue
                    if not isinstance(timestamp, str) or len(timestamp) > 40:
                        continue
                    try:
                        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if instant.tzinfo is None:
                            continue
                        seconds = instant.timestamp()
                    except (ValueError, OverflowError):
                        continue
                    # A delayed/replayed export is not evidence of current activity.
                    if not -5 <= wall_time() - seconds <= 10:
                        continue
                    with self._lock:
                        self._pending.append((session, seconds, source, kind))

    def bind(self, session):
        with self._lock:
            if self._session is None:
                self._session = session

    def drain(self):
        """Coalesce fresh events for the stdout-verified thread into one safe record."""
        with self._lock:
            if self._session is None:
                return None
            accepted = []
            while self._pending:
                session, seconds, source, kind = self._pending.popleft()
                if not -5 <= wall_time() - seconds <= 10:
                    continue
                if source == "codex.websocket_metric" and session is None:
                    session = self._session
                if session != self._session or seconds < self._latest_timestamp:
                    continue
                if seconds > self._latest_timestamp:
                    self._latest_timestamp = seconds
                    self._latest_kinds.clear()
                identity = (source, kind)
                if identity in self._latest_kinds:
                    continue
                if len(self._latest_kinds) >= 128:
                    continue
                self._latest_kinds.add(identity)
                accepted.append((source, kind))
            if not accepted:
                return None
            self._accepted += len(accepted)
            return {
                "type": "cafe.stream_activity",
                "session_id": self._session,
                "source": accepted[-1][0],
                "kind": accepted[-1][1],
                "event_count": len(accepted),
                "total_event_count": self._accepted,
                "timestamp": datetime.fromtimestamp(
                    self._latest_timestamp, timezone.utc
                ).isoformat(),
            }

    def diagnostics(self):
        with self._lock:
            return {
                "stream_activity_events": self._accepted,
                "stream_activity_rejected_requests": self._rejected_requests,
                "stream_activity_max_request_bytes": self._max_request_bytes,
                "stream_activity_last_event_at": (
                    datetime.fromtimestamp(self._latest_timestamp, timezone.utc).isoformat()
                    if self._accepted
                    else None
                ),
            }

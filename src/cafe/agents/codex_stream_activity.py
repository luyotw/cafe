"""Observe Codex transport activity without retaining prompts or streamed text."""

import json
import re
import secrets
from collections import deque
from datetime import datetime, timezone
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

    def __init__(self):
        self._lock = Lock()
        self._pending = deque(maxlen=64)
        self._session = None
        self._latest_timestamp = 0.0
        self._latest_kinds = set()
        self._accepted = 0
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
                    if self.path != owner._path or not 0 < size <= 1_048_576:
                        self.send_error(400)
                        return
                    owner.receive(json.loads(self.rfile.read(size)))
                except (OSError, ValueError, TypeError, RecursionError):
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
        home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        config_file = home / "config.toml"
        if config_file.exists():
            config = tomllib.loads(config_file.read_text(encoding="utf-8"))
            if config.get("otel", {}).get("exporter", "none") != "none":
                raise ValueError(
                    "Live Codex stream capture cannot replace an existing telemetry exporter."
                )
        for index, argument in enumerate(cmd):
            override = None
            if argument in {"-c", "--config"} and index + 1 < len(cmd):
                override = cmd[index + 1]
            elif argument.startswith("--config="):
                override = argument.removeprefix("--config=")
            if override:
                key = override.split("=", 1)[0].strip()
                if key == "otel.exporter" or key.startswith("otel.exporter."):
                    raise ValueError(
                        "Live Codex stream capture cannot replace an existing telemetry exporter."
                    )
        endpoint = f"http://127.0.0.1:{self._server.server_port}{self._path}"
        # exec owns config overrides: global flags before exec are ignored by
        # exec --ignore-user-config. Append to the executed subcommand instead.
        return [
            *cmd,
            "-c",
            "otel.exporter={otlp-http={endpoint=" + json.dumps(endpoint) + ',protocol="json"}}',
            "-c",
            "otel.log_user_prompt=false",
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
        """Discard every field except stream kind, conversation ID and event time."""
        if not isinstance(payload, dict):
            return

        def entries(container, key, limit):
            value = container.get(key, [])
            return value[:limit] if isinstance(value, list) else []

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
            return {"stream_activity_events": self._accepted}

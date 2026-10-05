# Codex stream activity

`codex exec --json` reports complete items. A model can send SSE or WebSocket
deltas while stdout remains quiet. The executor therefore observes Codex's
native `codex.sse_event` and `codex.websocket_event` OTel log events, and
`codex.websocket.event` OTel counters, as well as stdout. Codex 0.160.0
records WebSocket intermediate events as metrics; observing logs alone misses
those deltas. The Codex idle timeout remains 300 seconds; it is not a whole-call
deadline.

The executor depends on the provider-neutral `StreamActivity` protocol. It asks
the selected CLI strategy for `create_stream_activity(cmd)` and owns cleanup,
idle policy and completion checks. The base CLI strategy returns `None`, using
stdout only. `CodexCLI` supplies its native adapter for noninteractive Codex
commands. New CLI activity adapters can implement the same contract without
adding provider branches to the executor.

For each noninteractive Codex invocation, CAFE starts a temporary HTTP receiver
on loopback with an unpredictable URL. It appends invocation-only `-c` options
to the executed subcommand, enabling JSON OTLP log and metric export to that
receiver and disabling user-prompt logging. A copy of the child environment sets
`OTEL_METRIC_EXPORT_INTERVAL=1000` so WebSocket activity is collected every
second instead of the SDK default of 60 seconds. Neither `config.toml` nor the
caller environment is modified. Explicit log and metric export destinations
are not overwritten: initialization fails if an effective exporter or an
exporter override is present. The native default metrics destination is replaced
only for this monitored invocation. Without `--ignore-user-config`,
CAFE checks the base configuration followed by the selected
`$CODEX_HOME/<profile>.config.toml`; the profile's exporter takes precedence.
With `--ignore-user-config`, neither file is loaded, matching the installed
Codex CLI. Explicit exporter overrides are still protected. Interactive Codex
and other CLIs retain their existing execution paths.

Only fresh `response.*` activity counts. Log records must carry the
stdout-verified `thread.started` conversation identity. Native WebSocket
counters have no conversation label: the private per-invocation receiver binds
them to its own child after stdout verifies that child's thread identity. Only
successful `codex.websocket.event` counters qualify. Positive delta counts
must cover new, non-overlapping collection intervals; cumulative counts must
increase, or identify a newer counter start time. Unrelated requests, plugin
events, foreign log conversations, stale/replayed exports, zero deltas and
unchanged cumulative counters cannot keep an attempt alive. The receiver
retains a bounded queue and coalesces events while the executor waits for
stdout. It records only this metadata in `streaming.jsonl`:

```json
{"type":"cafe.stream_activity","session_id":"verified-thread","source":"codex.sse_event","kind":"response.output_text.delta","event_count":3,"total_event_count":10,"timestamp":"2026-10-04T03:00:00+00:00"}
```

Counts describe accepted observations, not exact generated tokens. Transport
activity supplies no response text, usage, acknowledgement or completion
evidence. Only the existing native terminal record can complete a workflow
attempt. Prompt text, deltas, reasoning and tool output are discarded from
telemetry rather than saved in activity records. Explicit duration and output
budgets remain enforced; activity records count toward output budgets.

The receiver closes on success, timeout, process errors and caller exceptions.
Failure diagnostics include the accepted stream-event count alongside stdout
and stderr counters, rejected HTTP requests, the largest request size and
the last accepted activity timestamp. Counter series retain only hashes of
attribute values and bounded numeric state (at most 128 series).
The loopback receiver accepts batches up to eight MiB: the installed CLI's
ordinary 512-record batches are about 1.16 MB and exceeded the original one-MiB
limit. The queue remains bounded and retained activity contains metadata only.

## Validation

`tests/unit/test_codex_stream_activity.py` checks quiet-stdout live streams,
WebSocket delta and cumulative counters, real inactivity, foreign log sessions,
repeated/stale exports, metadata privacy, explicit duration/output limits and telemetry completion without native turn
completion. It also covers isolated decision commands, ignored malformed
configuration, selected-profile exporter precedence and child environment
isolation. It accelerates only the watchdog clock, leaving subprocess and transport clocks unchanged.

A local Responses SSE fixture with the installed Codex CLI 0.160.0 sent one
text delta per second for eight seconds. The original executor timed out after
3.23 real seconds under a 100x watchdog clock. With native activity observation,
the same CLI completed after 8.66 seconds, recording ten stream events before
its native completion. This isolates the monitoring defect; it does not prove
that every historical timeout had a still-active provider or that a paused
workflow has resumed successfully.

A native Codex 0.160.0 WebSocket run confirmed periodic delta-counter exports
after setting the one-second collection interval. Its stdout remained quiet
while `response.output_text.delta` metrics arrived each second. This covers the
transport path absent from the original SSE-only validation. A complete native
WebSocket invocation then finished in 65.15 seconds with 58 accepted activity
records under a 20x watchdog clock (15 real seconds to idle timeout), while
still requiring the native turn-completion record.

Codex's supported telemetry settings and events are documented in
[Advanced Configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry).

## Interrupted sessions

A timeout or incomplete stream can still contain a verified provider thread
identity. Failed-attempt diagnostics retain that CLI/session pair, and the
phase saves the final attempt's pair in its iteration metadata for an exact
user-selected retry. Conflicting or invalid transport evidence is excluded;
the retained identity does not establish completion.

An existing-session recovery choice requires a valid CLI/session pair before
the task is completed. Missing or invalid identity leaves the task pending.
The phase also rejects legacy completed retry choices without a resumable pair,
rather than silently starting a new session. Fresh-session recovery remains
an explicit user-owned choice.

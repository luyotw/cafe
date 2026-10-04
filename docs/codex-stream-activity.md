# Codex stream activity

`codex exec --json` reports complete items. A model can send SSE or WebSocket
deltas while stdout remains quiet. The executor therefore observes Codex's
native `codex.sse_event` and `codex.websocket_event` OTel log events as well as
stdout. The Codex idle timeout remains 300 seconds; it is not a whole-call
deadline.

The executor depends on the provider-neutral `StreamActivity` protocol. It asks
the selected CLI strategy for `create_stream_activity(cmd)` and owns cleanup,
idle policy and completion checks. The base CLI strategy returns `None`, using
stdout only. `CodexCLI` supplies its native adapter for noninteractive Codex
commands. New CLI activity adapters can implement the same contract without
adding provider branches to the executor.

For each noninteractive Codex invocation, CAFE starts a temporary HTTP receiver
on loopback with an unpredictable URL. It appends invocation-only `-c` options
to the executed subcommand, enabling JSON OTLP log export to that receiver and
disabling user-prompt logging. It does not modify `config.toml`. Existing log
export destinations are not overwritten: initialization fails if an effective
exporter or an exporter override is present. Without `--ignore-user-config`,
CAFE checks the base configuration followed by the selected
`$CODEX_HOME/<profile>.config.toml`; the profile's exporter takes precedence.
With `--ignore-user-config`, neither file is loaded, matching the installed
Codex CLI. Explicit exporter overrides are still protected. Interactive Codex
and other CLIs retain their existing execution paths.

Only fresh `response.*` events belonging to the stdout-verified
`thread.started` identity count as activity. Unrelated requests, plugin events,
other conversations, stale exports and repeated events cannot keep an attempt
alive. The receiver retains a bounded queue and coalesces events while the
executor waits for stdout. It records only this metadata in `streaming.jsonl`:

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
and stderr counters.

## Validation

`tests/unit/test_codex_stream_activity.py` checks quiet-stdout live streams,
real inactivity, foreign sessions, repeated/stale exports, metadata privacy,
explicit duration/output limits and telemetry completion without native turn
completion. It also covers isolated decision commands, ignored malformed
configuration and selected-profile exporter precedence. It accelerates only
the watchdog clock, leaving subprocess and transport clocks unchanged.

A local Responses SSE fixture with the installed Codex CLI 0.160.0 sent one
text delta per second for eight seconds. The original executor timed out after
3.23 real seconds under a 100x watchdog clock. With native activity observation,
the same CLI completed after 8.66 seconds, recording ten stream events before
its native completion. This isolates the monitoring defect; it does not prove
that every historical timeout had a still-active provider or that a paused
workflow has resumed successfully.

Codex's supported telemetry settings and events are documented in
[Advanced Configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry).

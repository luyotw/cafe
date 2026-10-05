"""Native cumulative counters become invocation subtotals without invented zeros."""

import json

import pytest

from cafe.agents.cli import codex_usage
from cafe.agents.cli.codex import CodexCLI
from cafe.core.types import AgentCLI, AgentConfig, TokenUsage


@pytest.fixture
def source(tmp_path):
    session = "01a10a7d-c447-7032-a913-d78a3919d35e"
    journal = tmp_path / "sessions/2026/10/05" / f"rollout-test-{session}.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"type": "session_meta", "payload": {"id": session}}) + "\n")
    with journal.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"input_tokens": 100, "output_tokens": 10},
                        },
                    },
                }
            )
            + "\n"
        )
    cli = CodexCLI(AgentConfig(name="fixture", cli=AgentCLI.CODEX, session_id=session))
    return cli, {"CODEX_HOME": str(tmp_path)}, journal


def test_resume_snapshots_are_projected_to_per_turn_deltas(source):
    cli, environment, _journal = source
    observer = cli.prepare_response_accounting(cli.build_command("hello"), environment)
    lines = [
        json.dumps(record)
        for record in [
            {"type": "thread.started", "thread_id": cli.config.session_id},
            {"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 13}},
            {"type": "turn.completed", "usage": {"input_tokens": 150, "output_tokens": 17}},
        ]
    ]
    _reply, usage, _denials = cli.parse_response(lines)
    usage.duration_ms = 7
    observed = observer(usage, lines)
    assert observed.model_dump(exclude_unset=True) == {
        "input_tokens": 50,
        "output_tokens": 7,
        "duration_ms": 7,
        "turn_usages": [
            {"turn": 1, "input_tokens": 20, "output_tokens": 3},
            {"turn": 2, "input_tokens": 30, "output_tokens": 4},
        ],
    }
    assert "cache_read_input_tokens" not in observed.model_fields_set
    assert "total_cost_usd" not in observed.model_fields_set


def test_resume_native_options_keep_the_exact_configured_baseline(source):
    cli, environment, _journal = source
    command = cli.build_command("hello")
    index = command.index("resume") + 1
    command[index:index] = ["--ignore-user-config", "--skip-git-repo-check"]
    observer = cli.prepare_response_accounting(command, environment)
    lines = [json.dumps({"type": "thread.started", "thread_id": cli.config.session_id})]
    usage = observer(TokenUsage(input_tokens=150, output_tokens=13), lines)
    assert usage.input_tokens == 50 and usage.output_tokens == 3


def test_fresh_command_does_not_reuse_a_configured_resume_baseline(source):
    cli, environment, _journal = source
    assert cli.prepare_response_accounting(["codex", "exec", "hello"], environment) is None


@pytest.mark.parametrize("session", ["another-thread", None])
def test_stdout_cannot_borrow_an_unrelated_native_baseline(source, session):
    cli, environment, _journal = source
    observer = cli.prepare_response_accounting(cli.build_command("hello"), environment)
    lines = [json.dumps({"type": "thread.started", "thread_id": session})]
    usage = observer(TokenUsage(input_tokens=150, output_tokens=13, duration_ms=7), lines)
    assert usage.model_dump(exclude_unset=True) == {"duration_ms": 7}


def test_oversized_native_tail_is_unknown_and_preserves_invocation_duration(source, monkeypatch):
    cli, environment, journal = source
    monkeypatch.setattr(codex_usage, "_LINE_BYTES", 256)
    with journal.open("a") as handle:
        handle.write(json.dumps({"type": "response_item", "payload": "private" * 100}) + "\n")
    observer = cli.prepare_response_accounting(cli.build_command("hello"), environment)
    lines = [json.dumps({"type": "thread.started", "thread_id": cli.config.session_id})]
    usage = observer(TokenUsage(input_tokens=150, output_tokens=13, duration_ms=7), lines)
    assert usage.model_dump(exclude_unset=True) == {"duration_ms": 7}


def test_large_journal_uses_only_bounded_tail_counters(source, monkeypatch):
    cli, environment, journal = source
    monkeypatch.setattr(codex_usage, "_TAIL_BYTES", 512)
    with journal.open("a") as handle:
        for _ in range(100):
            handle.write(json.dumps({"type": "response_item", "payload": "private"}) + "\n")
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"input_tokens": 200, "output_tokens": 20},
                        },
                    },
                }
            )
            + "\n"
        )
    observer = cli.prepare_response_accounting(cli.build_command("hello"), environment)
    lines = [json.dumps({"type": "thread.started", "thread_id": cli.config.session_id})]
    usage = observer(TokenUsage(input_tokens=250, output_tokens=23), lines)
    assert usage.input_tokens == 50 and usage.output_tokens == 3

"""Exact invocation accounting for current Copilot and Gemini native formats."""

import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from cafe.agents.cli.codex_usage import _open_journal
from cafe.core.types import TokenUsage

MAX_BYTES = 2 * 1024 * 1024
TOKEN_FIELDS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadTokens": "cache_read_input_tokens",
    "cacheWriteTokens": "cache_write_input_tokens",
    "reasoningTokens": "reasoning_output_tokens",
}


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid provider counter")
    return value


def token_counters(raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid provider usage object")
    return {
        target: integer(raw[source]) for source, target in TOKEN_FIELDS.items() if source in raw
    }


def _read(path):
    with _open_journal(path) as handle:
        content = handle.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Native accounting source exceeds bound")
    return content.decode("utf-8")


def _resume(command):
    for index, option in enumerate(command):
        if option.startswith("--resume="):
            return option.split("=", 1)[1]
        if option in {"--resume", "-r"}:
            return command[index + 1] if index + 1 < len(command) else ""
    return None


def _session(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Expected exact native session UUID")
    return value


def _copilot_models(metrics):
    if not isinstance(metrics, dict):
        raise ValueError("Invalid Copilot metrics")
    models = metrics.get("modelMetrics")
    if not isinstance(models, dict) or not models:
        raise ValueError("Missing Copilot model metrics")
    result = {}
    for model, raw in models.items():
        if not isinstance(model, str) or not model or not isinstance(raw, dict):
            raise ValueError("Invalid Copilot model metrics")
        counters = token_counters(raw.get("usage"))
        if not {
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_write_input_tokens",
        }.issubset(counters):
            raise ValueError("Incomplete Copilot token metrics")
        entry = {"usage": counters}
        if "totalNanoAiu" in raw:
            entry["total_nano_aiu"] = integer(raw["totalNanoAiu"])
        result[model] = entry
    return result


class CopilotUsage:
    """Read the unique output file and subtract a verified pre-launch shutdown.

    modelMetrics and agentMetrics describe overlapping hierarchies. Only the
    former contributes tokens. Native nano-AIU is preserved separately.
    """

    def __init__(self, command, environment):
        self.session_id = _resume(command)
        self.baseline = None
        self.home = Path(environment.get("HOME", str(Path.home()))) / ".copilot"
        self.directory = tempfile.TemporaryDirectory(prefix="cafe-copilot-usage-")
        self.path = Path(self.directory.name) / "usage.json"
        command.extend(["--usage-output-file", str(self.path)])
        if self.session_id is not None:
            try:
                session = _session(self.session_id)
                records = [
                    json.loads(line)
                    for line in _read(
                        self.home / "session-state" / session / "events.jsonl"
                    ).splitlines()
                ]
                if (
                    not records
                    or not isinstance(records[0], dict)
                    or not isinstance(records[-1], dict)
                    or not isinstance(records[0].get("data"), dict)
                    or records[0].get("type") != "session.start"
                    or records[0].get("data", {}).get("sessionId") != session
                    or records[-1].get("type") != "session.shutdown"
                ):
                    raise ValueError("Unverified Copilot baseline")
                self.baseline = records[-1]["data"]
                _copilot_models(self.baseline)
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                self.baseline = None

    def __call__(self, usage, lines, session_id=None):
        durations = {
            key: value
            for key, value in usage.model_dump(exclude_unset=True).items()
            if key in {"duration_ms", "duration_api_ms"}
        }
        try:
            metrics = json.loads(_read(self.path))
        except (OSError, ValueError):
            if self.session_id is not None:
                return TokenUsage(
                    **durations,
                    turn_usages=[
                        {
                            "model": None,
                            "usage": {},
                            "unavailable_reason": "native_metrics_unavailable",
                        }
                    ],
                )
            return usage  # Older supported text summaries retain their counters.
        if self.session_id is not None and self.baseline is None:
            return TokenUsage(
                **durations,
                turn_usages=[
                    {
                        "model": None,
                        "usage": {},
                        "unavailable_reason": "native_baseline_unavailable",
                    }
                ],
            )
        if self.session_id is not None and session_id not in (None, self.session_id):
            raise ValueError("Copilot accounting session mismatch")
        current = _copilot_models(metrics)
        before = _copilot_models(self.baseline) if self.baseline else {}
        if set(before) - set(current):
            raise ValueError("Copilot cumulative model counters reset")
        turns, totals = [], {}
        for model, entry in current.items():
            prior = before.get(model, {"usage": {}})
            counters = {
                key: value - prior["usage"].get(key, 0) for key, value in entry["usage"].items()
            }
            if any(value < 0 for value in counters.values()):
                raise ValueError("Copilot cumulative token counters decreased")
            turn = {"model": model, "model_source": "native_metrics", "usage": counters}
            if "total_nano_aiu" in entry and (not prior["usage"] or "total_nano_aiu" in prior):
                delta = entry["total_nano_aiu"] - prior.get("total_nano_aiu", 0)
                turn["reported_nano_aiu"] = integer(delta)
            turns.append(turn)
            for key, value in counters.items():
                totals[key] = totals.get(key, 0) + value
        # Reconcile model billing against the session total. Agent-level costs
        # are deliberately not added a second time.
        if "totalNanoAiu" in metrics and (not self.baseline or "totalNanoAiu" in self.baseline):
            amount = integer(metrics["totalNanoAiu"]) - integer(
                (self.baseline or {}).get("totalNanoAiu", 0)
            )
            integer(amount)
            if all("reported_nano_aiu" in turn for turn in turns):
                if sum(turn["reported_nano_aiu"] for turn in turns) != amount:
                    raise ValueError("Copilot model and session billing disagree")
            else:
                for turn in turns:
                    turn.pop("reported_nano_aiu", None)
                # A native aggregate is still authoritative when model-level
                # amounts are unavailable; retain tokens without double charging.
                turns = [
                    {
                        "model": None,
                        "model_source": "native_metrics",
                        "usage": totals.copy(),
                        "reported_nano_aiu": amount,
                    }
                ]
        if "totalApiDurationMs" in metrics:
            duration = integer(metrics["totalApiDurationMs"]) - integer(
                (self.baseline or {}).get("totalApiDurationMs", 0)
            )
            durations["duration_api_ms"] = integer(duration)
        return TokenUsage(**durations, **totals, turn_usages=turns)

    def close(self):
        self.directory.cleanup()


def gemini_model_usages(stats):
    turns = []
    models = stats.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("Invalid Gemini model statistics")
    for model, raw in models.items():
        if not isinstance(model, str) or not isinstance(raw, dict):
            raise ValueError("Invalid Gemini model statistics")
        counters = {
            target: integer(raw[source])
            for source, target in (
                ("input_tokens", "input_tokens"),
                ("output_tokens", "output_tokens"),
                ("cached", "cache_read_input_tokens"),
            )
            if source in raw
        }
        if set(counters) == {
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
        } and not any(counters.values()):
            continue  # An unused router model is explicitly zero in stream stats.
        turns.append(
            {
                "model": model,
                "model_source": "provider_stats",
                "usage": counters,
                "unavailable_reason": "reasoning_usage_unavailable",
            }
        )
    return turns


def _gemini_journal(home, session):
    short = _session(session)[:8]
    matches, visited = [], 0
    for project in (home / "tmp").iterdir():
        visited += 1
        if visited > 4096:
            raise ValueError("Gemini journal search exceeds bound")
        if project.is_symlink() or not project.is_dir():
            continue
        chats = project / "chats"
        if chats.is_symlink() or not chats.is_dir():
            continue
        for path in chats.iterdir():
            visited += 1
            if visited > 65536:
                raise ValueError("Gemini journal search exceeds bound")
            if re.fullmatch(rf"session-.*-{short}\.jsonl?", path.name):
                matches.append(path)
    if len(matches) != 1:
        raise ValueError("Gemini journal missing or ambiguous")
    text = _read(matches[0])
    if matches[0].suffix == ".json":
        document = json.loads(text)
    else:
        rows = [json.loads(line) for line in text.splitlines()]
        if not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid Gemini journal records")
        document = dict(rows[0], messages=[])
        for row in rows[1:]:
            if "$set" in row:
                if not isinstance(row["$set"], dict):
                    raise ValueError("Invalid Gemini journal patch")
                for key, value in row["$set"].items():
                    if key == "messages":
                        # Validate before any later append/indexed patch uses
                        # the replacement. Invalid journal data stays optional.
                        if not isinstance(value, list) or any(
                            not isinstance(message, dict) for message in value
                        ):
                            raise ValueError("Invalid Gemini journal messages patch")
                        document["messages"] = value
                    elif key.startswith("messages."):
                        parts = key.split(".")
                        if len(parts) != 3 or parts[2] not in {
                            "tokens",
                            "model",
                            "thoughts",
                            "content",
                        }:
                            raise ValueError("Unknown Gemini journal patch")
                        document["messages"][int(parts[1])][parts[2]] = value
            elif "id" in row and "type" in row:
                document["messages"].append(row)
    if not isinstance(document, dict) or document.get("sessionId") != session:
        raise ValueError("Gemini accounting session mismatch")
    if not isinstance(document.get("messages"), list) or any(
        not isinstance(message, dict) for message in document["messages"]
    ):
        raise ValueError("Invalid Gemini journal messages")
    return document


class GeminiUsage:
    """Supplement candidate-only stdout with this invocation's thinking tokens."""

    def __init__(self, command, environment):
        self.session_id = _resume(command)
        self.home = (
            Path(environment.get("GEMINI_CLI_HOME", environment.get("HOME", str(Path.home()))))
            / ".gemini"
        )
        self.started_at = time.time()
        self.baseline = None
        if self.session_id:
            try:
                self.baseline = {
                    message["id"]
                    for message in _gemini_journal(self.home, self.session_id)["messages"]
                }
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                pass

    def __call__(self, usage, lines, session_id=None):
        result = usage.model_copy(deep=True)
        try:
            session = _session(session_id)
            if self.session_id is not None and (
                session != self.session_id or self.baseline is None
            ):
                return result
            document = _gemini_journal(self.home, session)
            if not isinstance(document.get("startTime"), str):
                raise ValueError("Invalid Gemini journal timestamp")
            if (
                self.session_id is None
                and datetime.fromisoformat(document["startTime"].replace("Z", "+00:00")).timestamp()
                < self.started_at
            ):
                return result
            models = {}
            for message in document["messages"]:
                if message.get("type") != "gemini" or message.get("id") in (self.baseline or set()):
                    continue
                tokens, model = message.get("tokens"), message.get("model")
                if not isinstance(tokens, dict) or not isinstance(model, str):
                    raise ValueError("Missing native Gemini token evidence")
                if integer(tokens.get("tool", 0)):
                    raise ValueError(
                        "Separate Gemini tool tokens require another billing dimension"
                    )
                counters = {
                    target: integer(tokens[source])
                    for source, target in (
                        ("input", "input_tokens"),
                        ("output", "output_tokens"),
                        ("cached", "cache_read_input_tokens"),
                        ("thoughts", "reasoning_output_tokens"),
                    )
                }
                combined = models.setdefault(model, dict.fromkeys(counters, 0))
                for key, value in counters.items():
                    combined[key] += value
            known = result.model_dump(exclude_unset=True)
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
                if key not in known or sum(c[key] for c in models.values()) != known[key]:
                    return result
            if not models:
                return result
            if result.turn_usages:
                supplied = {turn["model"]: turn["usage"] for turn in result.turn_usages}
                if set(supplied) != set(models) or any(
                    any(
                        counters.get(key) != models[model][key]
                        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens")
                    )
                    for model, counters in supplied.items()
                ):
                    return result
            turns = []
            for model, counters in models.items():
                candidate_tokens = counters["output_tokens"]
                counters["output_tokens"] += counters["reasoning_output_tokens"]
                turns.append(
                    {
                        "model": model,
                        "model_source": "native_journal",
                        "usage": counters,
                        "candidate_output_tokens": candidate_tokens,
                    }
                )
            result.turn_usages = turns
            result.output_tokens = sum(turn["usage"]["output_tokens"] for turn in turns)
            result.reasoning_output_tokens = sum(
                turn["usage"]["reasoning_output_tokens"] for turn in turns
            )
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            pass  # Keep known stdout counters, with incomplete billing evidence.
        return result

    def close(self):
        pass


def prepare_provider_usage(cli, command, environment):
    if cli == "copilot":
        return CopilotUsage(command, environment)
    if cli == "gemini":
        return GeminiUsage(command, environment)
    return None

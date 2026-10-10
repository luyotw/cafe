"""Official rate parsing, conditional refresh and immutable historical cards."""

import io
import json
import os
from http.client import BadStatusLine, HTTPResponse, IncompleteRead
from threading import Event, Thread
from urllib.error import HTTPError, URLError

import pytest
from typer.testing import CliRunner

from cafe.core.pricing import (
    SOURCE_URL,
    TTL_SECONDS,
    OpenAIPricingStore,
    PricingError,
    make_snapshot,
    parse_openai_rates,
)
from cafe.ui.commands.pricing import pricing_app
from cafe.core.types import TokenUsage


DOCUMENT = """# Pricing

Flagship models
Prices per 1M tokens.
Standard
### Standard pricing data
| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-test | $2.00 | $0.20 | $2.50 | $10.00 | $4.00 | $0.40 | $5.00 | $15.00 |

Batch
### Batch pricing data
| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-test | $1.00 | $0.10 | $1.25 | $5.00 | $2.00 | $0.20 | $2.50 | $7.50 |

Short context: ≤272K input tokens. Long context: >272K input tokens.
Specialized models
Prices per 1M tokens.
Standard
### Grouped Pricing Table data
| Category | Model | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
| Codex | gpt-test-codex | $2.00 | $0.20 | $10.00 |
| Embedding | not-a-text-model | $0.02 | - | - |
Finetuning
Standard
### Pricing Table data
| Model | Training | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
| gpt-test | $100.00 | $20.00 | $2.00 | $100.00 |
"""


class Response(io.BytesIO):
    def __init__(self, document=DOCUMENT, headers=None):
        super().__init__(document.encode())
        self.headers = headers or {"ETag": '"v1"', "Last-Modified": "Thu, 08 Oct 2026 00:00:00 GMT"}

    def geturl(self):
        return SOURCE_URL


def test_parser_separates_tiers_and_avoids_finetuning_and_non_text_prices():
    data = parse_openai_rates(DOCUMENT)
    assert data["rates"]["gpt-test"]["standard"]["usd_per_million_tokens"]["short"]["input"] == "2"
    assert data["rates"]["gpt-test"]["batch"]["usd_per_million_tokens"]["short"]["input"] == "1"
    assert data["rates"]["gpt-test"]["standard"]["short_context_limit"] == 272000
    assert data["rates"]["gpt-test-codex"]["standard"]["short_context_limit"] is None
    assert "not-a-text-model" not in data["rates"]


@pytest.mark.parametrize(
    "document",
    [
        "<html>Sign in</html>",
        DOCUMENT.replace("$2.00", "$-2.00"),
        DOCUMENT.replace("$2.00", "$NaN"),
        DOCUMENT.replace("Short context input", "Different input"),
        DOCUMENT.replace("Short context: ≤272K input tokens.", ""),
        DOCUMENT.replace("| gpt-test | $2.00", "| gpt-test | unexpected | $2.00"),
    ],
)
def test_invalid_documents_are_rejected(document):
    with pytest.raises(PricingError):
        parse_openai_rates(document)


def test_conditional_get_and_ttl_preserve_snapshot_version_and_fetched_at(tmp_path):
    now = [100000.0]
    requests = []

    def fetch(request, timeout):
        requests.append(request)
        assert timeout == 3
        if len(requests) > 1:
            assert request.get_header("If-none-match") == '"v1"'
            raise HTTPError(SOURCE_URL, 304, "Not Modified", {"ETag": '"v1"'}, None)
        return Response()

    store = OpenAIPricingStore(tmp_path, opener=fetch, clock=lambda: now[0])
    first = store.get()
    assert first["error"] is None and not first["stale"]
    now[0] += 10
    assert store.get()["snapshot"] == first["snapshot"]
    assert len(requests) == 1
    now[0] += TTL_SECONDS
    second = store.get()
    assert len(requests) == 2
    assert second["snapshot"] == first["snapshot"]
    assert second["checked_at"] == now[0]
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_modified_since_fallback_and_same_rates_do_not_rewrite_archive(tmp_path):
    store = OpenAIPricingStore(
        tmp_path,
        opener=lambda *a, **k: Response(headers={"Last-Modified": "old"}),
        clock=lambda: 100000,
    )
    first = store.get()
    archive = tmp_path / (first["snapshot"]["version"] + ".json")
    original = archive.read_bytes()

    def fetch(request, **kwargs):
        assert request.get_header("If-modified-since") == "old"
        return Response(DOCUMENT.replace("\n\n", "\n \n"), headers={"Last-Modified": "new"})

    store.opener = fetch
    second = store.get(force=True)
    assert second["snapshot"]["version"] == first["snapshot"]["version"]
    assert archive.read_bytes() == original
    assert second["last_modified"] == "new"


@pytest.mark.parametrize("failure", [URLError("offline"), PricingError("changed layout")])
def test_refresh_failure_retains_last_good_and_backs_off(tmp_path, failure):
    now = [100000]
    store = OpenAIPricingStore(tmp_path, opener=lambda *a, **k: Response(), clock=lambda: now[0])
    first = store.get()
    calls = []

    def fetch(*args, **kwargs):
        calls.append(1)
        raise failure

    store.opener = fetch
    now[0] += TTL_SECONDS
    failed = store.get()
    assert failed["stale"] and failed["error"]
    assert failed["snapshot"] == first["snapshot"]
    store.get()
    assert len(calls) == 1


def test_changed_rates_archive_old_snapshot_and_validate_before_publish(tmp_path):
    store = OpenAIPricingStore(tmp_path, opener=lambda *a, **k: Response(), clock=lambda: 100000)
    first = store.get()
    store.opener = lambda *a, **k: Response(DOCUMENT.replace("$2.00", "$3.00"))
    second = store.get(force=True)
    assert second["snapshot"]["version"] != first["snapshot"]["version"]
    assert (
        json.loads((tmp_path / (first["snapshot"]["version"] + ".json")).read_text())
        == first["snapshot"]
    )
    store.opener = lambda *a, **k: Response("bad HTML")
    assert store.get(force=True)["snapshot"] == second["snapshot"]


def test_packaged_snapshot_works_offline_and_status_is_read_only(tmp_path):
    def fail(*a, **k):
        raise AssertionError("No HTTP expected")

    store = OpenAIPricingStore(tmp_path / "absent", opener=fail)
    assert store.status()["stale"]
    state = store.get(auto_update=False)
    assert "gpt-5.3-codex" in state["snapshot"]["data"]["rates"]
    assert not store.cache_dir.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX refresh lock")
def test_failed_publisher_holds_lock_until_error_state_is_written(tmp_path, monkeypatch):
    import cafe.core.pricing as pricing

    store = OpenAIPricingStore(tmp_path, opener=lambda *a, **k: Response())
    original = store.get()
    publishing, release = Event(), Event()
    atomic_json = pricing._atomic_json

    def pause_failure(path, state):
        if state.get("error"):
            publishing.set()
            assert release.wait(3)
        atomic_json(path, state)

    def broken(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr(pricing, "_atomic_json", pause_failure)
    failed = OpenAIPricingStore(tmp_path, opener=broken)
    worker = Thread(target=lambda: failed.get(force=True))
    fetched = []

    def updated(*args, **kwargs):
        fetched.append(True)
        return Response(DOCUMENT.replace("$2.00", "$3.00"))

    successful = OpenAIPricingStore(tmp_path, opener=updated)
    worker.start()
    try:
        assert publishing.wait(3)
        contended = successful.get(force=True)
        assert not fetched
        assert contended["snapshot"] == original["snapshot"]
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    published = successful.get(force=True)
    assert fetched == [True]
    assert published["snapshot"]["version"] != original["snapshot"]["version"]
    assert successful.status()["snapshot"] == published["snapshot"]
    assert successful.status()["error"] is None


def test_background_refresh_does_not_wait_and_pins_old_card(tmp_path, monkeypatch):
    from cafe.core.cost import prepare_cost_accounting

    now = [100000]
    store = OpenAIPricingStore(tmp_path, opener=lambda *a, **k: Response(), clock=lambda: now[0])
    original = store.get()
    now[0] += TTL_SECONDS
    entered, release = Event(), Event()

    def stalled(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return Response(DOCUMENT.replace("$2.00", "$3.00"))

    store.opener = stalled
    workers = []
    schedule = store.refresh_in_background

    def track():
        worker = schedule()
        if worker is not None:
            workers.append(worker)

    monkeypatch.setattr("cafe.core.cost.pricing_store", lambda *args: store)
    monkeypatch.setattr(store, "refresh_in_background", track)
    try:
        recorder = prepare_cost_accounting("codex", {})
        assert entered.wait(3)
        assert workers[0].is_alive() and workers[0].daemon
        prepare_cost_accounting("codex", {})
        assert len(workers) == 1  # One worker per cache even during a stalled read.
    finally:
        release.set()
        for worker in workers:
            worker.join(3)
    assert store.status()["snapshot"]["version"] != original["snapshot"]["version"]
    usage = recorder(TokenUsage(input_tokens=100, output_tokens=10,
                                cache_read_input_tokens=0, cache_write_input_tokens=0), "gpt-test")
    assert usage.cost_records[0]["pricing"]["version"] == original["snapshot"]["version"]


def test_background_refresh_respects_ttl_and_failure_backoff(tmp_path):
    now = [100000]
    store = OpenAIPricingStore(tmp_path, opener=lambda *a, **k: Response(), clock=lambda: now[0])
    store.get()
    assert store.refresh_in_background() is None
    now[0] += TTL_SECONDS

    def broken(*args, **kwargs):
        raise IncompleteRead(b"partial")

    store.opener = broken
    worker = store.refresh_in_background()
    worker.join(3)
    assert not worker.is_alive()
    assert store.status()["error"] == "IncompleteRead"
    assert store.refresh_in_background() is None


@pytest.mark.parametrize("failure", ["construction", "start"])
def test_background_worker_start_failure_does_not_stop_accounting(tmp_path, monkeypatch, failure):
    from cafe.core.cost import prepare_cost_accounting

    class UnavailableThread:
        def __init__(self, **kwargs):
            if failure == "construction":
                raise RuntimeError("threads unavailable")

        def start(self):
            raise RuntimeError("threads unavailable")

    store = OpenAIPricingStore(tmp_path, opener=lambda *args, **kwargs: Response())
    monkeypatch.setattr("cafe.core.cost.pricing_store", lambda *args: store)
    monkeypatch.setattr("cafe.core.pricing.Thread", UnavailableThread)
    recorder = prepare_cost_accounting("codex", {})
    usage = recorder(TokenUsage(input_tokens=100, output_tokens=20,
                                cache_read_input_tokens=0), "gpt-5.3-codex")
    assert usage.cost_records[0]["provenance"] == "estimated"
    assert usage.cost_records[0]["pricing_stale"]
    # A transient inability to create/start a thread must not reserve the
    # cache indefinitely; the next invocation can still schedule an update.
    monkeypatch.setattr("cafe.core.pricing.Thread", Thread)
    worker = store.refresh_in_background()
    assert worker is not None
    worker.join(3)
    assert not worker.is_alive() and store.status()["error"] is None


def test_rate_terms_are_versioned_and_dates_are_metadata():
    first = make_snapshot(DOCUMENT, fetched_at=1, last_modified="one")
    second = make_snapshot(DOCUMENT, fetched_at=2, last_modified="two")
    assert first["version"] == second["version"]
    third = make_snapshot(DOCUMENT + "\nRegional processing incurs a surcharge.\n", fetched_at=3)
    assert third["version"] != first["version"]


def test_pricing_status_command_does_not_fetch(monkeypatch, tmp_path):
    store = OpenAIPricingStore(tmp_path)
    monkeypatch.setattr("cafe.ui.commands.pricing._store", lambda: store)
    result = CliRunner().invoke(pricing_app, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["provider"] == "openai"
    assert not tmp_path.joinpath("current.json").exists()


@pytest.mark.parametrize("failure", ["truncated", "status", "parser"])
@pytest.mark.parametrize("cached", [False, True])
def test_http_protocol_and_unexpected_errors_keep_rates_and_back_off(tmp_path, failure, cached):
    now = [100000]
    store = OpenAIPricingStore(tmp_path, opener=lambda *a, **k: Response(), clock=lambda: now[0])
    prior = store.get()["snapshot"] if cached else store.status()["snapshot"]
    now[0] += TTL_SECONDS
    calls = []

    class Socket:
        def makefile(self, *args):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nabc")

    def broken_response(*args, **kwargs):
        calls.append(1)
        if failure == "status":
            raise BadStatusLine("invalid status")
        if failure == "parser":
            raise RuntimeError("unexpected refresh failure")
        response = HTTPResponse(Socket())
        response.begin()
        response.geturl = lambda: SOURCE_URL
        return response

    store.opener = broken_response
    failed = store.get()
    assert failed["snapshot"] == prior
    assert failed["stale"] and failed["error"]
    if failure == "truncated":
        assert failed["error"] == IncompleteRead.__name__
    store.get()
    assert len(calls) == 1


def test_unexpected_cache_write_error_does_not_escape_refresh(monkeypatch, tmp_path):
    store = OpenAIPricingStore(tmp_path, opener=lambda *a, **k: Response())
    prior = store.get()["snapshot"]

    def broken_write(*args):
        raise RuntimeError("cache writer failed")

    monkeypatch.setattr("cafe.core.pricing._atomic_json", broken_write)
    failed = store.get(force=True)
    assert failed["snapshot"] == prior
    assert failed["stale"] and failed["error"] == "RuntimeError"

"""Official table schemas, independent refreshes and effective price dates."""

import io
import json
from decimal import Decimal
from urllib.error import HTTPError

import pytest
from typer.testing import CliRunner

from cafe.core.cost import account_cost
from cafe.core.pricing import PricingError, make_snapshot, validate_snapshot
from cafe.core.pricing_sources import (
    SOURCES,
    ProviderPricingStore,
    model_id,
    parse_copilot_rates,
    parse_cursor_rates,
    parse_gemini_rates,
)
from cafe.core.types import TokenUsage
from cafe.ui.commands.pricing import pricing_app

COPILOT = """- model: Claude Sonnet 5
  input: $2.00
  cached_input: $0.20
  cache_write: $2.50
  output: $10.00
"""
CURSOR = """# Models & Pricing
All prices per 1M tokens in USD.
| Model | Provider | Input | Cache write | Cache read | Output | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| [Claude 4.6 Sonnet](https://example.test/model) | Anthropic | $3 | $3.75 | $0.3 | $15 | No long-context surcharge |
"""


def gemini_document(
    input_price="$0.50 (text / image / video)",
    output_price="$3.00 (including thinking)",
    cached_price="$0.05 (text / image / video)<br>$1.00 / 1M tokens per hour (storage price)",
):
    return f"""<html><div class="models-section">
<h2>Gemini 3 Flash Preview</h2><code>gemini-3-flash-preview</code>
<h3>Standard</h3><table class="pricing-table">
<tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>
<tr><td>Input price</td><td>Free</td><td>{input_price}</td></tr>
<tr><td>Output price</td><td>Free</td><td>{output_price}</td></tr>
<tr><td>Context caching price</td><td>Free</td><td>{cached_price}</td></tr>
<tr><td>Grounding</td><td>Free</td><td>$99.00 per request</td></tr>
</table></div><footer>Last updated 2026-10-07 UTC</footer></html>"""


DOCUMENTS = {"copilot": COPILOT, "cursor": CURSOR, "gemini": gemini_document()}
PARSERS = {
    "copilot": parse_copilot_rates,
    "cursor": parse_cursor_rates,
    "gemini": parse_gemini_rates,
}


def state(provider, document=None):
    return {
        "snapshot": make_snapshot(
            document or DOCUMENTS[provider],
            fetched_at=100000,
            etag=None,
            last_modified=None,
            source_url=SOURCES[provider],
            parser=PARSERS[provider],
        ),
        "stale": False,
    }


@pytest.mark.parametrize("provider", DOCUMENTS)
def test_snapshot_is_valid_for_its_source_only(provider):
    snapshot = state(provider)["snapshot"]
    assert validate_snapshot(snapshot, source_url=SOURCES[provider]) is snapshot
    with pytest.raises(PricingError):
        validate_snapshot(snapshot)


def test_copilot_long_context_and_promotional_footnotes_are_retained():
    rows = COPILOT.replace("  input:", "  tier: Default\n  threshold: ≤ 272K\n  input:")
    rows += COPILOT.replace(
        "  input:", "  tier: Long context\n  threshold: '> 272K'\n  input:"
    ).replace("$2.00", "$4.00")
    rate = parse_copilot_rates(rows)["rates"]["claude-sonnet-5"]["standard"]
    assert rate["short_context_limit"] == 272000
    assert rate["usd_per_million_tokens"]["long"]["input"] == "4"
    promo = parse_copilot_rates(COPILOT.replace("Claude Sonnet 5", "Claude Sonnet 5[^promo]"))
    assert promo["rates"]["claude-sonnet-5"]["standard"]["terms_unverified"]


@pytest.mark.parametrize(
    "document", ["not yaml", "- model: {bad", COPILOT.replace("$2.00", "$NaN"), COPILOT + COPILOT]
)
def test_invalid_copilot_document_is_rejected(document):
    with pytest.raises(PricingError):
        parse_copilot_rates(document)


def test_cursor_normalizes_published_spelling_without_resolving_auto():
    assert model_id("Claude 4.6 Sonnet") == "claude-sonnet-4.6"
    assert model_id("Claude Opus 4.7 (fast mode)") == "claude-opus-4.7-fast"
    assert model_id("Auto") == "auto"
    rate = parse_cursor_rates(CURSOR)["rates"]["claude-sonnet-4.6"]["standard"]
    assert rate["usd_per_million_tokens"]["short"]["cache_write"] == "3.75"
    assert not rate["context_unverified"]


def test_cursor_fast_context_note_cannot_certify_the_base_context_boundary():
    document = CURSOR.replace(
        "No long-context surcharge",
        "Long context supports up to 1M with 2x input pricing; Fast mode for long context (>272k)",
    )
    rate = parse_cursor_rates(document)["rates"]["claude-sonnet-4.6"]["standard"]
    assert rate["context_unverified"]
    assert rate["short_context_limit"] is None


def test_cursor_estimate_charges_uncached_input_without_subtracting_cache_again():
    result = account_cost(
        TokenUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=30,
            cache_write_input_tokens=10,
        ),
        cli="cursor",
        model="Claude 4.6 Sonnet",
        state=state("cursor"),
    )
    record = result.cost_records[0]
    assert Decimal(record["amount_usd"]) == Decimal("0.0006465")
    assert record["billed_tokens"]["input"] == 100
    assert record["rate_model"] == "claude-sonnet-4.6"


def test_cursor_context_upper_bound_includes_both_cache_categories():
    document = CURSOR.replace(
        "No long-context surcharge", "Long context (>256k input tokens) is billed at 2x"
    )
    result = account_cost(
        TokenUsage(
            input_tokens=250000,
            output_tokens=1,
            cache_read_input_tokens=10000,
            cache_write_input_tokens=0,
        ),
        cli="cursor",
        model="Claude 4.6 Sonnet",
        state=state("cursor", document),
    )
    assert result.cost_records[0]["reason"] == "context_tier_unavailable"


def test_cursor_expired_promotional_price_is_unavailable():
    document = CURSOR.replace(
        "No long-context surcharge", "Promotional pricing through November 21, 2026"
    )
    usage = TokenUsage(
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_write_input_tokens=0
    )
    before = account_cost(
        usage,
        cli="cursor",
        model="Claude 4.6 Sonnet",
        state=state("cursor", document),
        valuation_date="2026-11-21",
    )
    after = account_cost(
        usage,
        cli="cursor",
        model="Claude 4.6 Sonnet",
        state=state("cursor", document),
        valuation_date="2026-11-22",
    )
    assert before.cost_records[0]["provenance"] == "estimated"
    assert after.cost_records[0]["reason"] == "expired_rate_unavailable"


def test_gemini_extracts_text_rates_and_ignores_audio_storage_and_grounding_dimensions():
    document = gemini_document(input_price="$0.50 (text / image / video)<br>$1.00 (audio)")
    rate = parse_gemini_rates(document)["rates"]["gemini-3-flash-preview"]["standard"]
    assert rate["usd_per_million_tokens"]["short"] == {
        "input": "0.5",
        "output": "3",
        "cached_input": "0.05",
    }


def test_gemini_footer_timestamp_and_whitespace_do_not_version_the_card():
    first = state("gemini")["snapshot"]
    changed = state(
        "gemini", DOCUMENTS["gemini"].replace("2026-10-07", "2026-10-09").replace("</td>", " </td>")
    )["snapshot"]
    assert first["version"] == changed["version"]


@pytest.mark.parametrize("date,price", [("2026-12-31", "0.75"), ("2027-01-01", "1.5")])
def test_gemini_dated_promotions_select_invocation_date_not_fetch_date(date, price):
    document = gemini_document(
        input_price="$0.75 through December 31, 2026<br>$1.50 starting January 1, 2027"
    )
    result = account_cost(
        TokenUsage(
            input_tokens=100, output_tokens=1, cache_read_input_tokens=0, reasoning_output_tokens=0
        ),
        cli="gemini",
        model="gemini-3-flash-preview",
        state=state("gemini", document),
        valuation_date=date,
    )
    assert result.cost_records[0]["rates_usd_per_million_tokens"]["input"] == price
    assert result.cost_records[0]["valuation_date"] == date


def test_gemini_long_context_requires_all_categories_with_matching_thresholds():
    document = gemini_document(
        input_price="$2.00, prompts <= 200k tokens<br>$4.00, prompts > 200k tokens",
        output_price="$12.00, prompts <= 200k tokens<br>$18.00, prompts > 200k tokens",
        cached_price="$0.20, prompts <= 200k tokens<br>$0.40, prompts > 200k tokens",
    )
    rate = parse_gemini_rates(document)["rates"]["gemini-3-flash-preview"]["standard"]
    assert rate["short_context_limit"] == 200000
    assert rate["usd_per_million_tokens"]["long"]["output"] == "18"
    with pytest.raises(PricingError):
        parse_gemini_rates(document.replace("$0.40, prompts > 200k", "$0.40, prompts > 300k"))


@pytest.mark.parametrize("provider", DOCUMENTS)
def test_provider_refresh_uses_own_validator_and_retains_offline_snapshot(provider, tmp_path):
    seen = []

    class Response(io.BytesIO):
        headers = {"ETag": '"provider-v1"'}

        def geturl(self):
            return SOURCES[provider]

    def fetch(request, timeout):
        seen.append(request)
        if provider == "cursor":
            assert request.get_header("Accept") == "*/*"
        if len(seen) > 1:
            assert request.get_header("If-none-match") == '"provider-v1"'
            raise HTTPError(SOURCES[provider], 304, "Not modified", {}, None)
        return Response(DOCUMENTS[provider].encode())

    store = ProviderPricingStore(provider, tmp_path, opener=fetch, clock=lambda: 100000)
    first = store.get()
    assert not first["error"] and not first["stale"]
    second = store.get(force=True)
    assert first["snapshot"] == second["snapshot"]
    assert seen[0].full_url == SOURCES[provider]
    store.opener = lambda *args, **kwargs: Response(b"unrecognized layout")
    failed = store.get(force=True)
    assert failed["error"] and failed["stale"]
    assert failed["snapshot"] == first["snapshot"]


def test_all_provider_status_is_read_only_and_machine_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("CAFE_PRICING_CACHE_DIR", str(tmp_path))
    result = CliRunner().invoke(pricing_app, ["status", "--provider", "all", "--json"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines()]
    assert {row["provider"] for row in rows} == {"openai", "copilot", "cursor", "gemini"}
    assert all(row["stale"] for row in rows)
    assert not list(tmp_path.iterdir())


def test_unknown_cli_provider_is_rejected():
    result = CliRunner().invoke(pricing_app, ["refresh", "--provider", "invented"])
    assert result.exit_code == 2

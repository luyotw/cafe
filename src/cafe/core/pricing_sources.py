"""Official provider rate cards; prices are factual data, prose is hashed."""

import re
from datetime import datetime
from html.parser import HTMLParser

import yaml

from cafe.core.pricing import OpenAIPricingStore, PricingError, _hash, _price

SOURCES = {
    "copilot": "https://raw.githubusercontent.com/github/docs/main/data/tables/copilot/models-and-pricing.yml",
    "cursor": "https://cursor.com/docs/models-and-pricing.md",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
}


def model_id(name):
    """Normalize published spelling, never resolve rolling aliases or Auto."""
    name = re.sub(r"\[\^.*?\]", "", name).strip().lower()
    name = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", name)
    name = re.sub(r"^claude (\d[\d.]*) (sonnet|opus|haiku)\b", r"claude \2 \1", name)
    name = (
        name.replace("(preview)", "preview")
        .replace("(fast mode)", "fast")
        .replace("(fast)", "fast")
    )
    return re.sub(r"[\s_]+", "-", name)


def _data(rates, terms):
    if not rates or not any("standard" in tiers for tiers in rates.values()):
        raise PricingError("No standard text model rates")
    return dict(
        rates=rates,
        terms_sha256=_hash(" ".join(terms.split())),
        currency="USD",
        unit="million_tokens",
        parser_version=1,
    )


def _entry(prices, limit=None, **metadata):
    return dict(usd_per_million_tokens={"short": prices}, short_context_limit=limit, **metadata)


def parse_copilot_rates(document):
    try:
        rows = yaml.safe_load(document)
    except yaml.YAMLError as error:
        raise PricingError("Invalid Copilot YAML") from error
    if not isinstance(rows, list):
        raise PricingError("Expected official Copilot YAML rows")
    rates = {}
    for row in rows:
        if not isinstance(row, dict) or not {"model", "input", "cached_input", "output"}.issubset(
            row
        ):
            raise PricingError("Copilot pricing schema changed")
        if not all(
            isinstance(row[key], str) for key in ("model", "input", "cached_input", "output")
        ):
            raise PricingError("Copilot pricing types changed")
        model = model_id(row["model"])
        prices = {
            k: _price(row[v]) if row[v] != "Not applicable" else None
            for k, v in (("input", "input"), ("cached_input", "cached_input"), ("output", "output"))
        }
        if "cache_write" in row:
            prices["cache_write"] = (
                _price(row["cache_write"]) if row["cache_write"] != "Not applicable" else None
            )
        tier = row.get("tier", "Default")
        if tier not in {"Default", "Long context"}:
            raise PricingError("Unknown Copilot context tier")
        entry = rates.setdefault(model, {}).setdefault("standard", _entry({}))
        if "[^" in row["model"]:
            entry["terms_unverified"] = True
        band = "long" if tier == "Long context" else "short"
        if band in entry["usd_per_million_tokens"] and entry["usd_per_million_tokens"][band]:
            raise PricingError("Duplicate Copilot rate")
        entry["usd_per_million_tokens"][band] = prices
        threshold = row.get("threshold", "Not applicable")
        if threshold != "Not applicable":
            match = re.fullmatch(r"[≤>]\s*(\d+)K", threshold)
            if not match:
                raise PricingError("Unknown Copilot threshold")
            entry["short_context_limit"] = int(match[1]) * 1000
    # YAML comments are not pricing terms. Footnotes/promotion markers remain
    # in the semantic source and therefore invalidate the snapshot.
    return _data(rates, str(rows))


def parse_cursor_rates(document):
    if not document.lstrip().startswith("# Models & Pricing"):
        raise PricingError("Expected official Cursor Markdown")
    rates, headers = {}, None
    for line in document.splitlines():
        if not line.startswith("|"):
            headers = None
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] == "Model":
            headers = cells
            continue
        if headers is None or all(re.fullmatch(r"-+", c) for c in cells):
            continue
        if len(cells) != len(headers):
            raise PricingError("Cursor table columns changed")
        row = dict(zip(headers, cells))
        if not {"Model", "Input", "Cache write", "Cache read", "Output", "Notes"}.issubset(row):
            raise PricingError("Cursor token table changed")
        model = model_id(row["Model"])
        prices = {
            k: _price(row[v])
            for k, v in (
                ("input", "Input"),
                ("cached_input", "Cache read"),
                ("cache_write", "Cache write"),
                ("output", "Output"),
            )
        }
        notes = row["Notes"].lower()
        context_clauses = " ".join(clause for clause in notes.split(";") if "fast" not in clause)
        boundary = re.search(
            r"(?:long context \(>|input exceeds |requests above )(\d+)k", context_clauses
        )
        limit = int(boundary[1]) * 1000 if boundary else None
        # Some rows describe a surcharge without defining its boundary. Retain
        # the rate but do not silently assume that this invocation qualifies.
        uncertain = (
            "long context" in notes and "no long-context surcharge" not in notes and limit is None
        )
        entry = _entry(prices, limit, context_unverified=uncertain)
        promotion = re.search(r"promotional pricing through ([a-z]+ \d+, \d{4})", notes)
        if promotion:
            entry["valid_through"] = datetime.strptime(promotion[1], "%B %d, %Y").date().isoformat()
        elif "promotional" in notes:
            entry["terms_unverified"] = True
        prior = rates.setdefault(model, {}).get("standard")
        if prior and prior != entry:
            raise PricingError("Conflicting Cursor rate")
        rates[model]["standard"] = entry
    return _data(rates, document)


class _PricingHTML(HTMLParser):
    """Collect model IDs, tier headings and pricing cells from official HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.models, self.tier, self.tables = [], None, []
        self.capture, self.buffer = None, []
        self.rows = self.row = None
        self.terms, self.div_depth, self.section_depth = [], 0, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            self.div_depth += 1
            if "models-section" in attrs.get("class", "").split():
                self.section_depth = self.div_depth
        if tag == "h2":
            self.models, self.tier = [], None
        if tag == "code" and self.tier is None:
            self.capture, self.buffer = "model", []
        elif tag == "h3":
            self.capture, self.buffer = "tier", []
        elif tag == "table":
            self.rows = []
        elif tag == "tr" and self.rows is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.capture, self.buffer = "cell", []
        elif tag == "br" and self.capture:
            self.buffer.append("\n")

    def handle_data(self, data):
        if self.section_depth is not None:
            self.terms.append(data)
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "div":
            if self.div_depth == self.section_depth:
                self.section_depth = None
            self.div_depth -= 1
        if (
            (tag == "code" and self.capture == "model")
            or (tag == "h3" and self.capture == "tier")
            or (tag in {"td", "th"} and self.capture == "cell")
        ):
            value = "".join(self.buffer).strip()
            if self.capture == "model" and re.fullmatch(r"gemini-[a-z0-9.-]+", value):
                self.models.append(value)
            elif self.capture == "tier":
                self.tier = value.lower()
            elif self.capture == "cell":
                self.row.append(value)
            self.capture = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append((list(self.models), self.tier, self.rows))
            self.rows = None


def _gemini_price(cell):
    """Text token price, optional short/long contexts and dated promotions."""
    lines = [" ".join(line.split()) for line in cell.splitlines() if line.strip()]
    # Storage is a different dimension (tokens/hour), never a token read price.
    lines = [line for line in lines if "storage price" not in line and line != "Same as Standard"]
    if not lines or not lines[0].startswith("$"):
        raise PricingError("Unknown Gemini text price")
    first = re.match(r"\$([\d.]+)", lines[0])
    if not first:
        raise PricingError("Unknown Gemini decimal rate")
    price = _price("$" + first[1])
    limit, long = None, None
    if "prompts <=" in " ".join(lines):
        short_match = re.search(r"\$([\d.]+), prompts <= (\d+)k", " ".join(lines))
        long_match = re.search(r"\$([\d.]+), prompts > (\d+)k", " ".join(lines))
        if not short_match or not long_match or short_match[2] != long_match[2]:
            raise PricingError("Unknown Gemini context price")
        price, limit, long = (
            _price("$" + short_match[1]),
            int(short_match[2]) * 1000,
            _price("$" + long_match[1]),
        )
    scheduled = []
    for amount, word, date in re.findall(
        r"\$([\d.]+) (through|starting) ([A-Za-z]+ \d+, \d{4})", " ".join(lines)
    ):
        scheduled.append(
            {
                "price": _price("$" + amount),
                word: datetime.strptime(date, "%B %d, %Y").date().isoformat(),
            }
        )
    if ("through " in " ".join(lines) or "starting " in " ".join(lines)) and not scheduled:
        raise PricingError("Unknown Gemini effective date")
    return price, limit, long, scheduled


def parse_gemini_rates(document):
    html = _PricingHTML()
    html.feed(document)
    rates = {}
    for models, tier, rows in html.tables:
        models = [
            m
            for m in models
            if re.match(r"gemini-[\d.]+-(?:pro|flash)", m)
            and not any(part in m for part in ("live", "audio", "image", "tts", "transcribe"))
        ]
        if not models or tier not in {"standard", "batch", "flex", "priority"}:
            continue
        if not rows or len(rows[0]) != 3 or "per 1M tokens in USD" not in rows[0][2]:
            raise PricingError("Gemini pricing units changed")
        prices, longs, schedules, limits = {}, {}, {}, set()
        for row in rows[1:]:
            if len(row) != 3:
                raise PricingError("Gemini pricing columns changed")
            label = row[0]
            key = (
                "input"
                if label.startswith("Input price")
                else (
                    "output"
                    if label.startswith("Output price")
                    else "cached_input" if label == "Context caching price" else None
                )
            )
            if key is None:
                continue
            price, limit, long, scheduled = _gemini_price(row[2])
            prices[key] = price
            if limit:
                limits.add(limit)
            if long:
                longs[key] = long
            if scheduled:
                schedules[key] = scheduled
        if not {"input", "output", "cached_input"}.issubset(prices) or len(limits) > 1:
            raise PricingError("Incomplete Gemini token rate")
        entry = _entry(prices, next(iter(limits)) if limits else None, scheduled_prices=schedules)
        if longs:
            if set(longs) != set(prices):
                raise PricingError("Incomplete Gemini long context rate")
            entry["usd_per_million_tokens"]["long"] = longs
        for model in models:
            if tier in rates.setdefault(model, {}):
                raise PricingError("Duplicate Gemini rate")
            rates[model][tier] = entry
    # Only hash text: template/navigation timestamps do not version the card.
    terms = " ".join(html.terms) or str(html.tables)
    return _data(rates, terms)


class ProviderPricingStore(OpenAIPricingStore):
    def __init__(self, provider, cache_dir=None, **kwargs):
        if provider not in SOURCES:
            raise ValueError("Unknown rate provider")
        self.provider, self.source_url = provider, SOURCES[provider]
        # Cursor's .md route returns a spurious 404 for Accept: text/markdown.
        # The explicit .md resource returns Markdown with normal negotiation.
        if provider == "cursor":
            self.accept_header = "*/*"
        self.parser = {
            "copilot": parse_copilot_rates,
            "cursor": parse_cursor_rates,
            "gemini": parse_gemini_rates,
        }[provider]
        super().__init__(cache_dir, **kwargs)


def pricing_store(provider, cache_dir=None, **kwargs):
    return (
        OpenAIPricingStore(cache_dir, **kwargs)
        if provider == "openai"
        else ProviderPricingStore(provider, cache_dir, **kwargs)
    )

"""Versioned official rate cards, conditional refresh and offline fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from threading import Lock, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SOURCE_URL = "https://developers.openai.com/api/docs/pricing.md"
TTL_SECONDS = 24 * 60 * 60
RETRY_SECONDS = 15 * 60
MAX_BYTES = 2 * 1024 * 1024
_BACKGROUND_LOCK = Lock()
_BACKGROUND_REFRESHES = set()


class PricingError(ValueError):
    """The official rate card could not be interpreted safely."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _price(cell):
    if cell == "-":
        return None
    if not re.fullmatch(r"\$\d+(?:\.\d+)?", cell):
        raise PricingError("Unrecognized OpenAI token price")
    return str(Decimal(cell[1:]).normalize())


def parse_openai_rates(document: str) -> dict:
    """Read text-model tables, retaining tiers, context bands and pricing terms.

    Do not infer aliases or apply an unrecognized table layout. Multimodal,
    fine-tuned, tool and other non-token prices are deliberately out of scope.
    """
    if not document.lstrip().startswith("# Pricing"):
        raise PricingError("Expected the official pricing Markdown")
    threshold = re.search(r"Short context:\s*≤([\d,]+)K input tokens", document)
    context_limit = int(threshold.group(1).replace(",", "")) * 1000 if threshold else None
    rates = {}
    section = tier = None
    headers = None
    for raw in document.splitlines():
        line = raw.strip()
        if line in {
            "Flagship models",
            "Specialized models",
            "Cyber models",
            "Multimodal models",
            "Tools",
            "Finetuning",
            "Cloud platforms",
        }:
            section, tier, headers = line, None, None
        if line.lower() in {"standard", "batch", "flex", "fast", "ultrafast"}:
            tier, headers = line.lower(), None
        match = re.fullmatch(r"### (Standard|Batch|Flex|Fast|Ultrafast) pricing data", line)
        if match:
            tier, headers = match.group(1).lower(), None
        if section not in {"Flagship models", "Specialized models"} or tier is None:
            continue
        if not line.startswith("|"):
            headers = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if "Model" in cells:
            headers = cells
            continue
        if headers is None or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        if len(cells) != len(headers):
            raise PricingError("OpenAI pricing table column count changed")
        row = dict(zip(headers, cells))
        # Specialized tables also contain embeddings, images and moderation.
        if section == "Specialized models" and row.get("Category") != "Codex":
            continue
        model = row["Model"]
        model_match = re.fullmatch(r"([a-zA-Z0-9._-]+)(?: \(<(\d+)K context length\))?", model)
        if not model_match:
            raise PricingError("Unrecognized OpenAI model identity")
        model = model_match.group(1)
        local_limit = int(model_match.group(2)) * 1000 if model_match.group(2) else context_limit
        bands = {}
        for band, prefix in (("short", "Short context "), ("long", "Long context ")):
            if "Short context input" not in headers:
                if band == "long":
                    continue
                columns = {"input": "Input", "cached_input": "Cached input", "output": "Output"}
            else:
                columns = {
                    name: prefix + label
                    for name, label in (
                        ("input", "input"),
                        ("cached_input", "cached input"),
                        ("cache_write", "cache writes"),
                        ("output", "output"),
                    )
                }
            if not set(columns.values()).issubset(headers):
                raise PricingError("OpenAI text pricing table layout changed")
            values = {name: _price(row[column]) for name, column in columns.items()}
            if values["input"] is not None and values["output"] is not None:
                bands[band] = values
        if not bands:
            continue
        if "long" in bands and local_limit is None:
            raise PricingError("Context threshold is missing")
        entry = {
            "usd_per_million_tokens": bands,
            "short_context_limit": local_limit if "long" in bands or model_match.group(2) else None,
        }
        prior = rates.setdefault(model, {}).get(tier)
        if prior is not None and prior != entry:
            raise PricingError("Conflicting OpenAI model rates")
        rates[model][tier] = entry
    if not any("standard" in model for model in rates.values()):
        raise PricingError("No standard text-model rates found")
    # Retain prose as well: a changed surcharge or qualification needs a new
    # version even when the numeric table itself is unchanged.
    terms = " ".join(
        line.strip()
        for line in document.splitlines()
        if not line.strip().startswith(("|", "<a ", "> For the complete"))
    )
    return {
        "rates": rates,
        "terms_sha256": _hash(" ".join(terms.split())),
        "currency": "USD",
        "unit": "million_tokens",
        "parser_version": 1,
    }


def make_snapshot(
    document,
    *,
    fetched_at,
    etag=None,
    last_modified=None,
    parser=parse_openai_rates,
    source_url=SOURCE_URL,
):
    data = parser(document)
    return {
        "schema_version": 1,
        "version": _hash(data),
        "source_url": source_url,
        "fetched_at": fetched_at,
        "etag": etag,
        "last_modified": last_modified,
        "data": data,
    }


def validate_snapshot(snapshot, *, source_url=SOURCE_URL):
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 1
        or snapshot.get("source_url") != source_url
        or not isinstance(snapshot.get("data"), dict)
        or snapshot.get("version") != _hash(snapshot["data"])
        or type(snapshot.get("fetched_at")) not in (int, float)
        or not Decimal(str(snapshot["fetched_at"])).is_finite()
    ):
        raise PricingError("Invalid pricing snapshot")
    data = snapshot["data"]
    if (
        data.get("currency") != "USD"
        or data.get("unit") != "million_tokens"
        or data.get("parser_version") != 1
    ):
        raise PricingError("Unsupported pricing units")
    if not isinstance(data.get("rates"), dict) or not data["rates"]:
        raise PricingError("Empty pricing snapshot")
    for model, tiers in data["rates"].items():
        if not isinstance(model, str) or not model or not isinstance(tiers, dict) or not tiers:
            raise PricingError("Invalid model rate entry")
        for rate in tiers.values():
            if not isinstance(rate, dict) or not isinstance(
                rate.get("usd_per_million_tokens"), dict
            ):
                raise PricingError("Invalid context rate entry")
            limit = rate.get("short_context_limit")
            if limit is not None and (type(limit) is not int or limit <= 0):
                raise PricingError("Invalid pricing context threshold")
            if "valid_through" in rate:
                date.fromisoformat(rate["valid_through"])
            bands = rate["usd_per_million_tokens"]
            if "short" not in bands or ("long" in bands and limit is None):
                raise PricingError("Invalid context bands")
            for prices in bands.values():
                if not isinstance(prices, dict) or not {"input", "output", "cached_input"}.issubset(
                    prices
                ):
                    raise PricingError("Invalid token rate entry")
                for price in prices.values():
                    if price is not None:
                        if not isinstance(price, str):
                            raise PricingError("Token rates must be decimal strings")
                        value = Decimal(price)
                        if not value.is_finite() or value < 0:
                            raise PricingError("Invalid token rate")
            schedules = rate.get("scheduled_prices", {})
            if not isinstance(schedules, dict):
                raise PricingError("Invalid effective rate schedule")
            for category, schedule in schedules.items():
                if (
                    category not in {"input", "output", "cached_input", "cache_write"}
                    or not isinstance(schedule, list)
                    or not schedule
                ):
                    raise PricingError("Invalid effective rate category")
                intervals = []
                for item in schedule:
                    if not isinstance(item, dict) or set(item) not in (
                        {"price", "through"},
                        {"price", "starting"},
                    ):
                        raise PricingError("Invalid effective rate interval")
                    if not isinstance(item["price"], str):
                        raise PricingError("Scheduled token rates must be decimal strings")
                    amount = Decimal(item["price"])
                    if not amount.is_finite() or amount < 0:
                        raise PricingError("Invalid scheduled token rate")
                    boundary = date.fromisoformat(item.get("through", item.get("starting")))
                    intervals.append(
                        (date.min, boundary) if "through" in item else (boundary, date.max)
                    )
                ordered = sorted(intervals)
                if any(left[1] >= right[0] for left, right in zip(ordered, ordered[1:])):
                    raise PricingError("Overlapping effective rate intervals")
    return snapshot


def _atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".pricing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class OpenAIPricingStore:
    """Refresh on use, with conditional GET and a last-good offline fallback."""

    provider = "openai"
    source_url = SOURCE_URL
    parser = staticmethod(parse_openai_rates)
    accept_header = "text/markdown, text/html;q=0.9"

    def __init__(self, cache_dir=None, *, opener=None, clock=None):
        self.cache_dir = (
            Path(cache_dir)
            if cache_dir is not None
            else Path.home() / ".cafe/pricing" / self.provider
        )
        self.opener = opener or urlopen
        self.clock = clock or time.time

    def _bundled(self):
        return validate_snapshot(
            json.loads(
                files("cafe")
                .joinpath(f"data/pricing/{self.provider}.json")
                .read_text(encoding="utf-8")
            ),
            source_url=self.source_url,
        )

    def _read_state(self):
        try:
            state = json.loads((self.cache_dir / "current.json").read_text(encoding="utf-8"))
            validate_snapshot(state["snapshot"], source_url=self.source_url)
            if (
                type(state.get("checked_at")) not in (int, float)
                or not Decimal(str(state["checked_at"])).is_finite()
            ):
                raise PricingError("Invalid pricing check timestamp")
            return state
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
            return {"snapshot": self._bundled(), "checked_at": 0, "error": None}

    def status(self):
        """Read-only inspection; never refresh or create a cache."""
        state = self._read_state()
        state["stale"] = (
            not state["checked_at"]
            or self.clock() - state["checked_at"] >= TTL_SECONDS
            or bool(state.get("error"))
        )
        return state

    def _fetch(self, headers):
        request = Request(self.source_url, headers={"Accept": self.accept_header, **headers})
        try:
            with self.opener(request, timeout=3) as response:
                if response.geturl() != self.source_url:
                    raise PricingError("Pricing source unexpectedly redirected")
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise PricingError("Pricing document exceeds size limit")
                return body.decode("utf-8"), response.headers
        except HTTPError as error:
            if error.code == 304:
                return None, error.headers
            raise

    @contextmanager
    def _lock(self):
        # Contending invocations use the last good card without waiting on HTTP.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with (self.cache_dir / "refresh.lock").open("a") as handle:
            if os.name == "posix":
                import fcntl

                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    yield False
                    return
                try:
                    yield True
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                yield True

    def _due(self, state):
        interval = RETRY_SECONDS if state.get("error") else TTL_SECONDS
        return not state["checked_at"] or self.clock() - state["checked_at"] >= interval

    def refresh_in_background(self):
        """Schedule at most one worker per source/cache; never wait for HTTP."""
        if not self._due(self.status()):
            return None
        key = (self.source_url, os.path.abspath(self.cache_dir))

        def refresh():
            try:
                self.get()
            except Exception:
                pass  # A cache/store fault is local to optional accounting.
            finally:
                with _BACKGROUND_LOCK:
                    _BACKGROUND_REFRESHES.discard(key)

        with _BACKGROUND_LOCK:
            if key in _BACKGROUND_REFRESHES:
                return None
            worker = Thread(target=refresh, name=f"cafe-pricing-{self.provider}", daemon=True)
            _BACKGROUND_REFRESHES.add(key)
            try:
                worker.start()
            except Exception:
                _BACKGROUND_REFRESHES.discard(key)
                raise
        return worker

    def _failed(self, error, *, persist):
        state = self.status()
        state.update(checked_at=self.clock(), error=type(error).__name__, stale=True)
        if persist:
            try:
                _atomic_json(self.cache_dir / "current.json", state)
            except Exception:
                pass
        return state

    def get(self, *, force=False, auto_update=True):
        state = self.status()
        if not auto_update or (not force and not self._due(state)):
            return state
        try:
            with self._lock() as acquired:
                if not acquired:
                    return self.status()
                state = self.status()
                if not force and not self._due(state):
                    return state
                try:
                    return self._refresh_locked(state)
                except Exception as error:
                    # Publish failures while still holding the refresh lock;
                    # another successful publisher cannot be overwritten.
                    return self._failed(error, persist=True)
        except Exception as error:
            # Lock creation/acquisition/release failures cannot authorize a
            # cache write. Return fallback without racing another publisher.
            return self._failed(error, persist=False)

    def _refresh_locked(self, state):
        """Fetch and publish a validated card under the caller's refresh lock."""
        snapshot = state["snapshot"]
        headers = {}
        # Only a previously fetched card's validator belongs to this client.
        # A packaged snapshot must first be checked with GET.
        if state["checked_at"]:
            if state.get("etag"):
                headers["If-None-Match"] = state["etag"]
            elif state.get("last_modified"):
                headers["If-Modified-Since"] = state["last_modified"]
        document, response_headers = self._fetch(headers)
        now = self.clock()
        if document is not None:
            candidate = make_snapshot(
                document,
                fetched_at=now,
                etag=response_headers.get("ETag"),
                last_modified=response_headers.get("Last-Modified"),
                parser=self.parser,
                source_url=self.source_url,
            )
            validate_snapshot(candidate, source_url=self.source_url)
            if candidate["version"] != snapshot["version"] or not state["checked_at"]:
                snapshot = candidate
        elif not headers:
            raise PricingError("Unexpected 304 without a cached validator")
        archive = self.cache_dir / f"{snapshot['version']}.json"
        if not archive.exists():
            _atomic_json(archive, snapshot)
        state = {
            "snapshot": snapshot,
            "checked_at": now,
            "error": None,
            "etag": response_headers.get(
                "ETag", state.get("etag") if document is None else None
            ),
            "last_modified": response_headers.get(
                "Last-Modified", state.get("last_modified") if document is None else None
            ),
            "stale": False,
        }
        _atomic_json(self.cache_dir / "current.json", state)
        return state

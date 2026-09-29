"""U12-U13: exact model evidence is separate from operational probes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


def _assessment(now: datetime) -> dict:
    source_date = now - timedelta(days=1)
    return {
        "provider": "provider-a",
        "model": "model-x",
        "version": "2026-09-01",
        "assessed_at": now.isoformat(),
        "workloads": ["implementation", "review"],
        "reasoning": "high",
        "capability_bands": {"coding": "strong", "review": "strong"},
        "limitations": ["Long contexts were not assessed."],
        "sources": [
            {
                "url": "https://provider.invalid/model-x",
                "retrieved_at": source_date.isoformat(),
                "fingerprint": "source-v1",
                "valid_until": (now + timedelta(days=30)).isoformat(),
            }
        ],
    }


def test_exact_model_assessment_expires_from_oldest_source_and_detects_change() -> None:
    module = load_kickoff_module("kickoff_models")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    record = _assessment(now)

    fresh = module.assess_model_evidence(record, now=now, current_sources={record["sources"][0]["url"]: "source-v1"})
    expired = module.assess_model_evidence(
        record,
        now=now + timedelta(days=7),
        current_sources={record["sources"][0]["url"]: "source-v1"},
    )
    changed = module.assess_model_evidence(record, now=now, current_sources={record["sources"][0]["url"]: "source-v2"})

    assert fresh["status"] == "hit"
    assert expired["status"] == "miss"
    assert changed["status"] == "miss" and changed["diagnostics"]


def test_aliases_missing_dates_or_scope_and_contradictions_cannot_hit() -> None:
    module = load_kickoff_module("kickoff_models")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    record = _assessment(now)

    alias = {**record, "version": "latest"}
    missing_source_date = {**record, "sources": [{**record["sources"][0], "retrieved_at": ""}]}
    missing_scope = {**record, "workloads": []}
    contradiction = module.assess_model_evidence(
        record,
        now=now,
        current_sources={record["sources"][0]["url"]: "source-v1"},
        contradictions=["new evidence conflicts with assessment"],
    )

    assert module.assess_model_evidence(alias, now=now)["status"] == "miss"
    assert module.assess_model_evidence(missing_source_date, now=now)["status"] == "miss"
    assert module.assess_model_evidence(missing_scope, now=now)["status"] == "miss"
    assert contradiction["status"] == "miss"


def test_operational_probe_is_not_model_suitability_evidence() -> None:
    module = load_kickoff_module("kickoff_models")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    incomplete = {"provider": "provider-a", "model": "model-x", "available": True}

    report = module.assess_model_evidence(incomplete, now=now)

    assert report["status"] == "miss"
    assert report["evidence_kind"] == "capability_assessment"


def test_future_dates_and_earlier_source_validity_cannot_extend_freshness() -> None:
    module = load_kickoff_module("kickoff_models")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    record = _assessment(now)
    future = {
        **record,
        "assessed_at": (now + timedelta(hours=1)).isoformat(),
        "sources": [{
            **record["sources"][0],
            "retrieved_at": (now + timedelta(hours=2)).isoformat(),
        }],
    }
    limited = {
        **record,
        "sources": [{
            **record["sources"][0],
            "valid_until": (now - timedelta(minutes=1)).isoformat(),
        }],
    }

    assert module.assess_model_evidence(future, now=now)["status"] == "miss"
    assert module.assess_model_evidence(limited, now=now)["status"] == "miss"

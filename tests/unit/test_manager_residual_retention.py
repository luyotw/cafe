"""U3/U8/U9/U10, I2/I3/I5/I8: Manager residual evidence survives retained reports."""

import shutil
from decimal import Decimal

import pytest

from cafe.core.types import TokenUsage
from cafe.core.usage import merge_token_usage_stats
from cafe.manager.costs import CostStore, inclusive_report, manager_usage_sink, preserve_worker_cost
from cafe.services.cost_summary import summarize_sources
from tests.unit.test_manager_costs import cost_journey
from tests.unit.test_native_accounting import record
from tests.unit.test_native_admission_reports import segments
from tests.unit.test_workflow_cost_summary import record as legacy_record


def projected_usage(rows, residual=0.25):
    return TokenUsage.model_validate(
        merge_token_usage_stats(
            dict(input_tokens=7, total_cost_usd=residual),
            TokenUsage(
                input_tokens=sum(row["usage"]["input_tokens"] for row in rows),
                output_tokens=sum(row["usage"]["output_tokens"] for row in rows),
                total_cost_usd=sum(float(row["amount_usd"]) for row in rows),
                cost_records=rows,
            ),
        )
    )


def test_manager_projected_residual_survives_replay_retention_and_source_removal(tmp_path):
    root, issue = cost_journey(tmp_path)
    usage = projected_usage(list(segments()))
    sink = manager_usage_sink(root, "topic", "wf", "manager-residual")
    for _ in range(2):
        sink(usage)
        result = inclusive_report(root, "topic", "wf", issue_dir=issue)
        assert result["manager"]["known"] == Decimal("0.25")
        assert result["manager"]["estimated"] == 0
        assert result["combined"]["known"] == Decimal("1.75")
        assert result["manager"]["incomplete"]
    preserve_worker_cost(root, issue, "topic", "wf")
    shutil.rmtree(issue)
    sink(usage)
    result = inclusive_report(root, "topic", "wf")
    assert result["manager"]["legacy"] == Decimal("0.25")
    assert result["combined"]["known"] == Decimal("1.75")
    sources = CostStore(root, "topic", "wf").read()["manager_sources"]
    assert len(sources) == 1
    assert sources[0]["legacy_residual"] == 0.25


@pytest.mark.parametrize("overlap", [True, False])
def test_records_only_progress_final_and_checkpoint_preserve_proven_residual(tmp_path, overlap):
    root, issue = cost_journey(tmp_path)
    initial = [record(100, "progress")]
    if overlap:
        initial.append(segments()[1])
    sink = manager_usage_sink(root, "topic", "wf", "manager-progress")
    sink(projected_usage(initial))
    for end, status in [(100, "progress"), (150, "progress"), (200, "final"), (200, "final")]:
        sink(TokenUsage(cost_records=[record(end, status)]))
        sink(TokenUsage())  # Checkpoint with no new amount or records.
        result = inclusive_report(root, "topic", "wf", issue_dir=issue)
        assert result["manager"]["legacy"] == Decimal("0.25")
        assert result["manager"]["known"] == (
            Decimal("0.25") if overlap else Decimal(str(end / 100)) + Decimal("0.25")
        )
    preserve_worker_cost(root, issue, "topic", "wf")
    shutil.rmtree(issue)
    assert inclusive_report(root, "topic", "wf")["manager"]["legacy"] == Decimal("0.25")


def test_explicit_residual_replacement_including_zero_is_idempotent(tmp_path):
    root, issue = cost_journey(tmp_path)
    rows = list(segments())
    sink = manager_usage_sink(root, "topic", "wf", "manager-replacement")
    sink(projected_usage(rows))
    for value in [0.5, 0.5, 0.0, 0.0, 0.25]:
        sink(TokenUsage(accounting_residual={"total_cost_usd": value}))
        sink(TokenUsage(cost_records=rows))
        result = inclusive_report(root, "topic", "wf", issue_dir=issue)
        assert result["manager"]["known"] == Decimal(str(value))
        assert result["manager"]["estimated"] == 0
        assert result["combined"]["known"] == Decimal("1.5") + Decimal(str(value))
        assert result["manager"]["incomplete"]


def test_unprojected_legacy_aggregate_remains_compatible(tmp_path):
    root, issue = cost_journey(tmp_path)
    rows = [legacy_record("reported", "1")]
    sink = manager_usage_sink(root, "topic", "wf", "legacy-manager")
    usage = TokenUsage(total_cost_usd=1.25, cost_records=rows)
    for update in [usage, usage, TokenUsage(cost_records=rows)]:
        sink(update)
        result = inclusive_report(root, "topic", "wf", issue_dir=issue)
        assert result["manager"]["legacy"] == Decimal("0.25")
        assert result["manager"]["known"] == Decimal("1.25")


@pytest.mark.parametrize("overlap", [True, False])
def test_retained_source_distinguishes_unknown_from_verified_zero(tmp_path, overlap):
    root, _ = cost_journey(tmp_path)
    rows = list(segments()) if overlap else [record(0, "final")]
    sink = manager_usage_sink(root, "topic", "wf", "manager-zero")
    sink(TokenUsage(cost_records=rows, accounting_residual={"total_cost_usd": 0.0}))
    sink(TokenUsage(cost_records=rows))
    source_summary = summarize_sources(CostStore(root, "topic", "wf").read()["manager_sources"])
    assert source_summary["known"] == 0
    assert source_summary["incomplete"] is overlap
    assert source_summary["counts"]["estimated"] == (0 if overlap else 1)
    assert source_summary["native_usage"]["complete"] is not overlap

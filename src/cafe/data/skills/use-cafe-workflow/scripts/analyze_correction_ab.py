#!/usr/bin/env python3
"""Analyze controlled fresh-vs-resume correction pairs without pricing guesses."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
BOOTSTRAP_SAMPLES = 10_000
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
QUALITY_FIELDS = ("success", "artifact_correct", "checklist_correct", "baton_correct")
PROTOCOL_FIELDS = ("randomized_order", "isolated_worktrees", "actual_billed_credits")
ARM_ORDERS = frozenset({"fresh_first", "resume_first"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_OID_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class ManifestError(ValueError):
    """Raised when an experiment manifest is incomplete or not paired."""


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"{field} must be a number")
    if not math.isfinite(value):
        raise ManifestError(f"{field} must be finite")
    if value < 0:
        raise ManifestError(f"{field} must be non-negative")
    return float(value)


def _integer(value: Any, *, field: str) -> int:
    number = _number(value, field=field)
    if not number.is_integer():
        raise ManifestError(f"{field} must be an integer")
    return int(number)


def _boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ManifestError(f"{field} must be a boolean")
    return value


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value.strip()


def _sha256(value: Any, *, field: str) -> str:
    digest = _text(value, field=field).lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise ManifestError(f"{field} must be a 64-character lowercase SHA-256")
    return digest


def _git_oid(value: Any, *, field: str) -> str:
    oid = _text(value, field=field).lower()
    if not GIT_OID_PATTERN.fullmatch(oid):
        raise ManifestError(f"{field} must be a 40- or 64-character lowercase Git OID")
    return oid


def _validate_arm(raw: Any, *, field: str, expected_policy: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ManifestError(f"{field} must be an object")
    policy = _text(raw.get("policy"), field=f"{field}.policy")
    if policy != expected_policy:
        raise ManifestError(
            f"{field}.policy must be {expected_policy!r}, got {policy!r}"
        )
    arm: dict[str, Any] = {
        "policy": policy,
        "credits": _number(raw.get("credits"), field=f"{field}.credits"),
        "wall_seconds": _number(
            raw.get("wall_seconds"), field=f"{field}.wall_seconds"
        ),
        "high_severity_findings": _integer(
            raw.get("high_severity_findings"),
            field=f"{field}.high_severity_findings",
        ),
    }
    for token_field in TOKEN_FIELDS:
        arm[token_field] = _integer(
            raw.get(token_field), field=f"{field}.{token_field}"
        )
    if arm["cached_input_tokens"] > arm["input_tokens"]:
        raise ManifestError(f"{field}.cached_input_tokens exceeds input_tokens")
    for quality_field in QUALITY_FIELDS:
        arm[quality_field] = _boolean(
            raw.get(quality_field), field=f"{field}.{quality_field}"
        )
    return arm


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and strictly validate one paired experiment manifest."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("manifest root must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"schema_version must be {SCHEMA_VERSION}")
    raw_protocol = raw.get("protocol")
    if not isinstance(raw_protocol, dict):
        raise ManifestError("protocol must be an object")
    protocol = {
        field: _boolean(raw_protocol.get(field), field=f"protocol.{field}")
        for field in PROTOCOL_FIELDS
    }
    raw_pairs = raw.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ManifestError("pairs must be a non-empty list")

    pairs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_pair in enumerate(raw_pairs):
        field = f"pairs[{index}]"
        if not isinstance(raw_pair, dict):
            raise ManifestError(f"{field} must be an object")
        pair_id = _text(raw_pair.get("id"), field=f"{field}.id")
        if pair_id in seen_ids:
            raise ManifestError(f"duplicate pair id: {pair_id}")
        seen_ids.add(pair_id)
        arm_order = _text(raw_pair.get("arm_order"), field=f"{field}.arm_order")
        if arm_order not in ARM_ORDERS:
            raise ManifestError(
                f"{field}.arm_order must be one of: {', '.join(sorted(ARM_ORDERS))}"
            )
        pairs.append(
            {
                "id": pair_id,
                "model": _text(raw_pair.get("model"), field=f"{field}.model"),
                "effort": _text(raw_pair.get("effort"), field=f"{field}.effort"),
                "cli_version": _text(
                    raw_pair.get("cli_version"), field=f"{field}.cli_version"
                ),
                "repo_sha": _git_oid(
                    raw_pair.get("repo_sha"), field=f"{field}.repo_sha"
                ),
                "correction_sha256": _sha256(
                    raw_pair.get("correction_sha256"),
                    field=f"{field}.correction_sha256",
                ),
                "playbook_sha256": _sha256(
                    raw_pair.get("playbook_sha256"),
                    field=f"{field}.playbook_sha256",
                ),
                "environment_sha256": _sha256(
                    raw_pair.get("environment_sha256"),
                    field=f"{field}.environment_sha256",
                ),
                "arm_order": arm_order,
                "resume": _validate_arm(
                    raw_pair.get("resume"),
                    field=f"{field}.resume",
                    expected_policy="resume",
                ),
                "fresh": _validate_arm(
                    raw_pair.get("fresh"),
                    field=f"{field}.fresh",
                    expected_policy="fresh",
                ),
            }
        )
    return {"protocol": protocol, "pairs": pairs}


def _reduction(control: float, treatment: float) -> float:
    if control == 0:
        return 0.0 if treatment == 0 else float("-inf")
    return (control - treatment) / control


def _median_ci(values: list[float]) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    generator = random.Random(0)
    sample_size = len(values)
    medians = sorted(
        statistics.median(generator.choices(values, k=sample_size))
        for _ in range(BOOTSTRAP_SAMPLES)
    )
    low_index = int(0.025 * (BOOTSTRAP_SAMPLES - 1))
    high_index = int(0.975 * (BOOTSTRAP_SAMPLES - 1))
    return medians[low_index], medians[high_index]


def _quality_passes(arm: dict[str, Any]) -> bool:
    return (
        all(arm[field] for field in QUALITY_FIELDS)
        and arm["high_severity_findings"] == 0
    )


def analyze(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return per-pair reductions, aggregate medians, and rollout readiness."""
    rows: list[dict[str, Any]] = []
    pairs: Iterable[dict[str, Any]] = manifest["pairs"]
    protocol = manifest["protocol"]
    for pair in pairs:
        resume = pair["resume"]
        fresh = pair["fresh"]
        resume_uncached = resume["input_tokens"] - resume["cached_input_tokens"]
        fresh_uncached = fresh["input_tokens"] - fresh["cached_input_tokens"]
        for baseline_field in ("credits", "wall_seconds", "input_tokens", "output_tokens"):
            if resume[baseline_field] <= 0:
                raise ManifestError(
                    f"pair {pair['id']!r} resume.{baseline_field} must be positive"
                )
        if resume_uncached <= 0:
            raise ManifestError(
                f"pair {pair['id']!r} resume uncached input must be positive"
            )
        rows.append(
            {
                "id": pair["id"],
                "model": pair["model"],
                "effort": pair["effort"],
                "cli_version": pair["cli_version"],
                "repo_sha": pair["repo_sha"],
                "correction_sha256": pair["correction_sha256"],
                "playbook_sha256": pair["playbook_sha256"],
                "environment_sha256": pair["environment_sha256"],
                "arm_order": pair["arm_order"],
                "credit_reduction": _reduction(
                    resume["credits"], fresh["credits"]
                ),
                "input_reduction": _reduction(
                    resume["input_tokens"], fresh["input_tokens"]
                ),
                "uncached_input_reduction": _reduction(
                    resume_uncached, fresh_uncached
                ),
                "output_reduction": _reduction(
                    resume["output_tokens"], fresh["output_tokens"]
                ),
                "wall_reduction": _reduction(
                    resume["wall_seconds"], fresh["wall_seconds"]
                ),
                "resume_quality_pass": _quality_passes(resume),
                "fresh_quality_pass": _quality_passes(fresh),
            }
        )

    metrics = (
        "credit_reduction",
        "input_reduction",
        "uncached_input_reduction",
        "output_reduction",
        "wall_reduction",
    )
    aggregate: dict[str, dict[str, float]] = {}
    for metric in metrics:
        values = [row[metric] for row in rows]
        low, high = _median_ci(values)
        aggregate[metric] = {
            "median": statistics.median(values),
            "ci95_low": low,
            "ci95_high": high,
        }

    quality_regressions = [
        row["id"]
        for row in rows
        if row["resume_quality_pass"] and not row["fresh_quality_pass"]
    ]
    fresh_quality_failures = [
        row["id"] for row in rows if not row["fresh_quality_pass"]
    ]
    order_counts = {
        order: sum(row["arm_order"] == order for row in rows)
        for order in sorted(ARM_ORDERS)
    }
    order_balanced = (
        all(count > 0 for count in order_counts.values())
        and max(order_counts.values()) - min(order_counts.values()) <= 1
    )
    protocol_ready = all(protocol.values()) and order_balanced
    aggregate_available = (
        len(rows) >= 10
        and protocol_ready
        and not quality_regressions
        and not fresh_quality_failures
    )
    claim_ready = aggregate_available and aggregate["credit_reduction"]["median"] >= 0.30
    report = {
        "schema_version": SCHEMA_VERSION,
        "pair_count": len(rows),
        "protocol": protocol,
        "protocol_ready": protocol_ready,
        "arm_order_counts": order_counts,
        "arm_order_balanced": order_balanced,
        "pairs": rows,
        "aggregate_available": aggregate_available,
        "quality_regressions": quality_regressions,
        "fresh_quality_failures": fresh_quality_failures,
        "claim_ready": claim_ready,
        "claim_rule": (
            "attested randomized isolated runs using actual billed credits; balanced "
            "arm order; at least 10 pairs; median credit reduction >= 30%; every fresh arm "
            "passes success/artifact/checklist/baton checks with no high-severity "
            "finding; no paired quality regression"
        ),
    }
    if aggregate_available:
        report["aggregate"] = aggregate
    return report


def _percent(value: float) -> str:
    if value == float("-inf"):
        return "-∞"
    return f"{value * 100:.1f}%"


def format_markdown(report: dict[str, Any]) -> str:
    """Format a compact per-pair and aggregate experiment report."""
    lines = [
        "# Correction Session Paired A/B",
        "",
        "| Pair | Model / effort | Credits Δ | Uncached input Δ | Output Δ | "
        "Wall Δ | Fresh quality |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["pairs"]:
        lines.append(
            "| {id} | {model} / {effort} | {credits} | {uncached} | {output} | "
            "{wall} | {quality} |".format(
                id=row["id"],
                model=row["model"],
                effort=row["effort"],
                credits=_percent(row["credit_reduction"]),
                uncached=_percent(row["uncached_input_reduction"]),
                output=_percent(row["output_reduction"]),
                wall=_percent(row["wall_reduction"]),
                quality="pass" if row["fresh_quality_pass"] else "fail",
            )
        )
    aggregate = report.get("aggregate")
    if aggregate:
        lines.extend(
            [
                "",
                "| Metric | Median | 95% bootstrap CI |",
                "| --- | ---: | ---: |",
            ]
        )
        for metric, values in aggregate.items():
            lines.append(
                f"| {metric} | {_percent(values['median'])} | "
                f"{_percent(values['ci95_low'])} to {_percent(values['ci95_high'])} |"
            )
    else:
        lines.extend(
            [
                "",
                "- Aggregate statistics are unavailable until the paired evidence is "
                "complete, balanced, and quality-preserving.",
            ]
        )
    lines.extend(
        [
            "",
            f"- Pair count: {report['pair_count']}",
            f"- Protocol ready: {'yes' if report['protocol_ready'] else 'no'}",
            f"- Arm order counts: {report['arm_order_counts']}",
            f"- Quality regressions: {report['quality_regressions'] or 'none'}",
            f"- Fresh quality failures: {report['fresh_quality_failures'] or 'none'}",
            f"- Claim ready: {'yes' if report['claim_ready'] else 'no'}",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        report = analyze(load_manifest(args.manifest))
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

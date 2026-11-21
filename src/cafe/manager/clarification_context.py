"""Bounded, exact excerpts for a Manager preparing a clarification assessment."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any, Mapping, Sequence

from cafe.core.human_tasks import HumanTaskQuestion

MAX_CONTEXT_BYTES = 16 * 1024
_MAX_EXCERPTS = 12
_MAX_PER_SOURCE = 4
_WINDOW_RADIUS = 4
_MAX_EXCERPT_BYTES = 2400
_MAX_QUERY_BYTES = 8192
_CJK = re.compile(r"[\u3400-\u9fff]+")
_WORDS = re.compile(r"[a-z][a-z_0-9]{2,}")


def _terms(text: str) -> set[str]:
    words = set(_WORDS.findall(text.lower()))
    for run in _CJK.findall(text):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return words


def _windows(text: str, query: set[str]) -> list[tuple[float, int, int, str]]:
    lines = text.splitlines(keepends=True)
    matches = [_terms(line) & query for line in lines]
    counts = Counter(term for row in matches for term in row)
    weights = {term: math.log1p(len(lines) / count) for term, count in counts.items()}
    scores = [
        sum(weights[term] for term in sorted(row)) / max(40, len(line)) ** 0.4
        for line, row in zip(lines, matches)
    ]
    selected: list[tuple[float, int, int, str]] = []
    for i in sorted(range(len(lines)), key=scores.__getitem__, reverse=True):
        if scores[i] == 0 or len(selected) == _MAX_PER_SOURCE:
            break
        lo, hi = max(0, i - _WINDOW_RADIUS), min(len(lines), i + _WINDOW_RADIUS + 1)
        excerpt = "".join(lines[lo:hi])
        if len(excerpt.encode("utf-8")) > _MAX_EXCERPT_BYTES:
            lo, hi, excerpt = i, i + 1, lines[i]
        if len(excerpt.encode("utf-8")) > _MAX_EXCERPT_BYTES:
            continue
        if any(lo < end and start <= hi for _, start, end, _ in selected):
            continue
        selected.append((scores[i], lo + 1, hi, excerpt))
    return selected


def clarification_context(
    questions: Sequence[HumanTaskQuestion],
    delivery_contract: Mapping[str, Any],
    sources: Mapping[str, str],
    paths: Mapping[str, str],
) -> dict[str, Any]:
    """Project original passages as navigation aids, without evaluating an answer."""
    query_text = "\n".join(
        text for question in questions for text in (question.prompt, *question.options)
    )
    if not query_text or len(query_text.encode("utf-8")) > _MAX_QUERY_BYTES:
        return {"status": "unavailable", "reason": "clarification_questions_missing_or_oversized"}
    query = _terms(query_text)
    catalog = [
        {"source": source, "path": paths[source]}
        for source in sorted(paths)
        if source in sources
    ]
    excerpts: list[dict[str, Any]] = []
    result = {
        "status": "available",
        "questions": [question.model_dump(mode="json") for question in questions],
        "delivery_contract": dict(delivery_contract),
        "source_catalog": catalog,
        "excerpts": excerpts,
    }

    def fits() -> bool:
        return len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= MAX_CONTEXT_BYTES

    if not fits():
        return {"status": "unavailable", "reason": "clarification_context_budget_exceeded"}
    candidates = sorted(
        (
            (score, source, lo, hi, excerpt)
            for source in sorted(paths)
            if source in sources
            for score, lo, hi, excerpt in _windows(sources[source], query)
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    for _, source, lo, hi, excerpt in candidates:
        if len(excerpts) == _MAX_EXCERPTS:
            break
        excerpts.append({"source": source, "start_line": lo, "end_line": hi, "text": excerpt})
        if not fits():
            excerpts.pop()
    return result

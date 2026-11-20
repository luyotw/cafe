"""Audit evidence that can confirm advancement of a saved response."""

import pytest

from cafe.core.blackboard import EventEntry
from cafe.core.task_continuation import _workflow_advanced


@pytest.mark.parametrize(
    ("events", "advanced"),
    [
        ([("step_started", "plan", {}), ("step_interrupted", "plan", {})], False),
        ([("transition", "develop", {"from": "plan", "to": "develop"})], True),
        ([("workflow_completed", "plan", {})], True),
        ([("human_task_materialized", "plan", {"trigger": "confirm_output"})], True),
        ([("agent_execution_task_materialized", "plan", {})], False),
        (
            [
                ("transition", "plan", {"from": "spec", "to": "plan"}),
                ("human_task_materialized", "plan", {"trigger": "confirm_output"}),
            ],
            False,
        ),
    ],
)
def test_only_the_continuation_phases_durable_outcome_confirms_advancement(events, advanced):
    history = [
        EventEntry(timestamp="", step=step, event_type=kind, message="", data=data)
        for kind, step, data in events
    ]
    assert _workflow_advanced(history, "plan") is advanced

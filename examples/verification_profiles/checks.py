"""Self-contained executable checks; no product dependency or wall-clock sleeps."""

import asyncio
import json
import os
import random
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def baseline(name):
    session = {"authenticated": True}
    if name == "logout":
        session.clear()
        assert not session
    else:
        assert session["authenticated"]
    return {"suite": name, "passed": True}


def midnight():
    instant = datetime.fromisoformat(os.environ["FROZEN_CLOCK"])
    assert instant.tzinfo is not None
    zone = ZoneInfo(os.environ["TZ"])
    before = instant.astimezone(zone).date()
    intended = (instant + timedelta(seconds=1)).astimezone(zone).date()
    observed = before if os.environ["FAULT"] == "midnight" else intended
    assert intended != before, "fixture must cross local midnight"
    assert observed == intended, "date must follow the injected clock across midnight"
    return {"suite": "midnight", "before": str(before), "after": str(observed)}


async def notification():
    assert os.environ["BARRIER"] == "notification-before-response"
    task_id = random.Random(int(os.environ["SEED"])).randrange(1, 1_000_000)
    release_response = asyncio.Event()
    request_started = asyncio.Event()
    persisted, visible, order = {}, {}, []

    async def save():
        request_started.set()
        await release_response.wait()
        persisted[task_id] = {"id": task_id, "saved": True}
        order.append("response")
        # A response must preserve an already observed source notification.
        if os.environ["FAULT"] == "notification":
            visible.clear()
        else:
            visible[task_id] = persisted[task_id]

    response = asyncio.create_task(save())
    await request_started.wait()
    visible[task_id] = {"id": task_id, "saved": False}
    order.append("notification")
    assert not response.done() and not persisted
    release_response.set()
    await response
    assert order == ["notification", "response"]
    assert set(visible) == set(persisted) == {task_id}
    assert visible[task_id]["saved"] is True
    return {
        "suite": "notification",
        "order": order,
        "visible_ids": list(visible),
        "persisted_ids": list(persisted),
    }


def main():
    name = sys.argv[1]
    if name == "midnight":
        result = midnight()
    elif name == "notification":
        result = asyncio.run(notification())
    else:
        assert name in {"bootstrap", "logout"}
        result = baseline(name)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

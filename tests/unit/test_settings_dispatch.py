"""Mode-neutral settings dispatch and owner adapter boundaries."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.settings import (
    SettingUpdateRequest,
    _declared_setting_owners,
    dispatch_setting_update,
)


class _EntryPoint:
    def __init__(self, name, handler):
        self.name = name
        self._handler = handler

    def load(self):
        return self._handler


def test_dispatch_loads_only_the_named_owner_handler(monkeypatch) -> None:
    requests = []
    expected = SimpleNamespace(status="proposed", changes={"driver": {}})
    entries = [
        _EntryPoint("driver", lambda request: requests.append(request) or expected),
        _EntryPoint("pr.auto_create", lambda _request: pytest.fail("wrong owner")),
    ]
    monkeypatch.setattr("cafe.settings._setting_entry_points", lambda: entries)
    request = SettingUpdateRequest(Path("issue.yaml"), {"mode": "unattended"}, True)

    assert dispatch_setting_update("driver", request) is expected
    assert requests == [request]


def test_dispatch_rejects_unknown_or_duplicate_owner_before_call(monkeypatch) -> None:
    called = []
    entry = _EntryPoint("driver", lambda _request: called.append(True))
    request = SettingUpdateRequest(Path("issue.yaml"), {}, False)

    monkeypatch.setattr("cafe.settings._setting_entry_points", lambda: [entry])
    with pytest.raises(ValueError, match="unsupported or protected"):
        dispatch_setting_update("phases", request)

    monkeypatch.setattr("cafe.settings._setting_entry_points", lambda: [entry, entry])
    with pytest.raises(ValueError, match="ambiguous"):
        dispatch_setting_update("driver", request)
    assert called == []


def test_packaged_setting_owner_adapters_are_declared_and_loadable() -> None:
    declared = _declared_setting_owners()

    assert {entry.name for entry in declared} == {"driver", "pr.auto_create"}
    assert all(callable(entry.load()) for entry in declared)

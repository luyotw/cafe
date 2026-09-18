"""Mode-neutral settings dispatch and owner adapter boundaries."""

from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib

from cafe.settings import SettingUpdateRequest, dispatch_setting_update


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
    root = Path(__file__).parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = project["project"]["entry-points"]["cafe.setting_updates"]

    assert set(declared) == {"driver", "pr.auto_create"}
    for name, value in declared.items():
        assert callable(EntryPoint(name=name, value=value, group="cafe.setting_updates").load())

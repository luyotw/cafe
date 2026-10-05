"""Both safe parser backends preserve CAFE's YAML data and rejection contracts."""

import importlib.util
from datetime import date
from io import StringIO

import pytest
import yaml

from cafe.utils import yaml_utils


@pytest.fixture(params=[yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)])
def safe_parser(request, monkeypatch):
    monkeypatch.setattr(yaml_utils, "SafeLoader", request.param)
    return yaml_utils.safe_load


def test_safe_backends_preserve_yaml_types_aliases_and_merge_keys(safe_parser):
    data = safe_parser(StringIO(
        "defaults: &defaults {enabled: true, count: 3}\n"
        "custom: {<<: *defaults, count: 4}\n"
        "on: {await_agent: done}\n"
        "text: 'on'\nempty: null\ndate: 2026-10-05\n"
    ))
    assert data["custom"] == {"enabled": True, "count": 4}
    assert data[True] == {"await_agent": "done"}
    assert data["text"] == "on" and data["empty"] is None
    assert data["date"] == date(2026, 10, 5)


@pytest.mark.parametrize("document", [
    "!!python/object/apply:builtins.str [forbidden]",
    "!!python/object:builtins.object {}",
    "broken: [",
    "one: 1\n---\ntwo: 2\n",
])
def test_safe_backends_reject_unsafe_malformed_and_multiple_documents(safe_parser, document):
    with pytest.raises(yaml.YAMLError):
        safe_parser(document)


def test_safe_backends_accept_empty_documents(safe_parser):
    assert safe_parser("") is None


def test_import_without_libyaml_keeps_the_pure_python_safe_backend(monkeypatch):
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    spec = importlib.util.spec_from_file_location("fallback_yaml_utils", yaml_utils.__file__)
    fallback = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fallback)
    assert fallback.SafeLoader is yaml.SafeLoader
    assert fallback.safe_load("enabled: true") == {"enabled": True}
    with pytest.raises(yaml.YAMLError):
        fallback.safe_load("!!python/object:builtins.object {}")

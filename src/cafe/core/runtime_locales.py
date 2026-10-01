"""Load developer-authored runtime copy and render simple named placeholders."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files
from string import Formatter
from types import MappingProxyType
from typing import Mapping

import yaml

from cafe.core.conversation_locale import SUPPORTED_TEXT_LOCALES, select_text_locale

_MESSAGE_KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")
_PLACEHOLDER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class LocaleCatalogError(ValueError):
    """Packaged runtime copy or its interpolation arguments violate the contract."""


class _CatalogLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently replacing authored messages."""

    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise LocaleCatalogError("message keys must be strings")
            if key in keys:
                raise LocaleCatalogError(f"duplicate message key: {key}")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def _placeholders(template: str, context: str) -> frozenset[str]:
    names = set()
    try:
        for _, name, spec, conversion in Formatter().parse(template):
            if name is None:
                continue
            if not _PLACEHOLDER.fullmatch(name) or spec or conversion:
                raise LocaleCatalogError(
                    f"{context}: placeholders must be simple names without formatting or conversion"
                )
            names.add(name)
    except ValueError as exc:
        raise LocaleCatalogError(f"{context}: invalid placeholder syntax: {exc}") from exc
    return frozenset(names)


@lru_cache(maxsize=1)
def load_catalogs() -> Mapping[str, Mapping[str, str]]:
    """Read immutable packaged catalogs and validate matching keys/placeholders.

    Resource paths are package-relative, independent of the working directory.
    Invalid authored data is an error; unsupported locale input still silently
    selects English through the existing locale resolver.
    """
    catalogs = {}
    contracts = {}
    for locale in SUPPORTED_TEXT_LOCALES:
        resource = f"data/locales/{locale}.yaml"
        try:
            document = yaml.load(
                files("cafe").joinpath(resource).read_text(encoding="utf-8"),
                Loader=_CatalogLoader,
            )
        except (OSError, UnicodeError, yaml.YAMLError, LocaleCatalogError) as exc:
            raise LocaleCatalogError(f"{resource}: cannot load runtime catalog: {exc}") from exc
        if not isinstance(document, dict) or not document:
            raise LocaleCatalogError(f"{resource}: catalog must be a non-empty message mapping")
        contract = {}
        for key, template in document.items():
            if not isinstance(key, str) or not _MESSAGE_KEY.fullmatch(key):
                raise LocaleCatalogError(f"{resource}: invalid message key: {key!r}")
            context = f"{resource}: {key}"
            if not isinstance(template, str) or not template.strip():
                raise LocaleCatalogError(f"{context}: message must be a non-empty string")
            contract[key] = _placeholders(template, context)
        if contracts:
            baseline = next(iter(contracts.values()))
            if contract.keys() != baseline.keys():
                different = sorted(contract.keys() ^ baseline.keys())
                raise LocaleCatalogError(f"{resource}: mismatched message keys: {different}")
            for key in baseline:
                if contract[key] != baseline[key]:
                    raise LocaleCatalogError(f"{resource}: {key}: mismatched placeholders")
        contracts[locale] = contract
        catalogs[locale] = MappingProxyType(document)
    return MappingProxyType(catalogs)


def render_text(key: str, *, locale: str | None = None, **values: str | int) -> str:
    """Select copy and interpolate exactly once without altering workflow state.

    Callers bound untrusted diagnostic values before rendering. Inserted braces
    remain literal; templates allow only named fields and escaped literal braces.
    """
    selected = select_text_locale(locale)
    catalog = load_catalogs()[selected]
    context = f"data/locales/{selected}.yaml: {key}"
    if key not in catalog:
        raise LocaleCatalogError(f"{context}: unknown message key")
    template = catalog[key]
    expected = _placeholders(template, context)
    if values.keys() != expected:
        raise LocaleCatalogError(
            f"{context}: incorrect arguments; missing {sorted(expected - values.keys())}, "
            f"unexpected {sorted(values.keys() - expected)}"
        )
    if any(not isinstance(value, (str, int)) for value in values.values()):
        raise LocaleCatalogError(f"{context}: placeholder values must be strings or integers")
    return template.format_map(values)

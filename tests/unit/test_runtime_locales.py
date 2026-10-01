"""Packaged runtime copy validates its keys and interpolates only named values."""

from importlib.resources import files

import pytest

from cafe.core import runtime_locales


@pytest.fixture
def catalogs(monkeypatch, tmp_path):
    """Replace only the resource I/O boundary, retaining production validation."""
    root = tmp_path / "data" / "locales"
    root.mkdir(parents=True)
    for locale in ("en-US", "zh-TW"):
        (root / f"{locale}.yaml").write_text('sample.message: "{{literal}} {value}"\n')
    monkeypatch.setattr(runtime_locales, "files", lambda package: tmp_path)
    runtime_locales.load_catalogs.cache_clear()
    yield root
    runtime_locales.load_catalogs.cache_clear()


def test_packaged_catalogs_share_keys_and_are_read_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    loaded = runtime_locales.load_catalogs()
    assert set(loaded) == {"en-US", "zh-TW"}
    assert set(loaded["en-US"]) == set(loaded["zh-TW"])
    assert "workspace.correction" in loaded["en-US"]
    with pytest.raises(TypeError):
        loaded["en-US"]["workspace.correction"] = "changed"
    for locale in loaded:
        assert files("cafe").joinpath(f"data/locales/{locale}.yaml").is_file()


@pytest.mark.parametrize(
    "locale,expected",
    [
        ("zh-TW", "Chinese"),
        ("zh-Hant", "Chinese"),
        ("zh-HK", "Chinese"),
        ("zh-MO", "Chinese"),
        (" ZH_hant_hk ", "Chinese"),
        ("zh-CN", "English"),
        ("zh-Hans-TW", "English"),
        ("zh", "English"),
        ("fr-FR", "English"),
        (None, "English"),
        ("", "English"),
        ("auto", "English"),
        ("zh//TW", "English"),
    ],
)
def test_renderer_reuses_locale_aliases_and_silent_fallback(catalogs, locale, expected):
    (catalogs / "en-US.yaml").write_text('sample.message: "English {value}"\n')
    (catalogs / "zh-TW.yaml").write_text('sample.message: "Chinese {value}"\n')
    assert runtime_locales.render_text("sample.message", locale=locale, value=3) == f"{expected} 3"


def test_interpolation_is_single_pass_and_supports_literal_template_braces(catalogs):
    value = "owner/{value}/{missing!r}.txt"
    assert runtime_locales.render_text("sample.message", value=value) == f"{{literal}} {value}"


@pytest.mark.parametrize(
    "document",
    [
        "[a, b]",
        "{}",
        "sample.message: 42",
        "sample.message: null",
        'sample.message: " "',
        '42: "text"',
        'bad key: "text"',
        'sample.message: "{value"',
        'sample.message: "{value.name}"',
        'sample.message: "{value[0]}"',
        'sample.message: "{}"',
        'sample.message: "{value!r}"',
        'sample.message: "{value:>20}"',
        'sample.message: "{value:{width}}"',
        'sample.message: "first"\nsample.message: "second"',
        "sample.message: [",
    ],
)
def test_invalid_catalog_data_has_resource_diagnostics(catalogs, document):
    (catalogs / "zh-TW.yaml").write_text(document)
    with pytest.raises(runtime_locales.LocaleCatalogError) as rejected:
        runtime_locales.load_catalogs()
    assert "zh-TW.yaml" in str(rejected.value)


@pytest.mark.parametrize("document", ['other.message: "{value}"', 'sample.message: "{other}"'])
def test_catalog_keys_and_placeholder_contracts_must_match(catalogs, document):
    (catalogs / "zh-TW.yaml").write_text(document)
    with pytest.raises(runtime_locales.LocaleCatalogError) as rejected:
        runtime_locales.load_catalogs()
    assert "zh-TW.yaml" in str(rejected.value)
    assert "sample.message" in str(rejected.value)


def test_unreadable_catalog_is_an_error_instead_of_locale_fallback(catalogs):
    (catalogs / "zh-TW.yaml").unlink()
    with pytest.raises(runtime_locales.LocaleCatalogError, match="zh-TW.yaml"):
        runtime_locales.render_text("sample.message", value="test")


def test_invalid_resource_encoding_has_resource_diagnostics(catalogs):
    (catalogs / "zh-TW.yaml").write_bytes(b"\xff")
    with pytest.raises(runtime_locales.LocaleCatalogError, match="zh-TW.yaml"):
        runtime_locales.load_catalogs()


@pytest.mark.parametrize("value", [None, [], object()])
def test_interpolation_requires_plain_scalar_values(catalogs, value):
    with pytest.raises(runtime_locales.LocaleCatalogError, match="sample.message"):
        runtime_locales.render_text("sample.message", value=value)


@pytest.mark.parametrize(
    "key,values",
    [
        ("unknown", {"value": 1}),
        ("sample.message", {}),
        ("sample.message", {"value": 1, "extra": 2}),
    ],
)
def test_unknown_keys_and_incorrect_arguments_have_message_diagnostics(catalogs, key, values):
    with pytest.raises(runtime_locales.LocaleCatalogError) as rejected:
        runtime_locales.render_text(key, **values)
    assert key in str(rejected.value)

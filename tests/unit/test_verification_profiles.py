"""U1/U5: repository profiles and explicit adoption preserve mandatory coverage."""

import json

import pytest

from cafe.verification import VerificationReceiptError
from cafe.verification.profiles import load_profile
from cafe.verification.project import resolve_profile_request


def manifest():
    return {
        "schema_version": 1,
        "profiles": {"custom": {"mandatory": ["bootstrap", "logout"], "optional": ["extra"]}},
        "suites": {
            name: {
                "argv": ["python3", "-c", "print('ok')"],
                "reuse": True,
                "context": {"files": [], "env": [], "probes": []},
            }
            for name in ["bootstrap", "logout", "extra"]
        },
    }


def write_manifest(tmp_path, data=None):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data or manifest()))
    return path


def test_selection_keeps_mandatory_and_adds_only_declared_extras(tmp_path):
    path = write_manifest(tmp_path)
    request = load_profile(tmp_path, path, "custom", ["extra"])
    assert request.selected == ("bootstrap", "logout", "extra")
    assert request.mandatory == ("bootstrap", "logout")
    with pytest.raises(VerificationReceiptError):
        load_profile(tmp_path, path, "custom", ["unknown"])
    with pytest.raises(VerificationReceiptError):
        load_profile(tmp_path, path, "custom", ["extra", "extra"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["profiles"]["custom"]["mandatory"].append("bootstrap"),
        lambda d: d["profiles"]["custom"]["mandatory"].append("missing"),
        lambda d: d["suites"]["bootstrap"].update(argv="python3"),
        lambda d: d["suites"]["bootstrap"].update(context={"files": ["../escape"]}),
        lambda d: d["suites"]["bootstrap"].update(reuse="yes"),
        lambda d: d["suites"]["bootstrap"].update(context={"env": ["BAD=NAME"]}),
    ],
)
def test_ambiguous_or_unsafe_definitions_fail(tmp_path, mutate):
    data = manifest()
    mutate(data)
    with pytest.raises(VerificationReceiptError):
        load_profile(tmp_path, write_manifest(tmp_path, data), "custom")


def test_duplicate_json_ids_and_path_escape_fail(tmp_path):
    path = write_manifest(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(VerificationReceiptError):
        load_profile(tmp_path, path, "custom")
    with pytest.raises(VerificationReceiptError):
        load_profile(tmp_path, tmp_path.parent / "elsewhere.json", "custom")


def test_project_binding_ignores_issue_overrides_and_explicit_binding_wins(tmp_path):
    path = write_manifest(tmp_path)
    assert resolve_profile_request(tmp_path) is None
    config = tmp_path / ".cafe/config.yaml"
    config.parent.mkdir()
    config.write_text("verification:\n  profile_file: profiles.json\n  profile: custom\n")
    issue = tmp_path / ".cafe/issues/custom/config.yaml"
    issue.parent.mkdir(parents=True)
    issue.write_text("verification:\n  profile: weakened\n")
    assert resolve_profile_request(tmp_path).selected == ("bootstrap", "logout")
    config.write_text("verification: broken\n")
    with pytest.raises(VerificationReceiptError):
        resolve_profile_request(tmp_path)
    assert resolve_profile_request(tmp_path, path, "custom").mandatory == ("bootstrap", "logout")
    with pytest.raises(VerificationReceiptError):
        resolve_profile_request(tmp_path, path, None)

"""U1/U2/U8: issue policy validation and targeted settings persistence."""

import pytest
import yaml

from cafe.core.restart_policy import resolve_restart_policy, restart_eligible
from cafe.settings import SettingUpdateRequest, dispatch_setting_update


@pytest.mark.parametrize("value", [None, False, 1, [], {}, "unknown", "RECHECK_PRIORITY"])
def test_invalid_explicit_policy_has_no_side_effects(tmp_path, value):
    path = tmp_path / "issue.yaml"
    path.write_text("unrelated: preserved\n")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        resolve_restart_policy({"execution": {"rate_limit_restart_policy": value}})
    with pytest.raises(ValueError):
        dispatch_setting_update("execution.rate_limit_restart_policy", SettingUpdateRequest(path, value))
    assert path.read_bytes() == before
    assert not path.with_name("issue-settings.lock").exists()


def test_absent_policy_defaults_without_mutation():
    config = {"execution": {"other": True}}
    assert resolve_restart_policy(config) == "continue_last_success"
    assert config == {"execution": {"other": True}}


@pytest.mark.parametrize("reason,new_invocation,expected", [
    ("agent_rate_limit", True, True), ("agent_rate_limit", False, False),
    ("agent_error", True, False), ("agent_timeout", True, False),
    ("rate_limit", True, False), (None, True, False),
])
def test_only_current_typed_rate_limit_new_invocation_is_eligible(reason, new_invocation, expected):
    assert restart_eligible(reason, new_invocation=new_invocation) is expected


@pytest.mark.parametrize("value", ["continue_last_success", "recheck_priority"])
def test_settings_preview_save_and_noop_preserve_unrelated_state(tmp_path, monkeypatch, value):
    path = tmp_path / ".cafe" / "issues" / "custom" / "issue.yaml"
    path.parent.mkdir(parents=True)
    original = {"execution": {"other": True}, "manager": {"opaque": "retain"}, "pr": {"auto_create": False}}
    path.write_text(yaml.safe_dump(original))
    monkeypatch.setattr("cafe.utils.issue_config._registered_worktree_paths", lambda _root: (tmp_path,))
    before = path.read_bytes()
    preview = dispatch_setting_update("execution.rate_limit_restart_policy", SettingUpdateRequest(path, value, True))
    assert preview.status == "proposed"
    assert path.read_bytes() == before
    assert not path.with_name("issue-settings.lock").exists()
    saved = dispatch_setting_update("execution.rate_limit_restart_policy", SettingUpdateRequest(path, value))
    assert saved.status == "saved"
    actual = yaml.safe_load(path.read_text())
    assert actual == {**original, "execution": {"other": True, "rate_limit_restart_policy": value}}
    assert resolve_restart_policy(actual) == value
    before = path.read_bytes()
    assert dispatch_setting_update("execution.rate_limit_restart_policy", SettingUpdateRequest(path, value)).status == "unchanged"
    assert path.read_bytes() == before

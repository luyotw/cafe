"""Issue #408 U1/U4: entry ownership, strict shape, and immutable identity."""

import pytest
import yaml

from cafe.core.restart_policy import InvocationOrder
from cafe.core.types import AgentCLI, AgentConfig, CliEntry
from cafe.utils.phase_config import load_phase_step_model


def write_phase(path, entries):
    path.write_text(yaml.safe_dump({"custom_build": {"name": "David", "clis": entries}}))


@pytest.mark.parametrize("value", [None, "", " ", True, 2, "high\n", "high\x00", "HIGH"])
def test_invalid_explicit_effort_names_source_and_entry(tmp_path, value):
    path = tmp_path / "phases.yaml"
    write_phase(path, [{"cli": "codex", "model": "gpt-5.5", "reasoning_effort": value}])
    with pytest.raises(ValueError) as error:
        load_phase_step_model(step_name="custom_build", local_path=path)
    assert str(path) in str(error.value)
    assert "custom_build.clis[0].reasoning_effort" in str(error.value)


@pytest.mark.parametrize("cli,model", [("gemini", "gemini-3-pro"), ("cursor-agent", "auto")])
@pytest.mark.parametrize("entry_index", [0, 1])
def test_unverified_effort_control_rejection_identifies_configured_tuple(
    tmp_path, cli, model, entry_index
):
    path = tmp_path / "phases.yaml"
    entries = [{"cli": "codex", "model": "legacy"}] * entry_index
    entries.append({"cli": cli, "model": model, "reasoning_effort": "high"})
    write_phase(path, entries)

    with pytest.raises(ValueError) as error:
        load_phase_step_model(step_name="custom_build", local_path=path)

    message = str(error.value)
    assert str(path) in message
    assert f"custom_build.clis[{entry_index}].reasoning_effort" in message
    assert cli in message and model in message
    assert any(action in message.lower() for action in ("omit", "remove", "select", "choose"))


@pytest.mark.parametrize("cli,model", [("gemini", "gemini-3-pro"), ("cursor-agent", "auto")])
def test_unverified_effort_control_preserves_legacy_omission(tmp_path, cli, model):
    path = tmp_path / "phases.yaml"
    write_phase(path, [{"cli": cli, "model": model}])

    result = load_phase_step_model(step_name="custom_build", local_path=path)

    assert result.clis[0].model == model
    assert result.clis[0].reasoning_effort is None


def test_local_chain_omission_does_not_inherit_repository_effort(tmp_path, monkeypatch):
    from tests.unit.test_reasoning_effort_adapters import install_provider_clients

    install_provider_clients(tmp_path, monkeypatch)
    local, repo = tmp_path / "local.yaml", tmp_path / "repo.yaml"
    write_phase(repo, [{"cli": "claude", "model": "claude-opus-4-6", "reasoning_effort": "high"}])
    write_phase(local, [{"cli": "claude", "model": "claude-opus-4-6"}])
    result = load_phase_step_model(step_name="custom_build", local_path=local, repo_path=repo)
    assert result.clis[0].reasoning_effort is None
    assert result.clis_source == "worktree"


def test_entry_config_and_invocation_preserve_independent_effort():
    entries = [
        CliEntry(cli=AgentCLI.CODEX, model="gpt-5.5", reasoning_effort="high"),
        CliEntry(cli=AgentCLI.CLAUDE, model="claude-opus-4-6", reasoning_effort="low"),
    ]
    config = AgentConfig(
        name="David",
        cli=entries[0].cli,
        model=entries[0].model,
        reasoning_effort="high",
        clis=entries,
    )
    assert config.model_copy(deep=True).clis[1].reasoning_effort == "low"
    order = InvocationOrder(entries=tuple(entry.model_dump() for entry in entries))
    assert order.entries[0].reasoning_effort == "high"
    with pytest.raises((AttributeError, TypeError)):
        order.entries[0].reasoning_effort = "low"


@pytest.mark.parametrize("model", [[], {}, 5, True])
def test_invalid_model_with_effort_is_source_qualified_before_capability_lookup(tmp_path, model):
    path = tmp_path / "phases.yaml"
    write_phase(path, [{"cli": "codex", "model": model, "reasoning_effort": "high"}])
    with pytest.raises(ValueError) as error:
        load_phase_step_model(step_name="custom_build", local_path=path)
    assert "custom_build.clis[0].model" in str(error.value)
    assert str(path) in str(error.value)


def test_legacy_resolution_constructor_normalizes_omission():
    from cafe.utils.phase_config import PhaseStepModelResolution

    result = PhaseStepModelResolution(
        name=None, role=None, clis=(("codex", "legacy"),), model="legacy", source="test", chain=()
    )
    assert result.clis[0].as_dict() == {"cli": "codex", "model": "legacy"}

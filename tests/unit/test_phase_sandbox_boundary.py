from types import SimpleNamespace

from cafe.core.phase_sandbox_mixin import PhaseSandboxMixin


def test_permission_retry_never_executes_raw_host_command() -> None:
    denial = SimpleNamespace(tool_name="Bash", tool_input={"command": "touch /host-owned"})
    note = PhaseSandboxMixin._execute_approved_sandbox_commands_on_host(object(), [denial], [0])
    assert "inside the agent sandbox" in note
    assert not hasattr(denial, "returncode")

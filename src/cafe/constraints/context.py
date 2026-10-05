"""Neutral capability derivation from actual allowed tools, never step names."""

from .resolver import execution_context


def context_for_tools(
    cli="codex",
    *,
    allowed_tools=None,
    workloads=(),
    capabilities=(),
    structured=False,
    consumers=(),
    surface="phase",
    operation="managed",
):
    tags = set(capabilities)
    if allowed_tools is None or any(
        str(tool).lower().split("(", 1)[0]
        in {"bash", "shell", "execute", "exec", "terminal", "command"}
        for tool in allowed_tools
    ):
        tags.add("execute")
    if structured:
        tags.add("structured-output")
    return execution_context(
        cli,
        capabilities=sorted(tags),
        workloads=list(workloads),
        consumers=sorted(set(consumers)),
        surface=surface,
        operation=operation,
    )

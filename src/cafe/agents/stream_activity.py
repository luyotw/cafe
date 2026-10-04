"""Provider-neutral activity observed independently of stdout or completion."""

from types import TracebackType
from typing import Any, Mapping, Protocol


class StreamActivity(Protocol):
    """One invocation's resource lifetime and verified stream metadata."""

    def __enter__(self) -> "StreamActivity": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def command(self, cmd: list[str], environment: Mapping[str, str]) -> list[str]: ...

    def bind(self, session: str) -> None: ...

    def drain(self) -> dict[str, Any] | None: ...

    def diagnostics(self) -> dict[str, Any]: ...

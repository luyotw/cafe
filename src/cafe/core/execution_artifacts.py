"""Common producer and consumer capacity for resolved execution artifacts."""

import json
from pathlib import Path
from cafe.core.packet_io import canonical_json

MAX_EXECUTION_ARTIFACT_BYTES = 256 * 1024


def bounded_execution_json(value) -> bytes:
    content = canonical_json(value)
    if len(content) > MAX_EXECUTION_ARTIFACT_BYTES:
        raise ValueError("execution artifact exceeds durable reader capacity")
    return content


def load_execution_artifact(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("execution artifact must be a bounded regular file")
    with path.open("rb") as handle:
        content = handle.read(MAX_EXECUTION_ARTIFACT_BYTES + 1)
    if len(content) > MAX_EXECUTION_ARTIFACT_BYTES:
        raise ValueError("execution artifact exceeds durable reader capacity")
    return json.loads(content)

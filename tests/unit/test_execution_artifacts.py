"""U6/U7: execution producers and consumers share the same byte capacity."""

from pathlib import Path

import pytest

from cafe.core.execution_artifacts import bounded_execution_json, load_execution_artifact


@pytest.mark.parametrize("excess", [0, 1])
def test_execution_artifact_byte_limit_roundtrip(tmp_path: Path, excess: int):
    empty = len(bounded_execution_json({"result": ""}))
    value = {"result": "é" * ((256 * 1024 - empty) // 2)}
    capacity_left = 256 * 1024 - len(bounded_execution_json(value))
    value["result"] += "x" * (capacity_left + excess)
    path = tmp_path / "review.json"
    if excess:
        with pytest.raises(ValueError):
            bounded_execution_json(value)
        from cafe.core.packet_io import canonical_json
        path.write_bytes(canonical_json(value))
        with pytest.raises(ValueError):
            load_execution_artifact(path)
    else:
        path.write_bytes(bounded_execution_json(value))
        assert path.stat().st_size == 256 * 1024
        assert load_execution_artifact(path) == value

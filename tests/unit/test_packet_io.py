import json
from pathlib import Path

import pytest

from cafe.core.packet_io import load_or_persist_json


def test_packet_io_enforces_caller_schema_identity_and_trusted_bytes(tmp_path: Path) -> None:
    path = tmp_path / "packet.json"
    packet = {"schema": "caller-owned", "id": "one"}
    validated: list[dict[str, str]] = []

    def validate(value: object) -> None:
        assert isinstance(value, dict)
        assert value["schema"] == "caller-owned"
        validated.append(value)

    persisted, metadata = load_or_persist_json(
        path,
        packet,
        validate=validate,
        matches_identity=lambda old, new: old["id"] == new["id"],
    )
    assert persisted == packet
    assert metadata["sha256"]

    with pytest.raises(ValueError, match="hash mismatch"):
        load_or_persist_json(
            path,
            packet,
            validate=validate,
            matches_identity=lambda old, new: old["id"] == new["id"],
            expected_sha256="0" * 64,
        )
    assert validated


def test_packet_io_reuses_caller_validation_for_persisted_json(tmp_path: Path) -> None:
    path = tmp_path / "packet.json"
    path.write_text(json.dumps({"schema": "wrong", "id": "one"}), encoding="utf-8")

    with pytest.raises(AssertionError):
        load_or_persist_json(
            path,
            {"schema": "caller-owned", "id": "one"},
            validate=lambda value: (
                (_ for _ in ()).throw(AssertionError("caller schema"))
                if value.get("schema") != "caller-owned"
                else None
            ),
            matches_identity=lambda old, new: old["id"] == new["id"],
        )

"""CLI journey coverage for Context Packets status projection."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cafe.core.context_packet import resolve_context_packet
from cafe.services.summary_service import SummaryService
from cafe.ui.cli import app

runner = CliRunner()


def _spec() -> str:
    return """# Source

GOAL-001 NONGOAL-001 AC-001 INV-001 TRUST-001

## Downstream Contract

- Contract-Version: `1`
- Artifact-Kind: `spec`

### Goals
| ID | Statement |
| --- | --- |
| GOAL-001 | Goal |
### Non-Goals
| ID | Statement |
| --- | --- |
| NONGOAL-001 | No |
### Acceptance Criteria
| ID | Priority | Statement |
| --- | --- | --- |
| AC-001 | must | Yes |
### Invariants
| ID | Statement |
| --- | --- |
| INV-001 | Safe |
### Trust Boundaries
| ID | Statement |
| --- | --- |
| TRUST-001 | Local |
"""


def test_cafe_status_shows_verified_and_fallback_context_packets(
    tmp_path: Path, monkeypatch
) -> None:
    """IT-003: the public command renders one verified and one safe fallback journey."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe/issues/demo"
    source = tmp_path / "spec.md"
    source.write_text(_spec(), encoding="utf-8")
    verified_dir = issue_dir / "develop/iteration_001"
    verified = resolve_context_packet(
        source_path=source,
        contract_kind="spec",
        target_step="develop",
        iteration=1,
        placeholders=("spec_file", "spec_file_path"),
        packet_path=verified_dir / "context_spec_file.json",
    )
    verified_binding = {
        "requested_mode": "packet",
        "mode": verified["mode"],
        "path": verified["path"],
        "reason": "",
        "fallback_reason": "",
        "detail": "",
        "source": verified["source"],
    }
    (verified_dir / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": "agent value",
                "effective_inputs": {
                    "spec_file": verified_binding,
                    "spec_file_path": verified_binding,
                },
            }
        ),
        encoding="utf-8",
    )
    fallback_dir = issue_dir / "review/iteration_002"
    fallback_dir.mkdir(parents=True)
    fallback_binding = {
        "requested_mode": "packet",
        "mode": "full_fallback",
        "path": str(source),
        "source": {"artifact_name": "spec", "artifact_version": 1},
        "reason": "packet_invalid",
        "fallback_reason": "packet_invalid",
        "detail": "context packet validation failed",
    }
    (fallback_dir / "iteration.json").write_text(
        json.dumps(
            {"iteration": {"agent": "value"}, "effective_inputs": {"spec": fallback_binding}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(SummaryService, "get_current_issue", lambda self: "demo")
    monkeypatch.setattr(
        "cafe.ui.commands.workflow._load_issue_step_names", lambda _issue: ["develop", "review"]
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.stdout
    assert "Context Packets" in result.stdout
    assert "packet" in result.stdout
    assert "full_fallback:packet_invalid" in result.stdout

"""Native-first review engine selection and fallback contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cafe.core.types import AgentCLI
from cafe.skills.review_engines import (
    ReviewEngineService,
    native_review_capability_rows,
    review_engine_prompt_context,
)


class RecordingRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        return self.results.pop(0)


def completed(command: list[str], *, stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)


def test_codex_native_review_produces_bounded_candidate_evidence(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            completed(["codex"], stdout="Run a code review non-interactively"),
            completed(["codex"], stdout="P1 src/example.py:9 definite bug"),
        ]
    )
    service = ReviewEngineService(runner=runner, home_dir=tmp_path / "home")
    evidence = tmp_path / "iteration" / "review_engine.md"

    result = service.prepare(
        cli=AgentCLI.CODEX,
        project_root=tmp_path,
        base_branch="develop",
        model="gpt-review",
        evidence_file=evidence,
        fallback_invocation="$cafe-review-fallback",
    )

    assert result.mode == "native_command"
    assert result.engine_id == "codex-review"
    assert result.evidence_file == evidence
    assert result.telemetry is not None
    assert result.telemetry.outcome == "completed"
    assert result.telemetry.tokens_metered is False
    assert "candidate findings" in result.guidance
    saved = evidence.read_text(encoding="utf-8")
    assert "P1 src/example.py:9 definite bug" in saved
    assert "Authority: candidate findings only" in saved
    command = runner.calls[1][0]
    assert command[:5] == ["codex", "-C", str(tmp_path), "-a", "never"]
    assert command[5:7] == ["--model", "gpt-review"]
    assert command[7:10] == ["review", "--base", "develop"]


def test_failed_native_execution_explicitly_selects_fallback(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            completed(["claude"], stdout="cloud-hosted multi-agent code review"),
            completed(["claude"], stderr="service unavailable", code=1),
        ]
    )
    service = ReviewEngineService(runner=runner, home_dir=tmp_path / "home")

    result = service.prepare(
        cli=AgentCLI.CLAUDE,
        project_root=tmp_path,
        base_branch="develop",
        model=None,
        evidence_file=tmp_path / "review_engine.md",
        fallback_invocation="/cafe-review-fallback",
    )

    assert result.mode == "fallback_skill"
    assert result.engine_id == "anthropic-pr-review-toolkit"
    assert "service unavailable" in (result.fallback_reason or "")
    assert "/cafe-review-fallback" in result.guidance
    assert result.telemetry is not None
    assert result.telemetry.outcome == "failed"
    assert result.telemetry.cost_metered is False
    assert not (tmp_path / "review_engine.md").exists()


def test_claude_native_review_rejects_incompatible_success_payload(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            completed(["claude"], stdout="cloud-hosted multi-agent code review"),
            completed(["claude"], stdout="job accepted but no JSON payload"),
        ]
    )
    service = ReviewEngineService(runner=runner, home_dir=tmp_path / "home")

    result = service.prepare(
        cli=AgentCLI.CLAUDE,
        project_root=tmp_path,
        base_branch="develop",
        model=None,
        evidence_file=tmp_path / "review_engine.md",
        fallback_invocation="/cafe-review-fallback",
    )

    assert result.mode == "fallback_skill"
    assert "did not return JSON" in (result.fallback_reason or "")


def test_native_review_decode_failure_selects_fallback(tmp_path: Path) -> None:
    calls = 0

    def runner(command, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        if calls == 1:
            return completed(command, stdout="Run a code review non-interactively")
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")

    service = ReviewEngineService(runner=runner, home_dir=tmp_path / "home")

    result = service.prepare(
        cli=AgentCLI.CODEX,
        project_root=tmp_path,
        base_branch="main",
        model=None,
        evidence_file=tmp_path / "review_engine.md",
        fallback_invocation="$cafe-review-fallback",
    )

    assert result.mode == "fallback_skill"
    assert "failed" in (result.fallback_reason or "")
    assert result.telemetry is not None
    assert result.telemetry.outcome == "failed"


def test_cli_without_native_metadata_selects_fallback_without_probe(tmp_path: Path) -> None:
    runner = RecordingRunner([])
    service = ReviewEngineService(runner=runner, home_dir=tmp_path / "home")

    result = service.prepare(
        cli=AgentCLI.GEMINI,
        project_root=tmp_path,
        base_branch="main",
        model=None,
        evidence_file=tmp_path / "review_engine.md",
        fallback_invocation="/cafe-review-fallback",
    )

    assert result.mode == "fallback_skill"
    assert "no compatible native review capability" in (result.fallback_reason or "")
    assert runner.calls == []


def test_cursor_uses_fallback_until_native_skill_has_a_completion_receipt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    skill_file = home / ".cursor/skills-cursor/review-bugbot/SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# review\n", encoding="utf-8")
    service = ReviewEngineService(runner=RecordingRunner([]), home_dir=home)

    result = service.prepare(
        cli=AgentCLI.CURSOR,
        project_root=tmp_path,
        base_branch="main",
        model=None,
        evidence_file=tmp_path / "review_engine.md",
        fallback_invocation="/cafe-review-fallback",
    )

    assert result.mode == "fallback_skill"
    assert result.engine_id == "anthropic-pr-review-toolkit"
    assert "no compatible native review capability" in (result.fallback_reason or "")
    assert "/cafe-review-fallback" in result.guidance


def test_review_engine_prompt_context_preserves_selection_reason(tmp_path: Path) -> None:
    service = ReviewEngineService(runner=RecordingRunner([]), home_dir=tmp_path / "home")
    selection = service.prepare(
        cli=AgentCLI.COPILOT,
        project_root=tmp_path,
        base_branch="main",
        model=None,
        evidence_file=tmp_path / "review_engine.md",
        fallback_invocation="/cafe-review-fallback",
    )

    context = review_engine_prompt_context(selection)

    assert context["review_engine_id"] == "anthropic-pr-review-toolkit"
    assert context["review_engine_mode"] == "fallback_skill"
    assert "copilot" in context["review_engine_fallback_reason"]


def test_native_review_capability_registry_is_explicit_and_bounded() -> None:
    rows = native_review_capability_rows()

    assert {row.cli for row in rows} == {
        AgentCLI.CLAUDE,
        AgentCLI.CODEX,
    }
    assert all(row.mode == "native_command" for row in rows)

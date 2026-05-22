"""Tests for PhaseReviewMixin questions XML validation and retry."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.phase_review_mixin import PhaseReviewMixin


class _StubPhase(PhaseReviewMixin):
    """Minimal object exposing mixin methods under test."""

    def __init__(self, agent_manager: MagicMock) -> None:
        self.agent_manager = agent_manager


VALID_QUESTIONS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>What is the expected error behavior?</title>
    <options>
      <option>Return error code</option>
      <option>Throw exception</option>
    </options>
  </question>
</questions>
"""

INVALID_QUESTIONS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Missing options question</title>
  </question>
</questions>
"""


@pytest.fixture
def stub_phase() -> _StubPhase:
    return _StubPhase(agent_manager=MagicMock())


class TestValidateAndRetryQuestionsXml:
    """Tests for _validate_and_retry_questions_xml()."""

    def test_returns_true_when_xml_is_valid(self, stub_phase: _StubPhase, tmp_path: Path) -> None:
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_XML, encoding="utf-8")

        result = stub_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is True

    def test_returns_false_when_xml_not_exists(self, stub_phase: _StubPhase, tmp_path: Path) -> None:
        xml_path = tmp_path / "questions.xml"

        result = stub_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is False

    def test_retries_on_invalid_xml_and_succeeds(self, stub_phase: _StubPhase, tmp_path: Path) -> None:
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML, encoding="utf-8")
        call_count = 0

        def mock_execute(agent_name: str, prompt: str, **kwargs: object) -> tuple:
            nonlocal call_count
            call_count += 1
            xml_path.write_text(VALID_QUESTIONS_XML, encoding="utf-8")
            return ("Fixed", MagicMock(), [], None, [], None)

        stub_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        result = stub_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is True
        assert call_count == 1

    def test_retries_up_to_3_times_then_returns_false(self, stub_phase: _StubPhase, tmp_path: Path) -> None:
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML, encoding="utf-8")
        call_count = 0

        def mock_execute(agent_name: str, prompt: str, **kwargs: object) -> tuple:
            nonlocal call_count
            call_count += 1
            return ("Still broken", MagicMock(), [], None, [], None)

        stub_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        result = stub_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is False
        assert call_count == 3
        assert not xml_path.exists()

    def test_retry_prompt_includes_xml_path(self, stub_phase: _StubPhase, tmp_path: Path) -> None:
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML, encoding="utf-8")
        captured_prompts: list[str] = []

        def mock_execute(agent_name: str, prompt: str, **kwargs: object) -> tuple:
            captured_prompts.append(prompt)
            xml_path.write_text(VALID_QUESTIONS_XML, encoding="utf-8")
            return ("Fixed", MagicMock(), [], None, [], None)

        stub_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        stub_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert len(captured_prompts) == 1
        assert str(xml_path) in captured_prompts[0]

    def test_agent_exception_during_retry_continues(self, stub_phase: _StubPhase, tmp_path: Path) -> None:
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML, encoding="utf-8")
        call_count = 0

        def mock_execute(agent_name: str, prompt: str, **kwargs: object) -> tuple:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Agent error")
            if call_count == 2:
                xml_path.write_text(VALID_QUESTIONS_XML, encoding="utf-8")
                return ("Fixed", MagicMock(), [], None, [], None)
            return ("Still broken", MagicMock(), [], None, [], None)

        stub_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        result = stub_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is True
        assert call_count == 2

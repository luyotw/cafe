"""Generic phase for skill-driven execution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cafe.core.questions_schema import validate_questions_xml
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser
from cafe.skills.loader import SkillLoader


class GenericPhase:
    """Build prompts from skill content and validate phase responses."""

    GOTO_PATTERN = re.compile(r"CAFE_GOTO\s*:\s*([a-zA-Z0-9_-]+)")

    def __init__(self, skill_loader: SkillLoader) -> None:
        self.skill_loader = skill_loader

    def build_prompt(
        self,
        *,
        skill_name: str,
        context: Optional[Dict[str, str]] = None,
        output_file: Optional[Path] = None,
        checklist_file: Optional[Path] = None,
        questions_xml_file: Optional[Path] = None,
    ) -> str:
        skill_body = self.skill_loader.activate(skill_name, context=context)
        lines = [skill_body.strip(), ""]

        if output_file is not None:
            lines.append(f"Write output to: {output_file}")
        if checklist_file is not None:
            lines.append(f"Mark checklist items in: {checklist_file}")
            lines.append("Do NOT return a status code until ALL checklist items are marked as [x].")
        if questions_xml_file is not None:
            lines.append(f"If clarification is needed, write questions.xml to: {questions_xml_file}")

        return "\n".join(lines).strip()

    @classmethod
    def extract_goto_target(cls, response: str) -> Optional[str]:
        match = cls.GOTO_PATTERN.search(response)
        if not match:
            return None
        return match.group(1)

    def parse_response(
        self,
        *,
        response: str,
        valid_status_codes: List[PhaseStatusCode],
    ) -> Tuple[Optional[PhaseStatusCode], Optional[str]]:
        status = StatusCodeParser.extract(response, valid_codes=valid_status_codes)
        goto_target = self.extract_goto_target(response)
        return status, goto_target

    def validate_clarification_output(
        self,
        *,
        status_code: Optional[PhaseStatusCode],
        questions_xml_file: Path,
    ) -> None:
        if status_code != PhaseStatusCode.NEED_CLARIFICATION:
            return
        if not questions_xml_file.exists():
            raise ValueError(
                f"Status is {PhaseStatusCode.NEED_CLARIFICATION.value} but questions.xml is missing: {questions_xml_file}"
            )
        if not validate_questions_xml(questions_xml_file):
            raise ValueError(f"questions.xml format is invalid: {questions_xml_file}")

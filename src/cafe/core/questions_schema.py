"""XML question schema for spec phase interactive Q&A.

Provides parsing and validation for structured clarification questions
produced by the PM agent during the spec phase.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Question:
    """A clarification question with suggested options."""

    id: str
    title: str
    options: list[str]
    multi_select: bool = False


def parse_questions_xml(xml_path: Path) -> list[Question]:
    """Parse questions XML file and return list of Question objects.

    Args:
        xml_path: Path to the questions.xml file

    Returns:
        List of Question objects

    Raises:
        FileNotFoundError: If the file does not exist
        xml.etree.ElementTree.ParseError: If the XML is malformed
    """
    if not xml_path.exists():
        raise FileNotFoundError(f"Questions file not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    questions = []
    for q_elem in root.findall("question"):
        q_id = q_elem.get("id", "")
        q_type = q_elem.get("type", "")
        multi_select = q_type == "checkbox"
        title_elem = q_elem.find("title")
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

        options = []
        options_elem = q_elem.find("options")
        if options_elem is not None:
            for opt_elem in options_elem.findall("option"):
                if opt_elem.text and opt_elem.text.strip():
                    options.append(opt_elem.text.strip())

        questions.append(Question(id=q_id, title=title, options=options, multi_select=multi_select))

    return questions


def validate_questions_xml(xml_path: Path) -> bool:
    """Validate that a questions XML file has correct format.

    Checks:
    - File exists and is parseable XML
    - Root tag is <questions>
    - At least one <question> element
    - Each <question> has a non-empty <title>
    - Each <question> has <options> with at least one non-empty <option>

    Args:
        xml_path: Path to the questions.xml file

    Returns:
        True if valid, False otherwise
    """
    try:
        if not xml_path.exists():
            return False

        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Root must be <questions>
        if root.tag != "questions":
            return False

        # Must have at least one <question>
        question_elems = root.findall("question")
        if not question_elems:
            return False

        for q_elem in question_elems:
            # Each question must have a non-empty <title>
            title_elem = q_elem.find("title")
            if title_elem is None or not title_elem.text or not title_elem.text.strip():
                return False

            # Each question must have <options> with at least one non-empty <option>
            options_elem = q_elem.find("options")
            if options_elem is None:
                return False

            valid_options = [
                opt for opt in options_elem.findall("option")
                if opt.text and opt.text.strip()
            ]
            if not valid_options:
                return False

        return True

    except (ET.ParseError, Exception):
        return False

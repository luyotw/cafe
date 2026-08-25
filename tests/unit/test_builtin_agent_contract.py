"""Contract tests for bundled CAFE agent role files."""

from pathlib import Path

import yaml

from cafe.utils.prompt_utils import extract_agent_guidelines_checklist

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTS_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "agents"


def _agent_files() -> list[Path]:
    return sorted(AGENTS_ROOT.glob("*/*.md"))


def _frontmatter(path: Path) -> dict[str, object]:
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _, raw, _body = content.split("---", 2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict), f"{path} frontmatter must be a mapping"
    return parsed


def test_every_builtin_agent_has_matching_identity_and_guidelines() -> None:
    files = _agent_files()
    assert files

    for path in files:
        metadata = _frontmatter(path)
        content = path.read_text(encoding="utf-8")
        body = content.split("---", 2)[2].strip()
        bullet_lines = [line for line in body.splitlines() if line.lstrip().startswith("- ")]

        assert metadata.get("name") == path.stem, f"{path} name must match its filename"
        assert str(metadata.get("description", "")).strip(), f"{path} needs a description"
        assert body, f"{path} needs a role statement"
        assert bullet_lines, f"{path} needs checklist-producing guidelines"
        assert all(line.startswith("- ") for line in bullet_lines), (
            f"{path} guidelines must be a flat list because indented bullets are also extracted"
        )

        checklist = extract_agent_guidelines_checklist(str(path))
        assert "## Agent Guidelines Checklist" in checklist
        assert checklist.count("[ ] ") == len(bullet_lines)


def test_traditional_chinese_builtin_agents_declare_native_language() -> None:
    for path in _agent_files():
        content = path.read_text(encoding="utf-8")
        body = content.split("---", 2)[2]
        if any("\u4e00" <= character <= "\u9fff" for character in body):
            description = str(_frontmatter(path).get("description", ""))
            assert "母語為繁體中文" in description, f"{path} must declare its native language"

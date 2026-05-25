"""PR title/body parsing helpers for workflow PR publish paths."""


def parse_pr_title(content: str) -> str:
    """Parse PR title from output.md content (first H1 heading)."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    raise ValueError("No H1 heading found in output.md")


def parse_pr_body(content: str) -> str:
    """Parse PR body from output.md (all content after first H1)."""
    lines = content.split("\n")
    h1_found = False
    body_lines = []
    for line in lines:
        if not h1_found:
            if line.strip().startswith("# "):
                h1_found = True
            continue
        body_lines.append(line)
    return "\n".join(body_lines).strip()

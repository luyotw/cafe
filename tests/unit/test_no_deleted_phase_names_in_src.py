"""Guardrail: deleted per-phase Python class names must not reappear in src/."""

from pathlib import Path

DELETED_PHASE_NAMES = (
    "SpecPhase",
    "PlanPhase",
    "DevelopPhase",
    "ReviewPhase",
    "PRPhase",
)


def test_src_has_no_deleted_phase_class_names() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    hits: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in DELETED_PHASE_NAMES:
            if name in text:
                hits.append(f"{path.relative_to(repo_root)}: {name}")
    assert hits == [], "Remove deleted phase class names from production source:\n" + "\n".join(hits)

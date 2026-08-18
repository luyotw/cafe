"""Policy tests for routing cafe chat implementation requests."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_SKILL = (
    PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "cafe-chat-develop-change" / "SKILL.md"
)


def test_chat_develop_routes_product_and_trust_boundary_changes_to_full_workflow() -> None:
    content = CHAT_SKILL.read_text(encoding="utf-8")

    assert "Classify any implementation change requested inside cafe chat" in content
    assert "any implementation or code change" in content
    assert "may introduce broad" in content
    assert "Classify scope before editing code" in content
    assert "new product capability" in content
    assert "authentication, authorization, privacy, security" in content
    assert "database schema change" in content
    assert "deployment, infrastructure, paid-service" in content
    assert "leave source files unchanged" in content
    assert "When uncertain, prefer the full workflow" in content
    assert "earliest responsible step from the active playbook" in content
    assert "use only a valid step name declared there" in content
    assert "Do not assume generic phase names exist" in content
    assert "route to the built-in `user` step" in content
    assert "Apply `cafe-common-chat-handoff`" in content

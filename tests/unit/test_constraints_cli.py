"""Public read-only inspection and generated docs: U8, I3/I5."""

import json

import pytest
from typer.testing import CliRunner

from cafe.constraints import Context, material_digest, resolve
from cafe.ui.cli import app


def test_catalog_show_and_contextual_json_share_runtime_facts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    catalog = runner.invoke(app, ["constraints", "list", "--json"])
    assert catalog.exit_code == 0, catalog.output
    assert len(json.loads(catalog.stdout)["entries"]) == 9
    result = runner.invoke(app, ["constraints", "list", "--cli", "gemini", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    idle = next(e for e in payload["entries"] if e["id"] == "agent.stdout-idle")
    assert idle["boundary"]["limits"][0]["value"] == 600
    assert payload["digest"] == material_digest(resolve(Context(cli="gemini", provider="google")))
    shown = runner.invoke(
        app, ["constraints", "show", "agent.stdout-idle", "--cli", "gemini", "--json"]
    )
    assert json.loads(shown.stdout)["entries"] == [idle]
    assert not (tmp_path / ".cafe").exists()
    assert runner.invoke(app, ["constraints", "show", "missing", "--json"]).exit_code != 0
    assert runner.invoke(app, ["constraints", "list", "--cli", "invalid", "--json"]).exit_code != 0


def test_document_generation_check_and_package_discovery_outside_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    page = tmp_path / "known.md"
    generated = runner.invoke(app, ["constraints", "docs", "render", "--output", str(page)])
    assert generated.exit_code == 0, generated.output
    assert runner.invoke(app, ["constraints", "docs", "check", str(page)]).exit_code == 0
    page.write_text(page.read_text() + "drift\n")
    assert runner.invoke(app, ["constraints", "docs", "check", str(page)]).exit_code != 0


@pytest.mark.parametrize("grant", [False, True])
def test_issue_step_inspection_uses_custom_declarations_and_is_read_only(
    tmp_path, monkeypatch, grant
):
    monkeypatch.chdir(tmp_path)
    issue = tmp_path / ".cafe/issues/custom"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text("playbook: custom-flow\n")
    playbook = tmp_path / ".cafe/playbooks/custom-flow.yaml"
    playbook.parent.mkdir()
    playbook.write_text(
        "playbook:\n"
        "  id: custom-flow\n"
        "roles:\n"
        "  author: {default_agent: Writer}\n"
        "steps:\n"
        "  scribe:\n"
        "    skill: bespoke\n"
        "    role: author\n"
        "    on: {await_agent: _done}\n"
        ""
    )
    (tmp_path / ".cafe/phases.yaml").write_text(
        "scribe:\n  name: Writer\n  clis:\n    - cli: codex\n      model: fixture\n"
    )
    skill = tmp_path / ".cafe/skills/bespoke/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: bespoke\n"
        "description: fixture\n"
        "workflow:\n"
        "  execution_profile:\n"
        "    workload: content\n"
        "    requested_workloads: [short-docs]\n"
        "---\n"
        "Write docs.\n"
        ""
    )
    if grant:
        skill.write_text(skill.read_text().replace("[short-docs]", "[implementation]"))
        text = playbook.read_text().replace(
            "    role: author\n",
            "    role: author\n    allowed_tools: [Read]\n    behavior:\n"
            "      runtime_tool_grants: [git_inspection]\n",
        )
        playbook.write_text(text)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = CliRunner().invoke(
        app, ["constraints", "list", "--issue", "custom", "--step", "scribe", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert ("agent.stdout-idle" in {e["id"] for e in payload["entries"]}) is grant
    if grant:
        from cafe.constraints.context import context_for_tools
        from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
        from cafe.playbooks.loader import PlaybookLoader

        executor = object.__new__(GenericWorkflowStepExecutor)
        executor.issue_dir = issue
        executor.playbook = PlaybookLoader(project_root=tmp_path, read_only=True).load(
            "custom-flow"
        )
        definition = executor.playbook["steps"]["scribe"]
        tools = executor._build_allowed_tools(
            step_name="scribe",
            step_def=definition,
            output_file=issue / "scribe/iteration_001/output.md",
            checklist_file=issue / "scribe/iteration_001/checklist.md",
            questions_xml_file=issue / "scribe/iteration_001/questions.xml",
        )
        production = resolve(
            context_for_tools(
                "codex",
                allowed_tools=tools,
                workloads=["implementation"],
                structured=True,
                consumers=["authority", "single-chain"],
            )
        )
        assert payload["digest"] == material_digest(production)
        assert {e["id"] for e in payload["entries"]} == {e.id for e in production.entries}
    after = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after

"""I1/I6/I7: public helper authoring journey and transaction invariants."""

from pathlib import Path

from cafe.authoring import apply, decode_request, prepare

FIXTURE = Path(__file__).parents[1] / "fixtures/authoring/pair.yaml"


def test_author_creates_usable_pair_then_repeats_without_changes(tmp_path):
    request = decode_request(FIXTURE.read_text())
    preview = prepare(request, root=tmp_path)
    assert preview.status == "ready", preview.to_dict()
    result = apply(request, root=tmp_path, expect_change=preview.change_digest)
    assert result.status == "applied", result.to_dict()
    assert (tmp_path / request["target"]).is_file()
    assert result.simulation[request["target"]]["unreachable_steps"] == []
    again = apply(request, root=tmp_path)
    assert again.status == "noop", again.to_dict()
    assert again.changes == []


def test_reviewed_digest_rejects_changed_request_without_publication(tmp_path):
    request = decode_request(FIXTURE.read_text())
    preview = prepare(request, root=tmp_path)
    request["companions"][0]["sections"]["Instructions"] += " Verify provenance."
    result = apply(request, root=tmp_path, expect_change=preview.change_digest)
    assert result.status == "rejected"
    assert not (tmp_path / ".cafe").exists()


def test_machine_author_uses_stdin_json_and_reviewed_digest(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from cafe.ui.cli import app

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    text = FIXTURE.read_text()
    preview = runner.invoke(
        app, ["playbook", "author", "--spec", "-", "--dry-run", "--format", "json"], input=text
    )
    assert preview.exit_code == 0, preview.output
    report = json.loads(preview.stdout)
    assert report["version"] == 1
    applied = runner.invoke(
        app,
        [
            "playbook",
            "author",
            "--spec",
            "-",
            "--apply",
            "--format",
            "json",
            "--expect-change",
            report["change_digest"],
        ],
        input=text,
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["status"] == "applied"
    invalid = runner.invoke(
        app,
        ["playbook", "author", "--spec", "-", "--dry-run", "--format", "json"],
        input='{"version":1,"version":1}',
    )
    assert invalid.exit_code == 1
    assert json.loads(invalid.stdout)["diagnostics"]
    usage = runner.invoke(app, ["playbook", "author", "--spec", "-"])
    assert usage.exit_code == 2


def test_focused_patch_preserves_author_prose_and_unrelated_comments(tmp_path):
    request = decode_request(FIXTURE.read_text())
    assert apply(request, root=tmp_path).status == "applied"
    book = tmp_path / request["target"]
    book.write_text("# Owner comment\n" + book.read_text() + "# End comment\n")
    phase = tmp_path / ".cafe/skills/cafe-observe/SKILL.md"
    before = phase.read_text()
    patch = {
        "version": 1,
        "target": request["target"],
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["steps", "observe", "allowed_goto"], "value": "observe"}
        ],
        "companions": [
            {
                "version": 1,
                "target": ".cafe/skills/cafe-observe/SKILL.md",
                "mode": "patch",
                "operations": [
                    {
                        "op": "upsert",
                        "path": ["metadata", "workflow", "required_tools"],
                        "value": [],
                    }
                ],
            }
        ],
    }
    result = apply(patch, root=tmp_path)
    assert result.status == "applied", result.diagnostics
    assert phase.read_text().split("---\n", 2)[2] == before.split("---\n", 2)[2]
    assert book.read_text().startswith("# Owner comment\n")
    assert book.read_text().endswith("# End comment\n")
    assert apply(patch, root=tmp_path).status == "noop"


def test_phase_command_accepts_file_request_and_text_preview(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from cafe.ui.cli import app

    monkeypatch.chdir(tmp_path)
    request = decode_request(FIXTURE.read_text())["companions"][0]
    file = tmp_path / "phase.json"
    file.write_text(json.dumps(request))
    runner = CliRunner()
    report = runner.invoke(app, ["skill", "author", "phase", "--spec", str(file), "--dry-run"])
    assert report.exit_code == 0, report.output
    assert ".cafe/skills/cafe-observe/SKILL.md" in report.stdout
    assert not (tmp_path / ".cafe").exists()

"""Runtime catalogs remain usable from built distributions outside the checkout."""

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def test_distribution_runtime_copy_loads_without_checkout_or_working_directory(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    dist = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--out-dir", str(dist)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    resources = [
        str(path.relative_to(repo / "src"))
        for path in (repo / "src/cafe/data").glob("**/locales/*.yaml")
    ]
    owners = [
        path.parent.parent.name
        for path in (repo / "src/cafe/data/skills").glob("*/locales/en-US.yaml")
    ]
    assert owners
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert set(resources) <= set(archive.namelist())
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        for resource in resources:
            assert any(name.endswith(f"/src/{resource}") for name in archive.getnames())
    installed = tmp_path / "installed"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            "--no-deps",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import cafe
from cafe.core.runtime_locales import load_catalogs, render_text
from cafe.core.human_tasks import HumanTaskPolicy
from cafe.core.workspace_artifact import workspace_correction_prompt
from cafe.core.human_task_notifications import (
    HumanTaskSlackMessage, WorkflowCallbackFailureSlackMessage,
)
from cafe.skills.loader import SkillLoader
from cafe.skills.notification_copy import resolve_step_notification_presentation
from pathlib import Path
import importlib.util
assert cafe.__file__.startswith(sys.argv[1])
assert set(load_catalogs()) == {"en-US", "zh-TW"}
for locale in ("en-US", "zh-TW"):
    prompt = workspace_correction_prompt(
        "owned/{reason}.txt", consumed=1, remaining=2, locale=locale
    )
    assert "owned/{reason}.txt" in prompt
    assert "1" in prompt and "2" in prompt
    message = HumanTaskSlackMessage(
        "repo", "issue", "flow", "task", "spec", "output-review", locale,
        resolve_step_notification_presentation(
            playbook_data={"steps": {"spec": {"skill": "cafe-spec"}}},
            step_name="spec", task_id="output-review", locale=locale,
        ),
    )
    assert "issue" in message.to_slack_payload()["text"]
    failure = WorkflowCallbackFailureSlackMessage(
        "repo", "issue", "spec", "event", "callback_ValueError", locale,
        message.presentation,
    )
    assert "issue" in failure.to_slack_payload()["text"]
loader = SkillLoader(project_root=Path.cwd(), global_root=Path.cwd() / "empty-global")
assert not any(key.startswith("human_task.") for key in load_catalogs()["en-US"])
for owner in sys.argv[2:]:
    entry, declaration = loader.get_workflow_declaration_entry(owner)
    root = entry.directory / "locales"
    catalogs = load_catalogs(root)
    assert set(catalogs["en-US"]) == set(catalogs["zh-TW"])
    for locale in ("en-US", "zh-TW"):
        from string import Formatter
        for key, template in catalogs[locale].items():
            parameters = {field: "fixture-" + field
                          for _, field, _, _ in Formatter().parse(template) if field}
            assert render_text(key, locale=locale, catalog_root=root, **parameters) == template.format(**parameters)
        for policy in declaration.human_tasks:
            snapshot = policy.for_locale(locale).model_dump(mode="json")
            assert isinstance(snapshot["prompt"], str)
            assert HumanTaskPolicy.model_validate(snapshot).model_dump(mode="json") == snapshot
            presentation = resolve_step_notification_presentation(
                playbook_data={"steps": {"renamed": {"skill": owner}}},
                step_name="renamed", task_id=policy.id, locale=locale, skill_loader=loader,
            )
            message = HumanTaskSlackMessage(
                "repo", "issue", "flow", "task", "renamed", policy.id, locale, presentation,
            )
            failure = WorkflowCallbackFailureSlackMessage(
                "repo", "issue", "renamed", "event", "callback_ValueError", locale, presentation,
            )
            for payload in (message.to_slack_payload(), failure.to_slack_payload()):
                assert "issue" in payload["text"]
                if presentation.step_label:
                    assert presentation.step_label in payload["text"]
            if presentation.action_label:
                assert presentation.action_label in message.to_slack_payload()["text"]
script_root = Path(cafe.__file__).parent / "data/skills/use-cafe-workflow/scripts"
sys.path.insert(0, str(script_root))
for name in ("render_workflow_progress", "format_kickoff_contract"):
    spec = importlib.util.spec_from_file_location(name, script_root / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if name == "render_workflow_progress":
        expected = load_catalogs()["zh-TW"]["manager.progress.missing"]
        assert module.render_progress(locale="zh-TW") == expected
    else:
        empty = {"deliver": [], "cleanup": []}
        result = module._render_closeout(empty, empty, zh=True)
        assert load_catalogs()["zh-TW"]["manager.kickoff.no_commands"] in result
"""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed), *owners],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

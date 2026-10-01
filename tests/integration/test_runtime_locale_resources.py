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
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for locale in ("en-US", "zh-TW"):
            assert f"cafe/data/locales/{locale}.yaml" in archive.namelist()
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        for locale in ("en-US", "zh-TW"):
            assert any(
                name.endswith(f"/src/cafe/data/locales/{locale}.yaml")
                for name in archive.getnames()
            )
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
from cafe.core.runtime_locales import load_catalogs
from cafe.core.workspace_artifact import workspace_correction_prompt
from cafe.core.human_task_notifications import (
    HumanTaskSlackMessage, WorkflowCallbackFailureSlackMessage,
)
from cafe.skills.loader import SkillLoader
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
    )
    assert "issue" in message.to_slack_payload()["text"]
    failure = WorkflowCallbackFailureSlackMessage(
        "repo", "issue", "spec", "event", "callback_ValueError", locale,
    )
    assert "issue" in failure.to_slack_payload()["text"]
loader = SkillLoader(project_root=Path.cwd(), global_root=Path.cwd() / "empty-global")
policy = loader.get_workflow_declaration("cafe-spec").human_tasks[0]
assert isinstance(policy.for_locale("zh-TW").prompt, str)
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
        [sys.executable, "-I", "-c", script, str(installed)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

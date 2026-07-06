import json
import os
import subprocess
from pathlib import Path


def _write_workflow_files(tmp_path: Path, *, intent: str = "await_agent") -> tuple[Path, Path]:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-issue"
    issue_dir.mkdir(parents=True)
    next_step = issue_dir / "next_step.txt"
    next_step.write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "build",
                "to_owner": "agent",
                "to_step": "review",
                "intent": intent,
                "status_code": "",
                "created_at": "2026-07-06T00:00:00Z",
                "source": "test",
            }
        ),
        encoding="utf-8",
    )
    blackboard = issue_dir / "blackboard.json"
    blackboard.write_text(
        json.dumps({"handoff_summary": "Build finished and is ready for review."}),
        encoding="utf-8",
    )
    return next_step, blackboard


def _script_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "src/cafe/data/skills/cafe-workflow-common/scripts/notify-slack.sh"


def test_notify_slack_skips_missing_webhook_and_writes_trace(tmp_path: Path) -> None:
    next_step, blackboard = _write_workflow_files(tmp_path, intent="need_permission")
    trace_file = tmp_path / "trace" / "slack.jsonl"

    result = subprocess.run(
        [
            "/bin/bash",
            str(_script_path()),
            "--step",
            "build",
            "--next-step",
            str(next_step),
            "--blackboard",
            str(blackboard),
            "--trace-file",
            str(trace_file),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path / "home")},
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["action"] == "skipped"
    assert payload["reason"] == "webhook_file_missing"

    trace = json.loads(trace_file.read_text(encoding="utf-8").strip())
    assert trace["action"] == "skipped"
    assert trace["reason"] == "webhook_file_missing"
    assert trace["issue"] == "demo-issue"
    assert trace["step"] == "build"
    assert trace["intent"] == "need_permission"
    assert trace["summary"] == "Build finished and is ready for review."
    assert trace["timestamp"]


def test_notify_slack_posts_payload_with_workflow_context(tmp_path: Path) -> None:
    next_step, blackboard = _write_workflow_files(tmp_path, intent="workflow_complete")
    trace_file = tmp_path / "trace" / "slack.jsonl"
    home = tmp_path / "home"
    home.mkdir()
    (home / ".slack-webhook").write_text("https://hooks.slack.test/demo\n", encoding="utf-8")

    curl_log = tmp_path / "curl.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        "python3 - \"$@\" <<'PY'\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "payload = args[args.index('--data') + 1]\n"
        "url = args[-1]\n"
        f"open({str(curl_log)!r}, 'w', encoding='utf-8').write(json.dumps({{'payload': payload, 'url': url}}))\n"
        "PY\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        [
            "/bin/bash",
            str(_script_path()),
            "--step",
            "build",
            "--next-step",
            str(next_step),
            "--blackboard",
            str(blackboard),
            "--trace-file",
            str(trace_file),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["action"] == "posted"

    curl_call = json.loads(curl_log.read_text(encoding="utf-8"))
    slack_payload = json.loads(curl_call["payload"])
    assert curl_call["url"] == "https://hooks.slack.test/demo"
    assert "Issue: demo-issue" in slack_payload["text"]
    assert "Step: build" in slack_payload["text"]
    assert "Intent: workflow_complete" in slack_payload["text"]
    assert "Build finished and is ready for review." in slack_payload["text"]

    trace = json.loads(trace_file.read_text(encoding="utf-8").strip())
    assert trace["action"] == "posted"
    assert trace["intent"] == "workflow_complete"


def test_standard_phase_notify_slack_wrappers_delegate_to_shared_script() -> None:
    project_root = Path(__file__).resolve().parents[2]
    wrappers = [
        project_root / "src/cafe/data/skills/cafe-spec/scripts/notify-slack.sh",
        project_root / "src/cafe/data/skills/cafe-plan/scripts/notify-slack.sh",
        project_root / "src/cafe/data/skills/cafe-develop/scripts/notify-slack.sh",
        project_root / "src/cafe/data/skills/cafe-review/scripts/notify-slack.sh",
        project_root / "src/cafe/data/skills/cafe-pr/scripts/notify-slack.sh",
    ]

    for wrapper in wrappers:
        content = wrapper.read_text(encoding="utf-8")
        assert "../../cafe-workflow-common/scripts/notify-slack.sh" in content
        assert "exec /bin/bash" in content

"""Run one finite, tool-restricted model evidence review; see README.md."""

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

# One-time, manual evaluation only; no pytest collection or workflow integration.
root = Path(os.environ.get("CAFE_QA_EVAL_DIR", "/tmp/issue647-eval"))
label, role = sys.argv[1:3]
work = root / "empty"
work.mkdir(exist_ok=True)
output = root / f"{label}-{role}.md"
policy = (root / label / f"{role}.md").read_text()
if role == "qa":
    policy = policy.split("---", 2)[-1]
    policy += (
        "\n" + Path("src/cafe/data/skills/cafe-qa/references/regression_acceptance.md").read_text()
    )
    policy += "\n" + Path("src/cafe/data/agents/qa/Quinn.md").read_text()
    if label == "candidate":
        policy += (
            "\n" + Path("src/cafe/data/skills/cafe-qa/references/execution_steps.md").read_text()
        )
else:
    policy = policy[
        policy.index("Only an executed required phase becomes due") : policy.index(
            "The Manager must complete all applicable review passes"
        )
    ]
packet = (root / "cases.md").read_text()
if role == "qa":
    task = "你是 QA，依下面提供的技能與各案例資料進行有限、離線證據判讀。每案以簡短 criteria 列或段落說明能成立的驗收結論、需要的下一步與既有路由。案例互相獨立，最多每案 170 個英文詞。"
else:
    task = "你是 Manager，依下面指引在已到期的既有審查點審查每案的驗收／交付主張。每案說明是否可以接受該主張、必要的既有下一步及理由，最多每案 120 個英文詞。另說明同樣資料若 QA 立即前進且屬合法 not_required，普通監督時如何處理。"
prompt = (
    task
    + "\n只使用本次給定資料，不呼叫任何工具，不讀其他檔案，不執行測試，不搜尋網路。可提出待補驗工作，不宣稱已執行。輸出是工程評估紀錄，請用 en-US。沒有提供 {agent_file} 等路徑的實體檔案；不要將評估包的省略當作產品缺陷。\n<policy>\n"
    + policy
    + "\n</policy>\n<cases>\n"
    + packet
    + "\n</cases>\n"
)
(root / f"{label}-{role}-prompt.txt").write_text(prompt)
cmd = [
    "codex",
    "--ask-for-approval",
    "never",
    "exec",
    "--ignore-user-config",
    "--ignore-rules",
    "--ephemeral",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--model",
    "gpt-6.1-sol",
    "-c",
    'model_reasoning_effort="high"',
    "-c",
    "project_doc_max_bytes=0",
    "-c",
    'web_search="disabled"',
    "-c",
    "mcp_servers={}",
    "-c",
    "agents.enabled=false",
    "-c",
    "notify=[]",
    "--json",
    "--cd",
    str(work),
    "--output-last-message",
    str(output),
]
for feature in (
    "shell_tool",
    "unified_exec",
    "apps",
    "plugins",
    "hooks",
    "multi_agent",
    "multi_agent_v2",
    "memories",
    "browser_use",
    "browser_use_external",
    "in_app_browser",
    "js_repl",
    "code_mode",
    "code_mode_host",
    "image_generation",
    "remote_control",
):
    cmd += ["-c", f"features.{feature}=false"]
env = dict(os.environ)
for name in ("CODEX_REMOTE_PAYLOAD", "CODEX_SESSION_ID", "CODEX_THREAD_ID"):
    env.pop(name, None)
start = time.monotonic()
events = []
terminal = False
tool_use = False
error = None
print(f"{label}/{role}: isolated evaluation started", flush=True)
with (root / f"{label}-{role}-stderr.txt").open("w+") as diagnostic:
    child = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=diagnostic,
        env=env,
        start_new_session=True,
        text=True,
    )
    child.stdin.write(prompt)
    child.stdin.close()
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    while True:
        if time.monotonic() - start > 240:
            error = "bounded evaluation timeout"
            os.killpg(child.pid, signal.SIGTERM)
            break
        ready = selector.select(1)
        if ready:
            line = child.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                error = "non-structured stdout"
                continue
            kind = event.get("type")
            item_type = event.get("item", {}).get("type")
            record = {"type": kind, "item_type": item_type}
            if kind in ("error", "turn.failed") or item_type == "error":
                record["diagnostic"] = str(
                    event.get("message")
                    or event.get("error")
                    or event.get("item", {}).get("message")
                    or event.get("item", {}).get("text")
                    or "provider diagnostic unavailable"
                )[:500]
            events.append(record)
            terminal = terminal or kind == "turn.completed"
            tool_use = tool_use or item_type in (
                "command_execution",
                "mcp_tool_call",
                "web_search",
                "collab_tool_call",
            )
            print(
                f"{label}/{role}: observed {kind}" + (f"/{item_type}" if item_type else ""),
                flush=True,
            )
        elif child.poll() is not None:
            break
    try:
        status = child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        status = child.wait(timeout=5)
        error = error or "post-output exit timeout"
    diagnostic.flush()
    diagnostic.seek(0)
    diagnostic_text = diagnostic.read()
metadata = {
    "model": "gpt-6.1-sol",
    "reasoning_effort": "high",
    "role": role,
    "variant": label,
    "exit_code": status,
    "turn_completed": terminal,
    "final_output_present": output.is_file() and bool(output.stat().st_size),
    "tool_use_observed": tool_use,
    "elapsed_seconds": round(time.monotonic() - start, 2),
    "error": error,
    "events": events,
    "command": cmd,
}
(root / f"{label}-{role}-completion.json").write_text(json.dumps(metadata, indent=2))
print(json.dumps({k: v for k, v in metadata.items() if k not in ("events", "command")}), flush=True)
if status != 0 or not terminal or not metadata["final_output_present"] or tool_use or error:
    print(
        "Evaluation incomplete; preserve diagnostics and do not score it as complete.", flush=True
    )
    print(diagnostic_text[-500:], flush=True)
    sys.exit(1)

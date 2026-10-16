"""Manual, pinned replay and archival checks for issue #647; see README.md."""

import argparse
import copy
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVAL_ROOT.parents[2]
BASELINE = "a5d0413f8f10e8a52690bb275ed53fcbb689cb7b"
INITIAL_MANAGER = "4155ea671e28f74f5fe80763b8f86073afd1491d"
REVIEWED = "ca948f59a94129db7875a7f4dcd0d9e7db253a43"
EVAL_PATH = "tests/evals/qa_evidence/"
QA_PATH = "src/cafe/data/skills/cafe-qa/"
MANAGER_PATH = "src/cafe/data/skills/use-cafe-workflow/references/running_workflow.md"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_bytes(commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=REPO_ROOT)


def load_archive(kind):
    """Extract verbatim payloads and expand the two lossless metadata profiles."""
    provenance = json.loads((EVAL_ROOT / f"{kind}-provenance.json").read_bytes())
    corpus = (EVAL_ROOT / provenance["corpus"]["file"]).read_bytes()
    assert sha256(corpus) == provenance["corpus"]["sha256"]
    sources = {}
    for path, entry in provenance["texts"].items():
        data = corpus[entry["offset"] : entry["offset"] + entry["bytes"]]
        assert len(data) == entry["bytes"] and sha256(data) == entry["sha256"], path
        sources[path] = data

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if "$record" in value:
                return {
                    **copy.deepcopy(provenance["profiles"]["record"]),
                    **expand(value["$record"]),
                }
            if "$command" in value:
                command = provenance["profiles"]["command"].copy()
                for flag, parameter in zip(
                    ("--cd", "--output-last-message"), value["$command"], strict=True
                ):
                    command[command.index(flag) + 1] = parameter
                return command
            return {key: expand(item) for key, item in value.items()}
        return value

    metadata = {path: expand(entry["value"]) for path, entry in provenance["metadata"].items()}
    return sources, metadata, provenance


def role_records(packet):
    if packet == "preliminary":
        return load_archive("preliminary")[1]["observations/completion.json"]
    return [
        r
        for r in load_archive("original")[1]["original-observations/completion.json"]
        if r["group"] == packet and r["role"] != "assessment"
    ]


def replay_inputs(variant, role, packet):
    """Build the exact tested prompt from pinned policies and unchanged common inputs."""
    initial = variant == "initial"
    record = next(
        r
        for r in role_records(packet)
        if r["role"] == role
        and r["variant"] == ("candidate" if initial else variant)
        and (r.get("round") == "initial") == initial
    )
    sources = []

    def materialize(commit, path):
        data = git_bytes(commit, path)
        sources.append({"commit": commit, "path": path, "sha256": sha256(data), "data": data})
        return data.decode()

    if role == "qa":
        commit = BASELINE if variant == "baseline" else REVIEWED
        policy = materialize(commit, QA_PATH + "SKILL.md").split("---", 2)[-1]
        policy += "\n" + materialize(BASELINE, QA_PATH + "references/regression_acceptance.md")
        policy += "\n" + materialize(BASELINE, "src/cafe/data/agents/qa/Quinn.md")
        if variant == "candidate":
            policy += "\n" + materialize(REVIEWED, QA_PATH + "references/execution_steps.md")
        task = (
            "你是 QA，依下面提供的技能與各案例資料進行有限、離線證據判"
            "讀。每案以簡短 criteria 列或段落說明能成立的驗收結"
            "論、需要的下一步與既有路由。案例互相獨立，最多每案 170 "
            "個英文詞。"
        )
    else:
        commit = (
            BASELINE
            if variant == "baseline"
            else INITIAL_MANAGER if initial or packet == "preliminary" else REVIEWED
        )
        policy = materialize(commit, MANAGER_PATH)
        policy = policy[
            policy.index("Only an executed required phase becomes due") : policy.index(
                "The Manager must complete all applicable review passes"
            )
        ]
        task = (
            "你是 Manager，依下面指引在已到期的既有審查點審查每案"
            "的驗收／交付主張。每案說明是否可以接受該主張、必要的既有下一"
            "步及理由，最多每案 120 個英文詞。另說明同樣資料若 QA"
            " 立即前進且屬合法 not_required，普通監督時如何"
            "處理。"
        )
    packet_path = "cases.md" if packet == "preliminary" else f"{packet}-cases.md"
    cases = materialize(REVIEWED, EVAL_PATH + packet_path)
    prompt = (
        task
        + (
            "\n只使用本次給定資料，不呼叫任何工具，不讀其他檔案，不執行測"
            "試，不搜尋網路。可提出待補驗工作，不宣稱已執行。輸出是工程評"
            "估紀錄，請用 en-US。沒有提供 {agent_file}"
            " 等路徑的實體檔案；不要將評估包的省略當作產品缺陷。\n<po"
            "licy>\n"
        )
        + policy
        + "\n</policy>\n<cases>\n"
        + cases
        + "\n</cases>\n"
    ).encode()
    assert sha256(prompt) == record["prompt_sha256"]
    assert sha256(cases.encode()) == record["case_packet_sha256"]
    if "policy_sha256" in record:
        assert sources[0]["sha256"] == record["policy_sha256"]
    return (
        prompt,
        sources,
        {
            "policy_sha256": sources[0]["sha256"],
            "effective_policy_sha256": sha256(policy.encode()),
            "case_packet_sha256": sha256(cases.encode()),
            "policy_commit": commit,
        },
    )


def assessor_input(packet, round_):
    kind = "preliminary" if packet == "preliminary" else "original"
    texts, _, provenance = load_archive(kind)
    recipe = provenance["assessor_inputs"][f"{packet}-{round_}"]
    parts = []
    for part in recipe["parts"]:
        if "literal" in part:
            parts.append(part["literal"].encode())
        else:
            path = part["source"]
            parts.append(texts[path] if path in texts else git_bytes(REVIEWED, EVAL_PATH + path))
    prompt = b"".join(parts)
    assert len(prompt) == recipe["bytes"] and sha256(prompt) == recipe["sha256"]
    return prompt


def verify_archives():
    """Manual consolidation proof, separate from semantic judgments and runtime gates."""
    counts = dict(texts=0, metadata=0, role_prompts=0, assessor_prompts=0)
    for kind in ("original", "preliminary"):
        texts, metadata, provenance = load_archive(kind)
        assert provenance["reviewed_commit"] == REVIEWED
        for path, data in texts.items():
            assert data == git_bytes(REVIEWED, EVAL_PATH + path), path
            counts["texts"] += 1
        for path, value in metadata.items():
            original = git_bytes(REVIEWED, EVAL_PATH + path)
            entry = provenance["metadata"][path]
            assert len(original) == entry["bytes"] and sha256(original) == entry["sha256"], path
            assert value == json.loads(original), path
            counts["metadata"] += 1
        for name, recipe in provenance["assessor_inputs"].items():
            packet, round_ = name.rsplit("-", 1)
            assert sha256(assessor_input(packet, round_)) == recipe["sha256"]
            counts["assessor_prompts"] += 1
    for packet in ("historical", "scope", "preliminary"):
        for record in role_records(packet):
            variant = "initial" if record.get("round") == "initial" else record["variant"]
            prompt, _, _ = replay_inputs(variant, record["role"], packet)
            assert sha256(prompt) == record["prompt_sha256"]
            counts["role_prompts"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", nargs="?", choices=("baseline", "candidate", "initial"))
    parser.add_argument("role", nargs="?", choices=("qa", "manager"))
    parser.add_argument(
        "--packet", choices=("historical", "scope", "preliminary"), default="historical"
    )
    parser.add_argument(
        "--assessment",
        choices=("initial", "final"),
        help="materialize an archived blinded assessor prompt; does not invoke a model",
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--verify-archive", action="store_true")
    args = parser.parse_args()
    if args.verify_archive:
        print(json.dumps(verify_archives()))
        return
    if not args.assessment and (
        args.variant is None
        or args.role is None
        or (args.variant == "initial" and (args.role != "manager" or args.packet == "preliminary"))
    ):
        parser.error(
            "choose a recorded role/variant; initial exists only for original-source Manager"
        )
    root = Path(os.environ.get("CAFE_QA_EVAL_DIR", "/tmp/issue647-replay"))
    root.mkdir(parents=True, exist_ok=True)
    if args.assessment:
        prompt = assessor_input(args.packet, args.assessment)
        (root / "assessor-prompt.txt").write_bytes(prompt)
        print(json.dumps({"prompt_sha256": sha256(prompt), "model_invoked": False}))
        return
    label, role = args.variant, args.role
    prompt_bytes, sources, identity = replay_inputs(label, role, args.packet)
    manifest = {
        **identity,
        "packet": args.packet,
        "variant": label,
        "role": role,
        "prompt_sha256": sha256(prompt_bytes),
        "materialized_sources": [],
    }
    for index, source in enumerate(sources):
        name = f"input-{index}-{Path(source['path']).name}"
        (root / name).write_bytes(source["data"])
        manifest["materialized_sources"].append(
            {k: v for k, v in source.items() if k != "data"} | {"file": name}
        )
    (root / "prompt.txt").write_bytes(prompt_bytes)
    (root / "inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if args.prepare_only:
        print(json.dumps({"prompt_sha256": manifest["prompt_sha256"], "model_invoked": False}))
        return
    prompt = prompt_bytes.decode()
    work = root / "empty"
    work.mkdir(exist_ok=True)
    output = root / f"{label}-{role}.md"
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
    metadata["prompt_sha256"] = sha256(prompt_bytes)
    metadata["output_sha256"] = sha256(output.read_bytes()) if output.is_file() else None
    (root / f"{label}-{role}-completion.json").write_text(json.dumps(metadata, indent=2))
    print(
        json.dumps({k: v for k, v in metadata.items() if k not in ("events", "command")}),
        flush=True,
    )
    if status != 0 or not terminal or not metadata["final_output_present"] or tool_use or error:
        print(
            "Evaluation incomplete; preserve diagnostics and do not score it as complete.",
            flush=True,
        )
        print(diagnostic_text[-500:], flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

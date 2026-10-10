"""Invocation-local native reviewer definitions and bounded provider evidence.

The parent CLI delegates the review. This module never launches a reviewer CLI.
"""

import json
import os
import re
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

_RESOURCE = "__CAFE_NATIVE_REVIEW_RESOURCE__"
_MAX_BYTES = 128 * 1024
_PROMPT = (
    "Independently review correctness, completeness, unnecessary changes, architecture and tests. "
    "Inspect files only. Never edit, commit, execute mutations or change workflow state. "
    "Do not delegate another review. Return exactly one JSON object with "
    "findings and targeted_tests. "
    "Each finding has severity (blocking or nonblocking) and detail. Preserve every blocker."
)


def reviewer_type(configuration):
    if configuration is None or configuration.get("cli") == "claude":
        return "cafe_reviewer"
    digest = sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()[:20]
    name = "cafe_reviewer_" + digest
    return name.replace("_", "-") + ":" + name if configuration.get("cli") == "copilot" else name


def review_instructions(configuration):
    """Provider-owned invocation/identity instructions projected into the contract."""
    cli = (configuration or {}).get("cli")
    return {
        "claude": "Call Agent with subagent_type; reviewer_id is its tool-use ID. Wait "
        "for the synchronous tool result.",
        "codex": "Use the checkpoint_command with your exact parent thread ID, then end "
        "this turn with exactly one JSON object: "
        '\'{"cafe_native_review":{"prompt":"Review instructions and '
        'CAFE_REVIEW_CHECKPOINT:<receipt_id>"}}\'. '
        "CAFE forks one native read-only reviewer in this same app-server. "
        "Do not call spawn_agent or launch another CLI. On the continuation turn, "
        "copy its independent conclusion and reviewer_id into native_review.json, "
        "then finish the declared handoff. Do not change reviewed content or "
        "request another review in that continuation; use the declared self-loop "
        "for a correction round.",
        "gemini": "Call invoke_agent with agent_name and prompt (or call the same-name "
        "native agent tool with query). reviewer_id is the tool_id. Wait for "
        "completed progress with terminateReason GOAL and its independent "
        "result.",
        "copilot": "Call task with agent_type and prompt. "
        + (
            "Set model to " + str((configuration or {}).get("model")) + ". "
            if (configuration or {}).get("model_behavior") == "independent_override"
            else "Do not override model. "
        )
        + "reviewer_id is the toolCallId. Wait for subagent.completed and the "
        "matching successful tool result.",
        "cursor-agent": "Call the native Task tool with the custom subagent type and prompt. "
        "Do not override model or enable background. reviewer_id is the "
        "call_id. Wait for its synchronous success result.",
    }.get(cli, "Native review configuration is unavailable.")


def validate_configuration(config):
    configuration = config.native_review_configuration
    if (
        configuration.get("cli") != config.cli.value
        or configuration.get("read_only") is not True
        or configuration.get("checkpoint_interface") != "parent_command"
    ):
        raise ValueError("unsupported native reviewer configuration")
    behavior = configuration.get("model_behavior")
    if behavior not in {"inherits_parent", "independent_override"}:
        raise ValueError("unsupported native reviewer model behavior")
    if behavior == "inherits_parent" and configuration.get("model") != config.model:
        raise ValueError("inherited reviewer model differs from the effective parent")
    if not isinstance(configuration.get("model"), str) or not configuration["model"].strip():
        raise ValueError("native reviewer model is required")
    if config.cli.value == "cursor-agent" and behavior != "inherits_parent":
        raise ValueError(
            "Cursor native plugin reviewers inherit the parent model; "
            "independent_override is unsupported"
        )
    return configuration


def project(config, command):
    if config.native_review_configuration is None:
        return command
    configuration = validate_configuration(config)
    role = reviewer_type(configuration)
    cli = config.cli.value
    if cli == "codex":
        cwd = command[command.index("-C") + 1]
        return [
            command[0], "-C", cwd, "-a", "never", "app-server",
            "--disable", "multi_agent", "--disable", "multi_agent_v2",
            "-c", "agents.enabled=false",
        ]
    if cli == "copilot":
        return [*command, "--plugin-dir", _RESOURCE, "--output-format=json", "--stream=on"]
    if cli == "cursor-agent":
        return [*command, "--plugin-dir", _RESOURCE]
    return command  # Gemini loads user agents from its invocation-local home.


def _reject_native_collision(cli, role, root):
    directories = (
        [root / ".gemini/agents"]
        if cli == "gemini"
        else [root / name / "agents" for name in (".cursor", ".claude", ".grok")]
    )
    visited = 0
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".mdc", ".markdown"}:
                continue
            visited += 1
            if visited > 1024 or path.stat().st_size > _MAX_BYTES:
                raise ValueError("native agent collision check exceeds bound")
            content = path.read_text(encoding="utf-8")
            match = re.match(r"^---\s*\n(.*?)\n---", content, re.S)
            if match:
                fields = yaml.safe_load(match.group(1))
                if isinstance(fields, dict) and fields.get("name") == role:
                    raise ValueError(
                        "native reviewer definition collides with an existing workspace agent"
                    )


def _reject_gemini_overrides(role, original, root, environment):
    paths = [original / "settings.json", root / ".gemini/settings.json"]
    system = environment.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
    if system:
        paths.append(Path(system))
    paths.append(Path("/etc/gemini-cli/settings.json"))
    for path in paths:
        if path.is_file():
            settings = json.loads(path.read_text(encoding="utf-8"))
            if role in settings.get("agents", {}).get("overrides", {}):
                raise ValueError(
                    "native reviewer effective configuration is overridden by Gemini settings"
                )


def _markdown(fields):
    return "---\n" + yaml.safe_dump(fields, sort_keys=False) + "---\n\n" + _PROMPT + "\n"


@contextmanager
def invocation(config, command, environment, *, working_directory=None):
    """Lease native configuration only while the physical parent process runs."""
    if config.native_review_configuration is None:
        yield command, environment
        return
    configuration = validate_configuration(config)
    cli = config.cli.value
    role = reviewer_type(configuration)
    model = configuration["model"]
    definition_name = role.split(":")[-1]
    workspace = Path(working_directory or Path.cwd()).resolve()
    if cli in {"gemini", "cursor-agent"}:
        _reject_native_collision(cli, role, workspace)
        if cli == "cursor-agent":
            _reject_native_collision(cli, role, Path.home())
    if cli == "codex":
        # Native fork permissions are verified by the app-server transport.
        yield command, environment
        return
    with TemporaryDirectory(prefix="cafe-native-review-") as temporary:
        root = Path(temporary)
        resource = root
        if cli in {"copilot", "cursor-agent"}:
            (root / "agents").mkdir()
            (root / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": definition_name.replace("_", "-"),
                        "version": "1.0.0",
                        "agents": "agents/",
                    }
                ),
                encoding="utf-8",
            )
            fields = {
                "name": definition_name,
                "description": "Independent read-only implementation reviewer",
            }
            if cli == "copilot":
                fields.update(tools=["view", "glob", "grep"], model=model)
                suffix = ".agent.md"
            else:
                # Plugin and workspace loaders use different frontmatter keys.
                fields.update(
                    readonly=True, permissionMode="readonly", model="inherit", background=False
                )
                suffix = ".md"
            (root / "agents" / (definition_name + suffix)).write_text(
                _markdown(fields), encoding="utf-8"
            )
        elif cli == "gemini":
            _gemini_overlay(root, environment, role, model, workspace)
        projected = [argument.replace(_RESOURCE, str(resource)) for argument in command]
        yield projected, environment


def _gemini_overlay(root, environment, role, model, workspace):
    """Keep auth and native sessions in place; do not install a global agent."""
    original = Path(environment.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
    _reject_gemini_overrides(role, original, workspace, environment)
    _reject_native_collision("gemini", role, original.parent)
    shared = original.parent / ".agents"
    if shared.exists():
        (root / ".agents").symlink_to(shared)
    home = root / ".gemini"
    home.mkdir()
    original.mkdir(parents=True, exist_ok=True)
    # Session continuation must survive disposal of this temporary definition.
    for name in ("tmp", "history", "projects"):
        (original / name).mkdir(exist_ok=True)
    for entry in original.iterdir():
        if entry.name not in {"agents", "settings.json"}:
            (home / entry.name).symlink_to(entry)
    agents = home / "agents"
    agents.mkdir()
    # Existing user agents keep their ordinary identities and precedence.
    if (original / "agents").is_dir():
        for entry in (original / "agents").iterdir():
            if entry.name == role + ".md":
                raise ValueError("native reviewer definition collides with an existing user agent")
            (agents / entry.name).symlink_to(entry)
    settings_file = original / "settings.json"
    settings = (
        json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    )
    settings.setdefault("experimental", {})["enableAgents"] = True
    (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (agents / (role + ".md")).write_text(
        _markdown(
            {
                "name": role,
                "description": "Independent read-only implementation reviewer",
                "kind": "local",
                "model": model,
                "tools": ["read_file", "list_directory", "glob", "grep_search"],
            }
        ),
        encoding="utf-8",
    )
    environment["GEMINI_CLI_HOME"] = str(root)


def _records(lines):
    for line in lines:
        if len(line.encode("utf-8", errors="replace")) > _MAX_BYTES:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError, RecursionError):
            continue
        if isinstance(record, dict):
            yield line, record


def _start(config, identifier, prompt, line, observed_at):
    marker = re.search(
        r"CAFE_REVIEW_CHECKPOINT:([A-Za-z0-9-]+)", prompt if isinstance(prompt, str) else ""
    )
    return {
        "reviewer_id": identifier,
        "receipt_id": marker.group(1) if marker else None,
        "configuration": config.native_review_configuration,
        "observed_at": observed_at.get(id(line)) if observed_at is not None else None,
        "terminal": None,
        "exit_status": None,
    }


def _invalidate(observation):
    observation.update(invalid=True, terminal=None, exit_status=None)


def _remember_start(invocations, config, identifier, prompt, line, times, arguments):
    candidate = _start(config, identifier, prompt, line, times)
    candidate["start_fingerprint"] = sha256(
        json.dumps(arguments, sort_keys=True).encode()
    ).hexdigest()
    if identifier in invocations:
        previous = invocations[identifier]
        if previous["start_fingerprint"] != candidate["start_fingerprint"]:
            _invalidate(previous)
        return previous
    invocations[identifier] = candidate
    return candidate


def _conclusion(payload):
    if isinstance(payload, dict):
        payload = json.dumps(payload)
    if not isinstance(payload, str) or len(payload.encode()) > _MAX_BYTES:
        return {}
    decoder = json.JSONDecoder()
    conclusions = []
    for match in re.finditer(r"\{", payload):
        try:
            value, _ = decoder.raw_decode(payload[match.start() :])
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict) and set(value) == {"findings", "targeted_tests"}:
            conclusions.append(value)
    if len(conclusions) != 1:
        return {}
    value = conclusions[0]
    tests = value.get("targeted_tests")
    findings = value.get("findings")
    if (
        not isinstance(tests, list)
        or not tests
        or any(not isinstance(test, str) or not test.strip() for test in tests)
        or not isinstance(findings, list)
    ):
        return {}
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or set(finding) != {"severity", "detail"}
            or not isinstance(finding.get("severity"), str)
            or finding["severity"] not in {"blocking", "nonblocking"}
            or not isinstance(finding.get("detail"), str)
            or not finding["detail"].strip()
        ):
            return {}
    return value


def _finish(observation, payload):
    if observation.get("invalid"):
        return
    conclusion = _conclusion(payload)
    if not conclusion:
        _invalidate(observation)
    elif observation.get("terminal") == "result":
        if any(observation.get(key) != value for key, value in conclusion.items()):
            _invalidate(observation)
    else:
        observation.update(terminal="result", exit_status=0, **conclusion)


def observations(config, lines, observed_at=None, environment=None):
    if config.native_review_configuration is None:
        return []
    cli = config.cli.value
    role = reviewer_type(config.native_review_configuration)
    if cli == "gemini":
        return _gemini_observations(config, role, lines, observed_at)
    if cli == "copilot":
        return _copilot_observations(config, role, lines, observed_at)
    if cli == "cursor-agent":
        return _cursor_observations(config, role, lines, observed_at)
    if cli == "codex":
        return _codex_observations(config, role, lines, observed_at, environment or os.environ)
    return []


def _gemini_observations(config, role, lines, times):
    invocations = {}
    for line, record in _records(lines):
        identifier = record.get("tool_id")
        args = record.get("parameters", {})
        if (
            record.get("type") == "tool_use"
            and isinstance(args, dict)
            and (
                record.get("tool_name") == role
                or (record.get("tool_name") == "invoke_agent" and args.get("agent_name") == role)
            )
            and isinstance(identifier, str)
            and len(invocations) < 16
        ):
            task_prompt = (
                args.get("prompt")
                if record.get("tool_name") == "invoke_agent"
                else args.get("query", args.get("prompt"))
            )
            _remember_start(invocations, config, identifier, task_prompt, line, times, args)
        if (
            record.get("type") == "tool_result"
            and isinstance(identifier, str)
            and identifier in invocations
        ):
            if record.get("status") != "success":
                _invalidate(invocations[identifier])
                continue
            try:
                progress = json.loads(record.get("output", ""))
            except (ValueError, TypeError, RecursionError):
                _invalidate(invocations[identifier])
                continue
            if (
                isinstance(progress, dict)
                and progress.get("isSubagentProgress") is True
                and progress.get("agentName") == role
                and progress.get("state") == "completed"
                and progress.get("terminateReason") == "GOAL"
            ):
                _finish(invocations[identifier], progress.get("result"))
            else:
                _invalidate(invocations[identifier])
    return list(invocations.values())


def _copilot_observations(config, role, lines, times):
    invocations = {}
    native_started = set()
    completed = set()
    failed = set()
    results = {}
    for line, record in _records(lines):
        data = record.get("data", {})
        if not isinstance(data, dict):
            continue
        identifier = data.get("toolCallId")
        if not isinstance(identifier, str):
            continue
        args = data.get("arguments", {})
        if (
            record.get("type") == "tool.execution_start"
            and data.get("toolName") == "task"
            and isinstance(args, dict)
            and args.get("agent_type") == role
            and isinstance(identifier, str)
            and len(invocations) < 16
        ):
            _remember_start(invocations, config, identifier, args.get("prompt"), line, times, args)
            model_ok = (
                args.get("model") == config.native_review_configuration["model"]
                if config.native_review_configuration["model_behavior"] == "independent_override"
                else not args.get("model")
            )
            if not model_ok or args.get("mode") in {"background", "async"}:
                failed.add(identifier)
        if record.get("type") == "subagent.started":
            if (
                data.get("agentName") == role
                and data.get("executionMode") not in {"background", "async"}
                and not data.get("parentId")
                and data.get("model") in {None, config.native_review_configuration["model"]}
            ):
                native_started.add(identifier)
            else:
                failed.add(identifier)
        if record.get("type") == "subagent.completed":
            if (
                data.get("agentName") == role
                and data.get("cancelled") is not True
                and data.get("firstDispatchedModel") == config.native_review_configuration["model"]
                and data.get("model") == config.native_review_configuration["model"]
            ):
                completed.add(identifier)
            else:
                failed.add(identifier)
        if record.get("type") == "subagent.failed":
            failed.add(identifier)
        if record.get("type") == "tool.execution_complete":
            result = data.get("result", {})
            if data.get("success") is not True or not isinstance(result, dict):
                failed.add(identifier)
            elif identifier in results and results[identifier] != result.get("content"):
                failed.add(identifier)
            else:
                results[identifier] = result.get("content")
    for identifier, observation in invocations.items():
        if identifier in failed:
            _invalidate(observation)
        elif identifier in (native_started & completed) and identifier in results:
            _finish(observation, results[identifier])
    return list(invocations.values())


def _cursor_task(record):
    call = record.get("tool_call", {})
    if not isinstance(call, dict):
        return {}
    if isinstance(call.get("taskToolCall"), dict):
        return call["taskToolCall"]
    tool = call.get("tool", {})
    if (
        isinstance(tool, dict)
        and tool.get("case") == "taskToolCall"
        and isinstance(tool.get("value"), dict)
    ):
        return tool["value"]
    return {}


def _cursor_observations(config, role, lines, times):
    invocations = {}
    for line, record in _records(lines):
        if record.get("type") != "tool_call":
            continue
        identifier = record.get("call_id")
        task = _cursor_task(record)
        args = task.get("args", {})
        if not isinstance(args, dict):
            continue
        subagent = args.get("subagentType", {})
        value = subagent.get("custom", {}) if isinstance(subagent, dict) else {}
        if (
            record.get("subtype") == "started"
            and isinstance(value, dict)
            and value.get("name") == role
            and isinstance(identifier, str)
            and len(invocations) < 16
        ):
            observation = _remember_start(
                invocations, config, identifier, args.get("prompt"), line, times, args
            )
            if (
                args.get("model")
                or args.get("isBackground")
                or args.get("mode") in {"TASK_MODE_BACKGROUND", "background"}
            ):
                _invalidate(observation)
        if (
            record.get("subtype") == "completed"
            and isinstance(identifier, str)
            and identifier in invocations
        ):
            if (
                invocations[identifier]["start_fingerprint"]
                != sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
            ):
                _invalidate(invocations[identifier])
            wrapped = task.get("result", {})
            success = wrapped.get("success", {}) if isinstance(wrapped, dict) else {}
            if not isinstance(success, dict) or success.get("isBackground") is not False:
                _invalidate(invocations[identifier])
                continue
            steps = success.get("conversationSteps", [])
            if not isinstance(steps, list):
                _invalidate(invocations[identifier])
                continue
            texts = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                message = step.get("assistantMessage", {})
                if isinstance(message, dict) and isinstance(message.get("text"), str):
                    texts.append(message["text"])
            _finish(invocations[identifier], texts[-1] if texts else None)
    return list(invocations.values())


def _codex_roles(environment, parent):
    """Bind native spawn arguments to returned child IDs in the exact parent journal."""
    import uuid

    from cafe.agents.cli.codex_usage import _find_journal, _open_journal

    roles = {}
    if not isinstance(parent, str) or str(uuid.UUID(parent)) != parent:
        return roles
    home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
    journal = _find_journal(home, parent)
    with _open_journal(journal) as handle:
        first = json.loads(handle.readline(_MAX_BYTES + 1))
        if first.get("type") != "session_meta" or first.get("payload", {}).get("id") != parent:
            return roles
        size = os.fstat(handle.fileno()).st_size
        offset = max(handle.tell(), size - 2 * 1024 * 1024)
        if offset > handle.tell():
            handle.seek(offset)
            handle.readline(_MAX_BYTES + 1)
        calls = {}
        while handle.tell() < size:
            line = handle.readline(_MAX_BYTES + 1)
            if len(line) > _MAX_BYTES:
                raise ValueError("native review journal record exceeds bound")
            record = json.loads(line)
            item = record.get("payload", {})
            if record.get("type") != "response_item" or not isinstance(item, dict):
                continue
            if item.get("type") == "function_call" and item.get("name") == "spawn_agent":
                args = json.loads(item.get("arguments", "{}"))
                if isinstance(args, dict):
                    calls[item.get("call_id")] = args
            if item.get("type") == "function_call_output" and item.get("call_id") in calls:
                output = json.loads(item.get("output", "{}"))
                if isinstance(output, dict) and isinstance(output.get("agent_id"), str):
                    roles[output["agent_id"]] = calls[item["call_id"]]
    return roles


def _codex_observations(config, role, lines, times, environment):
    records = list(_records(lines))
    parents = {r.get("thread_id") for _, r in records if r.get("type") == "thread.started"}
    if len(parents) != 1:
        return []
    parent = next(iter(parents))
    try:
        roles = _codex_roles(environment, parent)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        roles = {}
    invocations = {}
    for line, record in records:
        item = record.get("item", {})
        if (
            record.get("type") != "item.completed"
            or not isinstance(item, dict)
            or item.get("type") != "collab_tool_call"
            or item.get("sender_thread_id") != parent
        ):
            continue
        receivers = item.get("receiver_thread_ids", [])
        if item.get("status") != "completed":
            if isinstance(receivers, list):
                for child in receivers:
                    if isinstance(child, str) and child in invocations:
                        _invalidate(invocations[child])
            continue
        if (
            item.get("tool") == "spawn_agent"
            and isinstance(receivers, list)
            and len(receivers) == 1
        ):
            child = receivers[0]
            args = roles.get(child, {})
            if isinstance(child, str) and args.get("agent_type") == role and len(invocations) < 16:
                observation = _remember_start(
                    invocations, config, child, item.get("prompt"), line, times, args
                )
                if (
                    args.get("message") != item.get("prompt")
                    or args.get("model")
                    or args.get("sandbox_mode")
                ):
                    _invalidate(observation)
        states = item.get("agents_states", {})
        if item.get("tool") == "wait" and isinstance(states, dict):
            for child, state in states.items():
                if child not in invocations or not isinstance(state, dict):
                    continue
                if state.get("status") == "completed":
                    if _codex_child_is_read_only(
                        environment, child, config.native_review_configuration["model"]
                    ):
                        _finish(invocations[child], state.get("message"))
                    else:
                        _invalidate(invocations[child])
                elif state.get("status") not in {"pending_init", "running"}:
                    _invalidate(invocations[child])
    return list(invocations.values())


def _codex_child_is_read_only(environment, child, model):
    from cafe.agents.cli.codex_usage import _find_journal, _open_journal

    try:
        home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        path = _find_journal(home, child)
        with _open_journal(path) as handle:
            if os.fstat(handle.fileno()).st_size > 2 * 1024 * 1024:
                return False
            first = json.loads(handle.readline(_MAX_BYTES + 1))
            if first.get("type") != "session_meta" or first.get("payload", {}).get("id") != child:
                return False
            seen = False
            for line in handle:
                if len(line) > _MAX_BYTES:
                    return False
                record = json.loads(line)
                if record.get("type") != "turn_context":
                    continue
                actual = record.get("payload", {})
                if (
                    (model is not None and actual.get("model") != model)
                    or actual.get("sandbox_policy", {}).get("type") != "read-only"
                    or actual.get("permission_profile", {}).get("type") == "disabled"
                ):
                    return False
                seen = True
            return seen
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return False

# Talk to the current workflow Manager

Use `cafe manager chat` in an issue worktree. From the repository checkout,
select the issue explicitly when its branch and active marker do not identify
one consistent issue:

```bash
# In the prepared issue worktree
cafe manager chat

# In the repository root, including an issue in a registered Git worktree
cafe manager chat --issue issue465

# Run the composed CLI directly from a CAFE source checkout
PYTHONPATH=src python -m cafe.manager.cli manager chat --issue issue465
```

The command identifies the selected issue/worktree before accepting input.
Explicit selection overrides the checkout default without changing branch,
working directory, inventory or active issue. Missing, contradictory, unreadable,
archived and competing issue authorities fail with recovery guidance. Chat never
selects the newest issue or an unrelated issue just because it is the only one.

## Terminal conversation

Enter one message per line. Blank lines send nothing. `/quit`, EOF and Ctrl-C
while waiting for input exit without sending a request. A terminal is required;
piped input is rejected. Opening chat sends no greeting, status request, wake or
task answer. Each submitted message makes one exact-session delivery. Parsed
replies appear only after session identity and required evidence are verified.
This CAFE terminal loop provides text turns rather than the provider's full
interactive interface.

Each turn refreshes current durable workflow, handoff, pending task ownership,
accepted artifact references and confirmed scope/approval constraints. Old chat
history and wake notices cannot replace those facts. Unreadable or inconsistent
state stops the conversation. Chat shares the callback session lock during a
submitted turn; idle chat does not hold it. A busy turn fails promptly: wait for
it to finish and retry. Cancelling terminates only the command's provider child,
releases its lock and never stops the workflow worker.

## Supported identities and recovery

| Mode / active identity | Behavior and next action |
| --- | --- |
| `event-driven`, contract-managed provider-owned Codex primary | Resume its recorded active session. No model override is supplied, preserving inherited settings. |
| `event-driven`, already active provider-owned Codex fallback | Resume only its recorded current session with its confirmed fallback model; model evidence is required. |
| `event-driven`, host-bound Codex thread | Return to that originating host conversation. Restore access in the host if necessary; the CLI cannot resume its host binding. |
| `event-driven`, another provider | Return to that provider's originating conversation. This delivery supports Codex only; chat does not switch providers. |
| `event-driven`, legacy or unverifiable dispatch | Inspect and recover confirmed records through the existing originating Manager/setup procedure; no automatic migration. |
| `attached` or `unattended` | Return to the originating Manager conversation. This delivery does not capture a verifiable terminal identity for these modes or change their mode. |
| Missing identity | Return to the originating Manager and its existing confirmed setup procedure. Chat creates no workflow or replacement session. |
| Stale/conflicting identity, changed route or workflow, pending callback recovery | Inspect the originating conversation and current callback state; restore confirmed authority before retrying. Records are not remapped or repaired by chat. |
| Missing Codex CLI | Restore `codex` on PATH, then retry the same command and identity, or return to the originating conversation. |
| Resume fails before acceptance | Restore access to the existing provider/conversation, then retry explicitly. No automatic bootstrap or fallback occurs. |
| Acceptance/completion is uncertain, or a submitted turn is cancelled | Check the originating conversation before explicitly retrying: the message may already have arrived. Chat never automatically replays it. |

Primary, inactive fallback, historical and phase-agent sessions cannot replace the
selected current identity. Conversation locale comes from stored workflow state.
The command does not enable wakeups, change callback routing, acquire worker
ownership or interrupt foreground/host conversations.

## Human decisions and phase chat

Chat access grants no new authority. Opening, closing, failing, asking for status
or acknowledging a reply leaves workflow position, blackboard, baton, pending
HumanTasks and worker execution unchanged. Later requests remain subject to the
existing ownership, confirmation, permission and capability rules. Existing
Manager-confirmable behavior is preserved.

For a pending HumanTask, retain its recorded answer route:

```bash
cafe task inspect <task-id>
cafe task complete <task-id> --help
```

Read the task's required result and complete it through that authorized route
with an explicit answer. Mandatory stops, `user_required` decisions, human-owned
clarifications, permissions and capabilities are not answered by chat access or
by a generic acknowledgement.

`cafe chat <phase-role>` keeps its existing semantics. A custom playbook role
named `manager` still uses `cafe chat manager`; `cafe manager chat` targets the
issue's workflow Manager independently of playbook roles and phase sessions.
`python -m cafe.ui.cli` remains the generic compatibility entrypoint; use the
composed module above for Manager commands. Merely opening Manager chat does not
install or synchronize helper skills.

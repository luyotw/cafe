# The CAFE Engine

CAFE is a workflow engine that helps AI agents complete complex, long-running
work reliably.

Describe the outcome you want, and CAFE guides agents through clarification,
planning, execution, review, and delivery. It pauses when your judgment is
needed and can resume interrupted work from where it stopped.

To install CAFE, see [INSTALL.md](INSTALL.md), or ask your coding agent to follow
that file for you.

## 1. What CAFE Is

### Core idea

**Turn AI agent work from a one-off conversation into a manageable, resumable,
and continuously improvable workflow.**

CAFE does not replace agents or make every decision for you. It connects people,
agents, workflow steps, and outputs so agents can move forward within clear
boundaries and return control when human judgment is genuinely needed.

### Three highlights

#### 1. Move complex work all the way to completion

CAFE connects clarification, planning, execution, review, and delivery into one
workflow. You do not need to tell an agent what to do next in every conversation
or remember where the work stopped.

It is especially useful for software development, research, editorial work,
incident response, and other work that takes multiple steps to complete.

#### 2. Let agents work autonomously while people control key decisions

You decide in advance what agents may handle on their own and what still needs
your approval.

CAFE keeps work moving within those boundaries and pauses only for decisions
such as requirement tradeoffs or expanded permissions. You do not need to
supervise every step, and agents do not silently take control beyond the agreed
scope.

#### 3. Resume, hand off, and reuse work

Workflow state, outputs, decisions, and review results stay with the project
instead of depending on one chat session or one agent's memory.

For example, if Claude reaches its usage limit, Codex can read the current
workflow state and take over from the stopping point without requiring you to
explain the requirements again or restart the work.

Work can continue across sessions, agents, and interruptions with explicit
progress intact. Mature workflows can also become reusable playbooks for future
tasks or be adapted to fit a team's needs.

### Good use cases

CAFE is a good fit for:

- technical founders, operators, and small agent-native teams;
- software changes that benefit from explicit specification, planning, and
  independent review;
- long-running work that may pause, change owner, or span several sessions;
- repeated operating procedures that should be reviewed and evolved in Git;
- custom workflows such as research, editorial production, incidents, and
  other artifact-driven processes.

CAFE is probably unnecessary for a disposable one-prompt task. It is also not
an arbitrary host-privilege executor, a replacement for human review, or a
complete project-management platform. Its value starts when the workflow and
its history matter beyond the current chat.

### Supported coding agents

CAFE currently integrates with:

- [Claude Code](https://claude.com/product/claude-code)
- [OpenAI Codex CLI](https://developers.openai.com/codex/cli)
- [GitHub Copilot CLI](https://github.com/features/copilot/cli)
- [Cursor CLI](https://cursor.com/cli)
- [Gemini CLI](https://geminicli.com/)

At least one supported coding agent is required. CAFE itself requires Python 3.10+
and Git. GitHub workflows also require the
[GitHub CLI](https://cli.github.com/).

## 2. Use CAFE

### Install without using a terminal yourself

Send this request to any coding agent that can inspect files and run local
commands:

```text
Install the latest stable CAFE release from https://github.com/luyotw/cafe.
Follow INSTALL.md. I authorize the user-scoped changes described there.
Do not use sudo, do not modify system Python, and do not change my shell profile.
```

The repository bootstrap installs CAFE in an isolated user environment. It
then installs the `use-cafe-workflow`, `write-cafe-agent`, `write-cafe-phase`,
and `write-cafe-playbook` skills for detected supported agents. It does not
require a vendor-specific plugin.

For the exact mutation boundaries, prerequisites, manual alternatives, and
upgrade behavior, read [INSTALL.md](INSTALL.md).

### Start work with `use-cafe-workflow`

Start your coding agent in the project you want CAFE to manage. Then describe the
outcome instead of manually operating each CAFE command. For a GitHub issue:

```text
Use CAFE to work on GitHub issue #123 in this repository.
Keep our conversation in zh-TW and repository content in en-US.
```

For work that does not start from GitHub:

```text
Use CAFE to add CSV export to this project. Preserve the existing public API.
```

The `use-cafe-workflow` manager will inspect the repository and propose a
kickoff contract before it mutates the project or starts the first phase. The
proposal includes:

- the playbook and scope;
- conversation and repository-content locales;
- planned human confirmation points and reactive handoffs;
- issue size, risk, and the mandate boundary;
- the primary and fallback CLI/model chain for each agent phase;
- whether the issue should use a worktree.

Confirm or revise that contract once. The manager then prepares the issue and
executes one phase at a time. After every completed phase it inspects the
result and follows the persisted handoff. It changes a future phase model only
when you explicitly request it. It stops when a decision still belongs to you.

Common follow-up requests are similarly direct:

```text
Resume the current CAFE workflow.
```

```text
Show me the current CAFE status and explain what is waiting for me.
```

```text
Use <model-name> for the next develop iteration, then continue.
```

### Built-in playbooks

CAFE includes explicit software-development paths for different levels of
requirements and delivery rigor:

| Playbook | Path | Use when |
| --- | --- | --- |
| `direct` | develop → review → PR | The requested change is already clear and still needs independent review. |
| `direct-qa` | spec → develop → review → QA → PR | Requirements need confirmation and acceptance needs both independent review and QA, but implementation does not need a separate plan. |
| `direct-subagent-review` | develop + two subagent reviews → PR | The implementation boundary is already confirmed and focused detail and scope reviews can run inside Develop. |
| `subagent-flow` | spec + plan with subagent → develop + subagent reviews → PR | One owner should resolve requirements and implementation together with a planning partner before joint confirmation. |
| `subagent-flow-qa` | spec + plan with subagent → develop + subagent reviews → QA → PR | Subagent-assisted planning and review also need independent acceptance testing. |
| `simple` | spec → develop → QA → PR | The outcome needs confirmation and independent acceptance, but a low-risk docs, data, or config change does not need a separate plan or code review. |
| `standard` | spec → plan → develop → review → PR | The standard development path and built-in default. |
| `standard-qa` | spec → plan → develop → review → QA → PR | Standard development needs independent product acceptance. |
| `tdd` | spec → plan → TDD develop → review → PR | The implementation should follow test-driven development. |
| `tdd-qa` | spec → plan → TDD develop → review → QA → PR | TDD also needs independent product acceptance. |
| `bug` | diagnosis + RED → minimal repair + GREEN → review → PR | A confirmed, bounded defect needs verified reproduction and regression proof. |
| `hotfix` | develop → review → PR | An urgent production correction already has an understood repair and regression boundary. |

`standard` replaces the former built-in `default` ID. There is no alias or
automatic migration. `hotfix` remains available for urgent production fixes,
and the research, editorial, and incident playbooks retain their domain-specific
flows.

For a bounded defect, explicitly select `bug`, for example: “Use CAFE with the
bug playbook for issue #123: twice(3) returns 5; the confirmed result is 6.” Both
GitHub issue and manual input are supported. This choice does not change the
built-in default or other confirmed workflow choices. Use `standard` when scope
and implementation sequencing need specification and planning, or `tdd` for
planned test-driven delivery beyond a focused defect.

`bug` verifies the report and retains an unfixed revision, replayable regression,
actual defect-specific RED, then unchanged-test GREEN and focused checks. A new
regression can be demonstrated in isolation so diagnosis hands off a clean
workspace; repair commits the same test with the minimal fix through normal
hooks. Independent review precedes PR, including every PR-requested code
correction. Routine verified diagnosis adds no approval cycle; normal local PR
review, publication confirmation and capability checks remain required.

Missing reproduction, unrelated test failures, disputed expected behavior or
investigation beyond the defect boundary go to human clarification. Diagnosis,
repair and review each allow three unfinished attempts per correction cycle;
exhaustion requires the existing HumanTask and an authorized supported limit
adjustment to resume. The shared three-round disagreement rule also applies.
Interrupted work resumes from durable evidence after revision/test identity
checks. Broad redesign, speculative cleanup, incident response and work needing
a separate QA owner require another suitable workflow or a human scope decision.

`subagent-flow` and `subagent-flow-qa` use one `spec_plan` phase and one combined `plan`
artifact containing the complete requirements, implementation approach, Test
List, and Todo List. A native planning partner discusses the draft before its
joint confirmation gate. When preparing this workflow for a user who wants to
approve the draft personally, assign the `spec_plan` confirmation gate to the
user in the kickoff stop contract. Early clarification saves the original
request and known decisions in a provisional plan so a fresh session can resume
without losing requirements; it adds no confirmation gate. Revisions stay in
the same phase and repeat the discussion. Confirmed requirements invalidated
during development return to `spec_plan`; routine implementation corrections
stay in Develop. The existing detail/scope review and mandatory PR local review
still apply.
The QA variant adds independent acceptance against the requirements embedded
in the combined plan. QA failures return to Develop; each correction repeats
the subagent reviews and QA before publication.

To inspect what is available, ask your agent:

```text
Show me the CAFE playbooks available in this project and explain when to use
each one.
```

The QA variants share one declarative QA phase. It performs observable
acceptance checks, records reproducible failures, and returns every correction
through development and review before QA runs again.

### Create a custom workflow with skills

Custom workflows have three authoring layers:

| Need | Use | Project source of truth |
| --- | --- | --- |
| Define a role persona and its checklist guidance | `write-cafe-agent` | `.cafe/agents/<role>/<name>.md` |
| Define how one phase behaves | `write-cafe-phase` | `.cafe/skills/<name>/` |
| Connect phases and gates | `write-cafe-playbook` | `.cafe/playbooks/<id>.yaml` |
| Execute or resume the workflow | `use-cafe-workflow` | Runtime state under `.cafe/issues/` |

Define or update the agents and phase skills first, then connect them with a
playbook. Agent guideline bullets become checklist items in phases that opt into
role guidance. For example:

```text
Use write-cafe-agent to create a Traditional Chinese security reviewer whose
guidelines apply across every review phase.
```

Then define the phase behavior:

```text
Use write-cafe-phase to create a project skill that turns an approved research
brief into a cited report. The report must stop for user approval.
```

Then:

```text
Use write-cafe-playbook to create a research-publication playbook from the
existing brief, report, review, and publish skills.
```

These authoring skills encode CAFE's artifact, plan handoff, ownership,
confirmation, tool, and validation rules. They should edit project sources of
truth, not generated issue artifacts or globally installed skill copies.

Before using a custom workflow, ask the authoring agent to validate its skill
bindings, confirmation gates, and graph:

```text
Validate the research-publication skills and playbook strictly. Show me its
planned confirmation gates, simulate every route, and fix any unexplained
warning before we use it.
```

You can then ask the manager to use that playbook by name:

```text
Use CAFE with the research-publication playbook for this brief.
```

The skills are the recommended interface because they preserve kickoff,
one-step execution, user-directed phase model changes, and human-handoff rules. The agent
operates the Engine commands on your behalf and should explain outcomes and
decisions rather than exposing command mechanics as the normal user interface.

## 3. When You Need More Control

You do not need the following details for your first workflow, but they are the
main concepts to know when customizing, diagnosing, or requesting advanced
operations from CAFE.

### Mental model

| Concept | Responsibility |
| --- | --- |
| Playbook | Step graph, roles, ownership, artifacts, tools, hooks, and transitions |
| Phase skill | Instructions and execution contract for one workflow behavior |
| Blackboard | Durable workflow state, artifacts, events, and current handoff |
| HumanTask | A persisted question, decision, approval, or external action owned by a person |
| Phase chain | Ordered primary and fallback CLI/model entries for one agent step |
| Worktree | An isolated Git checkout for one issue's code and workflow state |

Artifact naming, verified workspace companions, correction routes, and receipt
binding are documented in [Artifact contracts](docs/artifact-contracts.md).

Custom playbooks should express ownership boundaries as top-level steps.
`assignee_type: hybrid` is deprecated; see
[Migrating hybrid workflow steps](docs/hybrid-workflow-migration.md).

The repository is the definition layer; chat history is not the source of
truth. Runtime state currently lives under `.cafe/issues/`, while project
playbooks, skills, strategy, and settings remain versionable alongside the
project.

### Important project files

- `.cafe/config.yaml`: project playbook and general settings.
- `.cafe/strategic_context.yaml`: confirmed strategic documents, authority, and
  repository-wide conventions.
- `.cafe/phases.yaml`: exact CLI/model chains used by agent-executed steps.
- `.cafe/playbooks/`: project-defined workflow graphs.
- `.cafe/skills/`: project-defined phase, shared, and chat skills.
- `.cafe/issues/<issue>/`: issue configuration, blackboard, HumanTasks,
  iterations, artifacts, and handoffs.

Issue worktrees can carry their own `.cafe/phases.yaml`, allowing model choices
to differ between issues without changing repository-wide defaults.

### Repository task inbox

Use the task inbox when you need to find human work across every live workflow
in the repository. Pending tasks are shown by default in deterministic order;
completed and cancelled tasks appear only when requested.

```bash
cafe task ls
cafe task ls --assignee alice --step review --due-state unscheduled
cafe task ls --historical
cafe task ls --status completed
```

Inspect a task by its stable identifier before answering it:

```bash
cafe task inspect 7fe1a9e8-66fa-4df2-88d4-cd6af87fae43
cafe task inspect 7fe1a9e8-66fa-4df2-88d4-cd6af87fae43 --json
```

Completion is interactive when no result option is supplied. Automation may
provide the task's declared response as JSON directly or in a file:

```bash
cafe task complete 7fe1a9e8-66fa-4df2-88d4-cd6af87fae43
cafe task complete 7fe1a9e8-66fa-4df2-88d4-cd6af87fae43 \
  --result '{"decision":"confirm"}' --json
cafe task complete 7fe1a9e8-66fa-4df2-88d4-cd6af87fae43 \
  --result-file response.json
```

A supervising user may explicitly override the declared continuation and hand
the completed task to any phase that exists in the owning playbook. The task's
declared response is still required and validated:

```bash
cafe task complete 7fe1a9e8-66fa-4df2-88d4-cd6af87fae43 \
  --result '{"decision":"confirm","work_report":{"summary":"Implemented the requested change.","outcome":"The change is ready for review."}}' \
  --handoff-to review
```

The override, original continuation, and optional work report are retained in
the TaskResult. `--handoff-to` does not accept arbitrary names or terminate the
workflow; its value must be a phase declared by the playbook.

Add `--json` to list, inspect, or complete to receive one result object with
`ok`, `operation`, `data`, and `error` fields. Filters combine with AND
semantics. Current HumanTask records have no due timestamp, so their due state
is `unscheduled`; the inbox does not invent or manage due dates.

Inbox operations fail closed when an identifier is missing or duplicated, a
task is stale or terminal, its workflow is missing or archived, or durable
records are corrupt. The error identifies the affected task or workflow when
known and includes a recovery action. Repair or explicitly restore the named
workflow, then retry the same stable identifier; the inbox never switches the
active issue or chooses an ambiguous record automatically.

To make new HumanTasks from any built-in, global, or project playbook
discoverable in a fixed Slack channel, follow the supported
[Slack HumanTask notification guide](docs/human-task-slack-notifications.md).
The channel-bound credential stays in `~/.slack-webhook`; project playbooks,
hooks, tasks, and agents cannot choose another destination or receive the
credential. Slack delivery never replaces `cafe task inspect` or
`cafe task complete`.

### Inspect and recover

Run `cafe status` in the issue worktree for the current workflow status followed by
the existing timeline and usage tables. A pending HumanTask includes its exact
`cafe task inspect <id>` command; a paused workflow includes its recorded reason,
and a completed workflow is explicitly labeled. Missing or conflicting records
are reported as unknown rather than guessed. Status inspection does not resume
work, complete tasks, or repair records.

To talk to the current workflow Manager from its issue worktree, run
`cafe manager chat`. From the repository root, use
`cafe manager chat --issue <issue-name>`. The terminal reconnects to an existing
verified event-driven Codex session; other combinations give recovery guidance.
See [Manager chat](docs/manager-chat.md) for the support matrix and terminal exit
behavior. Pending HumanTasks retain their `cafe task inspect` and
`cafe task complete` answer routes.

Ask the manager for the information or recovery outcome you need:

```text
Show the current workflow timeline, owner, latest phase output, and anything
that is waiting for me.
```

```text
List the prepared CAFE issues and their worktree locations.
```

```text
Explain what would be removed if we reset the latest development iteration.
Do not make the change until I confirm.
```

```text
Audit this project's CAFE playbooks and skills, then explain any inconsistency
in user-facing terms.
```

Resetting workflow iterations does not revert Git changes. Restoring archived
issues and deleting workflow state are also explicit operations; the manager
should show the exact scope before acting.

Do not manually edit the blackboard or handoff files during ordinary recovery.
If behavior is wrong rather than merely incomplete, let `use-cafe-workflow`
classify whether the defect belongs to a project playbook, a phase skill, or the
CAFE runtime before changing sources.

### Read-only diagnostic chat

Use the current issue branch and its configured role, phase, provider, model and
conversation context:

```bash
cafe chat developer --read-only
cafe chat developer --phase develop --read-only
cafe chat developer --read-only --prompt "Diagnose the current issue"
cafe chat developer --phase develop --read-only -p "Diagnose the current issue"
```

Both interactive and one-shot diagnosis support fresh and resumed conversations
with all five integrated providers, including custom roles/phases and currently
configured backup sessions. The current invocation receives these native options:

| Provider | Native options |
| --- | --- |
| Codex | `--sandbox read-only`, never approval |
| Claude | `--tools Read,Glob,Grep`, matching `--allowed-tools`, `--disallowed-tools Bash,Edit,Write,NotebookEdit`, `--permission-mode plan` |
| Gemini | `--approval-mode plan` |
| Cursor | `--mode ask`, removing CAFE-built `--force`/`--yolo` |
| Copilot | `--available-tools=view,glob,grep`, `--allow-tool=read`, `--deny-tool=shell`, `--deny-tool=write`, removing CAFE-built broad tool approvals |

Availability lists restrict model tools; approval lists alone do not do so.
Copilot availability names differ from permission kinds. Its installed help and
command reference describe `--allow-all-tools` as required for programmatic use;
CAFE uses the explicit read approval/cap instead and reports any actual version
rejection without restoring broad approval. Unsupported operations fail before
launch. Actual native option,
authentication or backend errors remain errors, without switching provider or
retrying writable chat. Omitting the flag retains ordinary writable behavior.

CAFE skips helper/chat-skill synchronization, handoff preparation/clearing,
session/timestamp persistence, usage publication and Gemini `.geminiignore`
preparation (including existing files and linked targets). Existing artifacts, task
and result records, baton, blackboard and associated linked/shared context are
read without CAFE initialization, reconciliation, repair, task completion or
ownership changes. Missing optional state stays absent; required unsafe context
reports an error. Permitted stale-session or prompt-too-long recovery retains
native restrictions and the configured provider/model, updating session identity
only in memory. Diagnostic output remains available, but CAFE does not record
chat usage or save the recovered session.

These native model-tool parameters are not immutable protection of the entire
CLI process. Provider-owned history/session/configuration persistence may still
write inside or outside the repository. Native UI commands, permission changes,
integrations/subprocesses and IPC/daemon paths are not guaranteed confined.
Recorded limitations include:

- Codex app-server `thread/settings/update` accepted changing read-only settings
  to `workspaceWrite` in a metadata-only probe. No file write occurred in that
  probe; it demonstrates mutable settings, not a demonstrated filesystem write.
- Claude Code **2.1.284** native TUI `!touch` created a scratch file despite
  restricted read tools, write/command denial and plan mode. That observed native
  shell path bypasses the model-tool restriction; other versions were not tested.
- Codex's native read-only mode can depend on its built-in sandbox/backend, which
  may be unavailable locally. CAFE surfaces the actual failure and adds no outer
  sandbox or environment admission probe.
- Gemini's documented plan mode permits writes to plan files and mutable policy.
  In headless execution, entry/exit of plan mode is automatically approved, and
  exit switches to YOLO automated implementation. This is a documentation-derived
  limitation, not a new native observation. See [Plan Mode](https://geminicli.com/docs/cli/plan-mode/)
  and [Policy engine](https://geminicli.com/docs/reference/policy-engine/).
- Cursor ask mode and Copilot's read-tool availability do not guarantee protection
  from native UI/settings, hooks/plugins/MCP, integrations/subprocesses, external
  IPC/daemon paths or provider-owned history/session/configuration writes. No new
  native mutation-denial experiment was run for these providers. Option sources:
  [Cursor parameters](https://cursor.com/docs/cli/reference/parameters) and
  [Copilot command/tool reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference).

Argument/forwarding tests and isolated CAFE storage inventories verify the
feature's wiring and CAFE effects. They do not prove native inference success,
whole-process confinement or denial of every mutation path.

### Global helper skills

CAFE synchronizes its bundled helper skills only for detected coding agents. An
agent is detected through its executable on `PATH` or existing vendor state;
directories containing only old CAFE-managed copies do not count as an
installation.

Ask your agent to repair a managed copy or preinstall for a specific agent:

```text
Repair CAFE's managed helper skills for every detected coding agent.
```

```text
Install CAFE's helper skills for Codex and Cursor even if they are not currently
detected. Tell me which user directories will be created before proceeding.
```

Explicit agent targets bypass detection and may create the selected vendor
skill directories. Synchronization is transactional and safe to repeat.

### Security and authority

CAFE separates agent-authored intent from trusted host execution. A workflow
may describe a desired operation, but credentials, external mutations, and
host-side capabilities remain subject to tool availability, policy, and human
authorization. Installing CAFE does not configure provider credentials or give
an agent additional system privileges.

### Project status and compatibility

CAFE is actively evolving. Release numbers follow the documented Semantic
Versioning policy, while roadmap stages describe product direction independently.

- [Roadmap](docs/roadmap.md)
- [Versioning policy](docs/versioning.md)
- [Changelog](CHANGELOG.md)
- [Latest release notes](docs/releases/v0.7.5.md)
- [Strategic positioning](docs/positioning.md)
- [Known runtime constraints](docs/known-constraints.md)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development
setup, testing, and release verification.

## License

CAFE is available under the [MIT License](LICENSE).

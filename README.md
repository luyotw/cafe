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
then installs the `use-cafe-workflow`, `write-cafe-phase`, and
`write-cafe-playbook` skills for detected supported agents. It does not require
a vendor-specific plugin.

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

The `use-cafe-workflow` driver will inspect the repository and propose a
kickoff contract before it mutates the project or starts the first phase. The
proposal includes:

- the playbook and scope;
- conversation and repository-content locales;
- planned human confirmation points and reactive handoffs;
- issue size, risk, and the mandate boundary;
- the primary and fallback CLI/model chain for each agent phase;
- whether the driver may adjust later model choices autonomously; and
- whether the issue should use a worktree.

Confirm or revise that contract once. The driver then prepares the issue and
executes one phase at a time. After every completed phase it inspects the
result, reassesses later model choices within the granted authority, and follows
the persisted handoff. It stops when a decision still belongs to you.

Common follow-up requests are similarly direct:

```text
Resume the current CAFE workflow.
```

```text
Show me the current CAFE status and explain what is waiting for me.
```

```text
Continue, but require my approval before changing any phase model.
```

### Built-in playbooks

CAFE includes explicit software-development paths for different levels of
requirements and delivery rigor:

| Playbook | Path | Use when |
| --- | --- | --- |
| `direct` | develop → review → PR | The requested change is already clear and still needs independent review. |
| `simple` | spec → develop → PR | The outcome needs confirmation, but a low-risk docs, data, or config change does not need a separate plan or agent review. |
| `standard` | spec → plan → develop → review → PR | The standard development path and built-in default. |
| `standard-qa` | spec → plan → develop → review → QA → PR | Standard development needs independent product acceptance. |
| `tdd` | spec → plan → TDD develop → review → PR | The implementation should follow test-driven development. |
| `tdd-qa` | spec → plan → TDD develop → review → QA → PR | TDD also needs independent product acceptance. |

`standard` replaces the former built-in `default` ID. There is no alias or
automatic migration. `hotfix` remains available for urgent production fixes,
and the research, editorial, and incident playbooks retain their domain-specific
flows.

To inspect what is available, ask your agent:

```text
Show me the CAFE playbooks available in this project and explain when to use
each one.
```

The QA variants share one declarative QA phase. It performs observable
acceptance checks, records reproducible failures, and returns every correction
through development and review before QA runs again.

### Create a custom workflow with skills

Custom workflows have two authoring layers:

| Need | Use | Project source of truth |
| --- | --- | --- |
| Define how one phase behaves | `write-cafe-phase` | `.cafe/skills/<name>/` |
| Connect phases and gates | `write-cafe-playbook` | `.cafe/playbooks/<id>.yaml` |
| Execute or resume the workflow | `use-cafe-workflow` | Runtime state under `.cafe/issues/` |

Define or update the phase skills first, then connect them with a playbook. For
example:

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

You can then ask the driver to use that playbook by name:

```text
Use CAFE with the research-publication playbook for this brief.
```

The skills are the recommended interface because they preserve kickoff,
one-step execution, model reassessment, and human-handoff rules. The agent
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

### Inspect and recover

Ask the driver for the information or recovery outcome you need:

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
issues and deleting workflow state are also explicit operations; the driver
should show the exact scope before acting.

Do not manually edit the blackboard or handoff files during ordinary recovery.
If behavior is wrong rather than merely incomplete, let `use-cafe-workflow`
classify whether the defect belongs to a project playbook, a phase skill, or the
CAFE runtime before changing sources.

### Global helper skills

CAFE synchronizes its three helper skills only for detected coding agents. An
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

CAFE is actively evolving. Roadmap version labels describe development cycles;
the changelog and release notes describe what a particular release actually
ships.

- [Roadmap](docs/roadmap.md)
- [Changelog](CHANGELOG.md)
- [Latest release notes](docs/releases/v0.3.2.md)
- [Strategic positioning](docs/positioning.md)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development
setup, testing, and release verification.

## License

CAFE is available under the [MIT License](LICENSE).

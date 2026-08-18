# The CAFE Engine
## AI-Driven Development Workflow Automation

**Maximize your vibe coding—without losing control of your codebase.**

CAFE (CLI Agent Flow Engine) is an AI-driven development workflow automation system powered by headless CLI agents. Our goal is to help individual developers leverage AI agents more effectively while maintaining code quality and long-term maintainability. It automates the entire development lifecycle—from requirements analysis to PR generation—by orchestrating specialized roles such as PM, Developer, and Reviewer, while keeping the codebase structured, inspectable, and maintainable.

CAFE is actively evolving—we're continuously iterating based on real-world usage and feedback to improve stability, usability, and integration with various AI agents.

To install CAFE, see [INSTALL.md](INSTALL.md), or ask your CLI agent to follow it.

![image](https://github.com/luyotw/cafe/blob/main/images/cafe-flow.png)

---

## Who is CAFE designed for?

CAFE is designed for **individual developers** who want to leverage CLI-based AI agents to accelerate development without sacrificing code quality or long-term maintainability.

CAFE is a good fit if you want to:

- **Use CLI agent tools more effectively**  
  Automate repetitive development tasks—such as requirement breakdown, implementation, code review, and PR generation—so you can focus on higher-level thinking and creative work.

- **Maintain control over AI-generated code**  
  CAFE enforces a multi-stage workflow that decomposes complex tasks into single-objective steps, guiding AI behavior and improving output stability and predictability.

- **Improve long-term code maintainability**  
  By introducing explicit planning and review stages, CAFE ensures that AI-generated code is not only functional, but also readable, structured, and easier to evolve over time.

> There is no magic bullet for high-quality code. Human review remains essential.  
> CAFE is designed to **minimize the time and cognitive load required for review**, not to eliminate it.

---

## Key Features

### High-Quality Code via Role-Oriented Workflow

If you frequently spend time correcting code produced by CLI agent tools, CAFE addresses this by introducing explicit role separation.  
A PM clarifies intent, a Developer implements changes, and a Reviewer evaluates the result—ensuring each stage is handled with a clear responsibility and objective.

---

### Standardized, Repeatable Development Flow

Clarify requirements → Confirm implementation plan → Grab a coffee → Review the PR → Done.

---

### Git Worktree Support for Parallel Development

CAFE supports `git worktree`, enabling parallel development across multiple issues without context switching. Each worktree operates in an isolated environment with its own configuration, allowing you to:

- Work on multiple features simultaneously without stashing changes
- Let agents work in the background while you focus on other tasks
- Maintain separate agent configurations per task (e.g., different models for different complexity levels)

---

### Flexible Integration with CLI Agent Tools

CAFE integrates with multiple CLI-based AI agents (e.g., Claude Code, Cursor CLI).
Different agents and models can be assigned per role, allowing you to balance cost, performance, and reasoning depth.

### Phase-Specific Agent Chains

CAFE executes each workflow step from the exact ordered chain in the active
worktree's `.cafe/phases.yaml`. When a primary agent hits a supported fallback
condition, CAFE tries the next configured entry without changing configuration.

```yaml
develop:
  name: David
  role: developer
  clis:
    - cli: codex
      model: gpt-5.6-sol
    - cli: claude
      model: claude-opus-5
```

Every entry requires an exact model. Missing, empty, or malformed step chains
stop before agent invocation with source, step, and field context.

---

### GitHub-Native Workflow

CAFE can fetch requirements directly from GitHub Issues and automatically generate pull requests, keeping your AI-driven workflow aligned with standard GitHub development practices.

## System Requirements

### Prerequisites
- **Python 3.10+**
- [git](https://git-scm.com/) - for version control
- [gh](https://cli.github.com/) - GitHub CLI (for PR creation and issue management)

### Agent CLI Tools (at least one is required)
- [Claude CLI](https://claude.com/product/claude-code)
- [OpenAI Codex CLI](https://developers.openai.com/codex/cli)
- [GitHub Copilot CLI](https://github.com/features/copilot/cli)
- [Cursor CLI](https://cursor.com/zh-Hant/cli)
- [Gemini CLI](https://geminicli.com/)

Support for more CLI agent tools is planned for the future. Stay tuned!

> **Note**: CAFE leverages the **headless mode** of these CLI tools, which means CAFE operates without requiring interactive sessions or IDE integrations. The CLI tools execute commands and return results to CAFE for processing, enabling seamless automation of the entire development workflow.

## Installation

### Ask Your CLI Agent (Recommended)

CAFE does not require a vendor-specific plugin. Ask any CLI agent that can
inspect repository files and run local commands to install it for you:

```text
Install the latest stable CAFE release from https://github.com/luyotw/cafe.
Follow INSTALL.md. I authorize the user-scoped changes described there.
Do not use sudo, do not modify system Python, and do not change my shell profile.
```

The repository bootstrap installs CAFE in an isolated user environment and
synchronizes the bundled workflow skills for detected Claude, Codex, Copilot,
Cursor, and Gemini installations. See [INSTALL.md](INSTALL.md) for the exact
safety boundaries and agent instructions.

### Manual Installation

From PyPI:

```bash
pip install cafe-engine
```

From source:

```bash
# Clone repository
git clone https://github.com/luyotw/cafe.git
cd cafe

# Install CAFE
pip install -e .

# Or install the development version (including testing tools)
pip install -e ".[dev]"
```

After installation, you can use the `cafe` command:
```bash
cafe --help
```

## Usage

### Quick Start

> Please ensure you have installed the prerequisites from the [System Requirements](#system-requirements) section and at least one Agent CLI tool.

1.  **Initialize CAFE**:
    ```bash
    cafe init
    ```
    This initializes project settings and bundled default content. Issue-owned
    phase chains are established by the workflow driver after kickoff confirmation.

2.  **Start the development workflow**:
    ```bash
    cafe prepare
    ```
    Switch to the worktree path if set, then:
    ```bash
    cafe make
    ```

3.  **Finalize and sync back**:
    ```bash
    cafe close
    ```

### Phase Configuration

`.cafe/phases.yaml` is the sole runtime execution configuration, with the step
name as the top-level key.

```yaml
build:
  name: Build step
  role: developer
  clis:
    - cli: claude
      model: claude-opus-5
    - cli: gemini
      model: gemini-2.5-pro
```

The highest precedence source is worktree-local `.cafe/phases.yaml` in the active
worktree, then repository `.cafe/phases.yaml`. Missing or malformed entries are rejected with
field-level validation errors.

### Project Settings

Project settings (playbook, rigor, auto-update) live in `.cafe/config.yaml` and are managed separately from phase execution configuration:

```bash
# Interactive
cafe setup

# Non-interactive
cafe setup --playbook default --rigor high --auto-update
```

You can also grant every workflow agent access to additional project directories:

```yaml
# .cafe/config.yaml
allowed_directories:
  - src
  - tests
```

For a one-off run, append directories with `cafe make --add-dir scripts --add-dir docs`.
Configured and CLI-provided directories must exist before the workflow starts.

### Global Workflow Helper Skills

Every `cafe` CLI startup detects supported agent installations, performs a fast
per-machine fingerprint check, and installs or updates bundled workflow helper
skills when their sources changed or an installed copy is missing. Detection
uses an agent executable on `PATH` or existing vendor state other than copies
managed only by CAFE. This covers fresh machines, new checkouts, and package
upgrades without creating directories for agents that are not installed.

The fingerprint and installed copies are local to each machine. Multiple
machines can develop CAFE concurrently: each one syncs from its current local
checkout, and receives another machine's committed skill changes after the
normal Git push/pull exchange. CAFE does not auto-pull or modify a checkout.
Concurrent CAFE processes on one machine serialize complete sync batches so
their destination updates cannot interleave. A batch stages every changed copy
before publishing and rolls the published copies back if any update fails.

CAFE's Git hooks also verify and synchronize all managed copies immediately
after every commit or merge, including amended commits. Enable the hooks once
per checkout:

```bash
./setup-hooks.sh
```

Use the explicit command for recovery after an automatic sync warning, repairing
manually edited destination content, or limiting the target CLIs:

```bash
cafe skill sync-global
```

The default sync copies `use-cafe-workflow`, `write-cafe-playbook`, and
`write-cafe-phase`, including their references and scripts, to the detected
subset of:

- `~/.claude/skills/`
- `~/.codex/skills/`
- `~/.copilot/skills/`
- `~/.cursor/skills/`
- `~/.gemini/skills/`

The command is safe to rerun: it reports each destination as installed,
updated, or unchanged. Explicit `--cli` targets bypass detection and may create
the selected vendor directory:

```bash
cafe skill sync-global --cli codex --cli cursor
```

Pass bundled skill names as positional arguments to override the default set.

### Multiple Worktrees

In worktree mode, each worktree maintains independent configuration:
```bash
cafe prepare --worktree .cafe/worktrees/issue42
cd .cafe/worktrees/issue42
cafe make
```

## Core Architecture

### 5-in-1 Workflow:

- The development process consists of five main phases:
  - **Phase 1: Requirements Analysis** - The PM agent clarifies requirements and writes specification documents.
  - **Phase 2: Implementation Analysis** - The Developer agent creates an implementation plan and breaks down tasks.
  - **Phase 3: Development** - The Developer agent implements the features and commits the code.
  - **Phase 4: Code Review** - The Reviewer agent reviews the code. If modifications are needed, the process returns to Phase 3.
  - **Phase 5: Create PR** - The Developer agent automatically creates a GitHub PR or allows the user to review locally. If there are suggestions for changes, the process returns to Phase 3.
- You can run the entire flow with a single command, `cafe make`, or resume an interrupted flow.
- To run a single playbook step explicitly, use `cafe workflow --start-step <step> --execute` (for example `cafe workflow --start-step spec --execute --user-input "..."`).

### Agent System
- **PM**: Clarifies requirements, avoiding technical details.
- **Developer**: Analyzes implementation and writes code.
- **Reviewer**: Reviews code for quality assurance.

You can create and manage custom agents using the `cafe agent` command set. Custom agents are stored globally in `~/.cafe/agents/` and can be reused across all your CAFE projects. See `cafe agent --help` for more details.

### Template System
- **Spec Template**: Defines the format for requirements clarification and specification documents.
- **Plan Template**: Defines the format for the implementation plan.
- **Review Report Template** (To be implemented): Defines the format for code review reports.
- **PR Description Template** (To be implemented): Defines the format for PR descriptions.

You can create and manage custom templates with the `cafe template` command set. Custom templates are stored globally in `~/.cafe/templates/` and can be reused across all your CAFE projects. When a custom template has the same name as a system template, the custom template takes precedence. See `cafe template --help` for details.

### Other Features

CAFE provides additional commands for managing issues and viewing execution details:

#### Project Setup
- `cafe init` - Initialize project settings and bundled default content
- `cafe setup` - Configure project settings (playbook, rigor, auto-update) in config.yaml
- `cafe skill sync-global` - Explicitly install, recover, or selectively update bundled workflow helper skills; configured Git hooks keep normal source updates synchronized automatically

#### Workflow Execution
- `cafe prepare` - Prepare issue environment (creates worktree, initializes config and git branch)
- `cafe make` - Execute the complete automated workflow from current phase to PR creation
- `cafe close` - Close current feature and return to base branch (syncs changes, removes worktree)

#### Monitoring & Control
- `cafe status` - Display a comprehensive timeline of all workflow phases, iterations, and execution statistics
- `cafe show` - Display iteration file contents (spec, plan, output, checklist, questions, error logs, etc.)
- `cafe chat <pm|developer|reviewer>` - Open interactive chat with a specific role agent (extremely useful for confirming details or making changes outside the spec)
- `cafe reset` - Rollback the previous iteration (CAFE's basic execution unit), useful for redoing work or reverting a mistaken confirm (note: does not revert git changes)

#### Issue Management
- `cafe ls` - List all CAFE issues with their worktree paths and current status
- `cafe restore` - Restore archived issues from backup (recover closed issues and chat with historical agents)
- `cafe rm` - Remove one or more issues and all their data without backing up (use with caution)

#### Customization
- `cafe config` - View and manage CAFE configuration settings
- `cafe agent create` - Create custom agents with specific behaviors and prompts tailored to your needs
- `cafe template create` - Create custom spec and plan templates for specialized workflows or domain-specific requirements

Use `cafe <command> --help` to see detailed usage for each command.

## Contributing
Contributions of any kind are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for more details.

## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

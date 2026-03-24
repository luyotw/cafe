# The CAFE Engine
## AI-Driven Development Workflow Automation

**Maximize your vibe coding—without losing control of your codebase.**

CAFE (CLI Agent Flow Engine) is an AI-driven development workflow automation system powered by headless CLI agents. Our goal is to help individual developers leverage AI agents more effectively while maintaining code quality and long-term maintainability. It automates the entire development lifecycle—from requirements analysis to PR generation—by orchestrating specialized roles such as PM, Developer, and Reviewer, while keeping the codebase structured, inspectable, and maintainable.

CAFE is actively evolving—we're continuously iterating based on real-world usage and feedback to improve stability, usability, and integration with various AI agents.

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

### Automatic Backup Agent Switching on Rate Limits

When a primary agent hits an API rate limit, CAFE automatically switches to backup agents in configured order—without stopping the workflow. If the backup agent also hits a rate limit, CAFE continues to the next backup until one succeeds or all are exhausted.

You can configure backup agents and their per-phase models in `.cafe/config.yaml`:

```yaml
agents:
  developer:
    name: David
    cli: claude                    # Primary CLI agent
    backup:                        # Backup agents (tried in order on rate limit)
      - gemini
      - copilot
    models:                        # Per-CLI, per-phase model configuration
      claude:
        plan: opus
        develop: sonnet
      gemini:
        plan: gemini-2.5-pro-preview
        develop: gemini-2-flash-preview
      copilot: {}                  # Use CLI default model
```

If all agents (primary + backups) are exhausted, the workflow stops with a clear error message listing which agents were tried. You can add more backup agents with `cafe config edit`.

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
- [GitHub Copilot CLI](https://github.com/features/copilot/cli)
- [Cursor CLI](https://cursor.com/zh-Hant/cli)
- [Gemini CLI](https://geminicli.com/)

Support for more CLI agent tools is planned for the future. Stay tuned!

> **Note**: CAFE leverages the **headless mode** of these CLI tools, which means CAFE operates without requiring interactive sessions or IDE integrations. The CLI tools execute commands and return results to CAFE for processing, enabling seamless automation of the entire development workflow.

## Installation

### From PyPI

```bash
pip install cafe-engine
```

### From Source

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

1.  **Initialize CAFE settings**:
    ```bash
    cafe init
    ```
    This command will guide you through selecting the Agent CLI tools and configuring the three roles (PM, Developer, Reviewer).

2.  **Start the development workflow**:
    ```bash
    # Initialize the issue environment
    cafe prepare
    ```

    Switch to the worktree path if set, then:
    ```bash
    # Start or continue the full automated workflow
    cafe make
    ```

3.  **Finalize and sync back to original branch**:
    You merge the PR manually on GitHub (or do nothing in local review mode), then:

    ```bash
    cafe close
    ```
    This command updates the original branch, removes the feature branch and worktree, and backs up related environment files.

5.  **Multiple CLI agents configuration**:
    You can adjust the CLI agent settings at any time using the `cafe config` command set. For example:

    ```bash
    cafe config set agents.developer.cli gemini
    cafe config set agents.developer.plan.model gemini-3-pro-preview
    ```

    To view the current configuration:
    ```bash
    cafe config
    ```

    Or to edit the configuration file directly in your default editor:
    ```bash
    cafe config edit
    ```

    > **Note**: In `worktree` mode, each worktree maintains an independent configuration, allowing for isolated agent settings per development task.

## Core Architecture

### 5-in-1 Workflow:

- The development process consists of five main phases:
  - **Phase 1: Requirements Analysis** - The PM agent clarifies requirements and writes specification documents.
  - **Phase 2: Implementation Analysis** - The Developer agent creates an implementation plan and breaks down tasks.
  - **Phase 3: Development** - The Developer agent implements the features and commits the code.
  - **Phase 4: Code Review** - The Reviewer agent reviews the code. If modifications are needed, the process returns to Phase 3.
  - **Phase 5: Create PR** - The Developer agent automatically creates a GitHub PR or allows the user to review locally. If there are suggestions for changes, the process returns to Phase 3.
- You can run the entire flow with a single command, `cafe make`, or resume an interrupted flow.
- You can also execute each phase separately with its corresponding command:
  - Phase 1: `cafe spec`
  - Phase 2: `cafe plan`
  - Phase 3: `cafe develop`
  - Phase 4: `cafe review`
  - Phase 5: `cafe pr`
  - Each of these phase commands can use the `--auto` flag to automatically proceed to the next phase.

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
- `cafe init` - Initialize CAFE configuration for the project
- `cafe setup` - Reconfigure agent roles (CLI, agent, and model assignments) with back navigation support

#### Workflow Execution
- `cafe prepare` - Prepare issue environment (creates worktree, initializes config and git branch)
- `cafe make` - Execute the complete automated workflow from current phase to PR creation
- `cafe close` - Close current feature and return to base branch (syncs changes, removes worktree)

#### Monitoring & Control
- `cafe summary` - Display a comprehensive timeline of all workflow phases, iterations, and execution statistics
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

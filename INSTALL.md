# Install CAFE with a CLI Agent

CAFE can be installed by any CLI agent that can inspect repository files and
run local commands. The user does not need to install a Codex, Claude, or other
vendor-specific plugin.

## What to tell your agent

Send this request to your CLI agent:

```text
Install the latest stable CAFE release from https://github.com/luyotw/cafe.
Follow INSTALL.md. I authorize the user-scoped changes described there.
Do not use sudo, do not modify system Python, and do not change my shell profile.
```

The authorization covers only the isolated CAFE environment, one user launcher,
and the detected user-level skill directories. It does not authorize changes
to system packages, provider credentials, unrelated launchers, or project files.

## Instructions for the CLI agent

1. Verify that the source is `https://github.com/luyotw/cafe` and resolve its
   latest stable release. Do not install an unreviewed fork or a moving branch.
2. Check out that exact release tag into a temporary or user-approved location.
   Inspect this file and `scripts/bootstrap-cafe.py` before execution. Do not use
   a `curl | sh` or equivalent remote-code pipeline.
3. Confirm that Python 3.10 or newer is available. Stop and explain the missing
   prerequisite instead of installing system packages or using `sudo`.
4. Show the deterministic plan without changing files:

   ```bash
   python3 scripts/bootstrap-cafe.py --dry-run
   ```

5. If the user's request already authorizes the documented user-scoped changes,
   install non-interactively:

   ```bash
   python3 scripts/bootstrap-cafe.py --yes
   ```

   Otherwise, obtain authorization before running this command.
6. Report the installed CAFE version, launcher path, skill synchronization
   result, and any missing prerequisite. Never request or modify provider API
   keys as part of this installation.

On Windows, use an available Python 3.10+ launcher to run the same Python script.

## What the bootstrap changes

The bootstrap:

- creates a versioned virtual environment under
  `~/.local/share/cafe-engine/environments/`;
- creates a CAFE-managed launcher in `~/.local/bin/`;
- verifies the installed package before publishing that launcher;
- runs `cafe skill sync-global`, which copies the bundled
  `use-cafe-workflow`, `write-cafe-agent`, `write-cafe-playbook`, and
  `write-cafe-phase` skills to
  user directories for detected Claude, Codex, Copilot, Cursor, and Gemini
  installations; and
- removes only the previous isolated environment recorded in CAFE's own install
  manifest after a successful upgrade.

The bootstrap does not use `sudo`, modify system Python, edit a shell profile,
install an agent CLI, or configure provider authentication. It refuses to
replace an existing `cafe` launcher that it does not manage.

If `~/.local/bin` is not on `PATH`, the installation remains usable through the
reported absolute launcher path. A CLI agent must ask separately before editing
a shell profile.

## Manual installation

Developers who manage their own Python environment can still install the
published package or a trusted source checkout directly:

```bash
pip install cafe-engine
```

```bash
git clone https://github.com/luyotw/cafe.git
cd cafe
pip install -e .
```

Run `cafe skill sync-global` after a manual installation to install the bundled
workflow helper skills for all supported CLI agents.

## Global workflow helper behavior

CAFE treats installing a missing helper and publishing a helper update as
different operations:

- Observational commands such as `status`, `show`, checks, lists, help, and a
  default `workflow` dry run never write user-level helper directories or sync
  metadata.
- An explicitly supported mutating command may install a missing managed helper.
  A released installation uses its packaged bundle; a Git linked worktree uses
  the canonical main checkout's bundle. Existing directories and symlinks are
  always left unchanged during startup.
- `cafe skill sync-global` is the explicit publication command. It can install,
  update, or confirm unchanged copies and reports the exact resolved source plus
  every CLI destination outcome. Run it deliberately when publishing helper
  changes from a feature worktree.

Global helper publication is separate from project/catalog comparison and its
approval token. Approval for `cafe catalog sync-global` does not authorize a
helper update, and helper synchronization does not publish project catalogs.

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

## GitHub authentication before a workflow

GitHub workflows need both authenticated GitHub API access through `gh` and
Git push access to the repository. A successful `gh auth status` does not prove
that Git can authenticate using this checkout's remote transport.

Check the existing configuration from the checkout that will run the workflow:

```bash
gh auth status --hostname github.com
git remote get-url origin
```

Use the authentication method that matches the remote:

- **HTTPS** (`https://github.com/OWNER/REPO.git`): Git needs an HTTPS credential
  helper. If you want Git to use your existing authenticated GitHub CLI account,
  run `gh auth setup-git --hostname github.com`. This changes your user Git
  credential-helper configuration for GitHub; an agent must obtain permission
  for that change separately from CAFE installation. If `gh` is not logged in,
  the user should first run `gh auth login --hostname github.com`.
- **SSH** (`git@github.com:OWNER/REPO.git`): Git needs an SSH key accepted by the
  intended GitHub account. The user can check authentication with
  `ssh -T git@github.com`; verify the account named in the response. GitHub's
  successful authentication response still exits with status 1 because it does
  not provide shell access. An authenticated account also needs write access
  to the target repository; reading a public remote alone does not prove that.

### Recover an HTTPS push that cannot prompt for credentials

An automated push may stop with:

```text
fatal: could not read Username for 'https://github.com': No such device or address
```

This can happen when the documented HTTPS clone is used on a machine with
working SSH authentication but no HTTPS credential helper. Either configure the
HTTPS helper above, or, if the user chooses to reuse existing SSH authentication,
change only this checkout's remote to the **same repository**:

```bash
# Replace OWNER/REPO with the owner and repository from the existing origin.
git remote set-url origin git@github.com:OWNER/REPO.git
git remote get-url origin
```

An agent must have authorization for the remote change; do not rewrite remotes
automatically, switch repositories, copy credentials, or put a token in a URL.
After fixing authentication, inspect `cafe status` and follow the existing
workflow recovery path. Preserve the failed attempt and resume the same
workflow rather than starting the issue over. Installation itself does not
configure either GitHub authentication method.

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

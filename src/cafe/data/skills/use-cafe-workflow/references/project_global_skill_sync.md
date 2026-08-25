# Project And Global Skill Consistency

Read this reference before starting or resuming workflow execution. The check is
read-only and prevents a linked worktree from silently using a stale
`~/.cafe/skills` copy when the canonical project has a newer `.cafe/skills`
override.

If Git uses a separate metadata directory and cannot identify the canonical main
worktree, the check fails closed. Re-run it as
`python3 <skill-dir>/scripts/project_global_skill_sync.py --project-root <canonical-main-worktree> check`;
when invoked from a linked worktree in the same repository, its active
`.cafe/skills` overlay is still applied. Keep that same working directory and
explicit canonical root for the approved update and post-update check.

## Check before execution

Run from the repository or issue worktree:

```bash
python3 <skill-dir>/scripts/project_global_skill_sync.py check
```

The script discovers the canonical main worktree through Git's common directory,
then overlays any `.cafe/skills` present in the active linked worktree. It compares
only skills that have a project version; global-only skills are outside this
check. Generated `__pycache__`, `.pyc`, `.pyo`, and `.DS_Store` entries do not
affect the digest.

Route by the JSON `status`:

- `identical`: continue without mentioning the check or asking the user.
- `no_project_skills`: continue without asking; there is no project/global pair
  to compare.
- `differences`: list every item using `skill`, `reason`, `project_version`, and
  `global_version`, retain the reported `comparison_token`, then ask one focused
  question: whether to update those global copies from the project versions. Do
  not update before the user explicitly agrees.

Do not treat a matching version string as equality. The tree digest, including
scripts, references, assets, symlink targets, and executable modes, is the
authoritative comparison.

## Apply an approved update

After the user approves the exact listed skills, pass each approved name:

```bash
python3 <skill-dir>/scripts/project_global_skill_sync.py update \
  --comparison-token <token-from-the-approved-check> \
  --skill cafe-example-one \
  --skill cafe-example-two
```

When the initial check required the fail-closed fallback, run the approved
update and verification from the same linked-worktree directory:

```bash
python3 <skill-dir>/scripts/project_global_skill_sync.py \
  --project-root <canonical-main-worktree> update \
  --comparison-token <token-from-the-approved-check> \
  --skill cafe-example-one
python3 <skill-dir>/scripts/project_global_skill_sync.py \
  --project-root <canonical-main-worktree> check
```

The token binds the update to the exact project/global digests shown to the user;
any intervening content change fails closed and requires a new check and answer.
The update is a replacement of only those global skill folders under a bounded
10-second lock, with rollback backups retained if automatic restoration cannot complete. It does not
modify the project copy or CLI-native installed skill directories.
Re-run `check` afterward. Continue only after the approved names no longer appear in
`differences`; if the user declines an update, preserve the global copies and
record that explicit choice before workflow execution.

Never infer approval from kickoff confirmation, a generic `continue`, or an old
sync decision. A newly detected content difference requires a new explicit
answer.

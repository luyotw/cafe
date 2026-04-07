---
name: release
description: Automate the cafe-engine release process — bump version, update CHANGELOG.md, commit, merge to main, tag, create GitHub release with auto-generated notes, and push.
user_invocable: true
allowed-tools:
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - Bash
  - AskUserQuestion
---

# Release Skill

## Purpose

Automate the full release workflow for cafe-engine. Handles version bump, changelog, git tagging, GitHub release creation, and push.

## Arguments

- `$ARGUMENTS` — optional: `patch`, `minor`, `major`, or an explicit version number (e.g., `0.1.5`).

## Pre-flight Checks

Before starting, verify ALL of the following. If any check fails, stop and report the issue:

1. Current branch is `develop`
2. Working tree is clean (`git status` shows no uncommitted changes)
3. `develop` is up to date with `origin/develop` (run `git pull` if needed)

## Version Selection

1. Read the current version from `pyproject.toml` (e.g., `0.1.4`)
2. If `$ARGUMENTS` is `patch`, `minor`, or `major`, calculate the new version automatically
3. If `$ARGUMENTS` is an explicit version number, use it directly
4. If `$ARGUMENTS` is empty, present the three options to the user and ask them to choose:

```
Current version: 0.1.4

  patch → 0.1.5  (bug fixes, small changes)
  minor → 0.2.0  (new features, backward compatible)
  major → 1.0.0  (breaking changes)

Which release type?
```

5. After determining the version, verify:
   - The new version is higher than the current version
   - The tag `v{version}` does not already exist

## Release Steps

### Step 1: Generate Release Notes

1. Find the latest existing tag (e.g., `v0.1.4`)
2. Run `git log {latest_tag}..HEAD --oneline` to get all commits since the last release
3. Categorize commits by their conventional commit prefix:
   - `feat:` → **New Features**
   - `fix:` → **Bug Fixes**
   - `refactor:` → **Refactoring**
   - `test:` → **Tests**
   - `docs:` → **Documentation**
   - `chore:` → **Chores**
   - Merge commits → skip
4. Draft the release notes in markdown format, grouped by category
5. Show the draft to the user and ask for confirmation before proceeding

### Step 2: Bump Version

1. Update `version` in `pyproject.toml`
2. Run `uv lock` to sync `uv.lock`

### Step 3: Update CHANGELOG.md

1. Add a new section at the top (below the header) for the new version
2. Use the Keep a Changelog format (### Added, ### Fixed, ### Changed, ### Removed)
3. Update the comparison links at the bottom of the file

### Step 4: Commit Version Bump

```
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore: bump version to {version}"
```

### Step 5: Merge to Main

```
git checkout main
git pull origin main
git merge develop
```

If there are merge conflicts, stop and ask the user for help.

### Step 6: Tag and Push

```
git tag v{version}
git push origin main
git push origin v{version}
```

### Step 7: Create GitHub Release

```
gh release create v{version} --title "v{version}" --notes "{release_notes}"
```

Use the release notes generated in Step 1.

### Step 8: Return to Develop

```
git checkout develop
git merge main
git push origin develop
```

## After Completion

Report a summary:
- Version: old → new
- GitHub release URL
- Number of commits included
- Categories breakdown (X features, Y fixes, etc.)

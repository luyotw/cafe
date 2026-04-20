---
name: github_sync
description: Shared GitHub sync utilities for other skills.
version: 1.0.0
---

# GitHub Sync Shared Skill

## Purpose
- Provide a single implementation for spec/plan GitHub sync behavior.
- Keep calling skills lightweight by delegating to shared scripts.

## Available scripts
- `scripts/sync_github.sh` — Sync confirmed spec/plan output to GitHub issue comment when enabled

```bash
bash scripts/sync_github.sh --help
```

## Notes
- This skill is intended to be invoked by other skill-local wrapper scripts.
- Keep CLI/JSON contract stable because multiple skills depend on it.

---
name: cafe-github_sync
description: Shared GitHub sync utilities for other skills.
version: 1.0.0
---

# GitHub Sync Shared Skill

## Purpose
- Document the runtime-owned confirmed spec/plan GitHub sync behavior.
- Keep phase agents outside the host capability authority path.

## Runtime contract
- The trusted workflow runtime invokes `cafe.github.issue_comment` only from its
  fixed `after_execute` + `confirmed` gate for spec and plan artifacts.
- Phase agents write the confirmation baton only. They must not invoke a sync
  script or construct a capability request.

## Compatibility
- `scripts/sync_github.sh` and skill-local wrappers remain for compatibility
  testing and migration only. They are not an authority path and must not be
  executed by phase agents.

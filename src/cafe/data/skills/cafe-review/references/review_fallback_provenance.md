# Upstream provenance

- Source: `anthropics/claude-plugins-official`
- Component: `plugins/pr-review-toolkit/agents/code-reviewer.md`
- License: Apache-2.0; see `review_fallback_LICENSE.md` in this directory.
- Pin and source digest: `../assets/review_fallback_upstream.json`
- Maintainer command: `python scripts/update_review_fallback.py`

CAFE records the exact digest of the pinned upstream source, then deterministically strips its frontmatter and normalizes provider-specific project-guidance names into `review_procedure.md`. Only that provider-neutral procedure is installed as executable review guidance. The wrapper deliberately changes invocation, scope ownership, conditional lenses, output authority, and handoff behavior. An upstream update does not weaken or replace the outer `cafe-review` acceptance and risk contract.

Run `python scripts/update_review_fallback.py` from this skill directory to check the normalized procedure delta. After inspecting the complete delta, copy the exact `target` and `target_source_sha256` values into the printed `--ref <revision> --expect-source-sha256 <digest> --apply` command. The updater refuses mutable apply refs, truncated deltas, locally modified procedures, and incompatible upstream structures; normal repository review and tests remain required before committing an applied update.

# Upstream provenance

- Source: `anthropics/claude-plugins-official`
- Component: `plugins/pr-review-toolkit/agents/code-reviewer.md`
- License: Apache-2.0; see `LICENSE.md` in this directory.
- Pin and source digest: `../assets/upstream.json`
- Maintainer command: `cafe skill update-review-fallback`

CAFE records the exact digest of the pinned upstream source, then deterministically strips its frontmatter and normalizes provider-specific project-guidance names into `review_procedure.md`. Only that provider-neutral procedure is installed as executable review guidance. The wrapper deliberately changes invocation, scope ownership, conditional lenses, output authority, and handoff behavior. An upstream update does not weaken or replace the outer `cafe-review` acceptance and risk contract.

Use `cafe skill update-review-fallback` to check the normalized procedure delta. Add `--apply` only after inspecting that delta. The updater refuses locally modified procedures and incompatible upstream structures; normal repository review and tests remain required before committing an applied update.

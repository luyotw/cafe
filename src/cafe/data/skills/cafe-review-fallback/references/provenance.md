# Upstream provenance

- Source: `anthropics/claude-plugins-official`
- Component: `plugins/pr-review-toolkit/agents/code-reviewer.md`
- License: Apache-2.0; see `LICENSE.md` in this directory.
- Pin and source digest: `../assets/upstream.json`
- Maintainer command: `cafe skill update-review-fallback`

CAFE keeps the upstream reviewer text as a pinned snapshot and wraps it with a provider-neutral skill. The wrapper deliberately changes invocation, scope ownership, project-guidance naming, conditional lenses, output authority, and handoff behavior. An upstream snapshot update does not weaken or replace the outer `cafe-review` acceptance and risk contract.

Use `cafe skill update-review-fallback` to check the current upstream delta. Add `--apply` only after inspecting that delta. The updater refuses locally modified snapshots and incompatible upstream structures; normal repository review and tests remain required before committing an applied update.

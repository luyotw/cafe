# Migrating hybrid workflow steps

`assignee_type: hybrid` is deprecated. Existing custom playbooks remain valid
until the next breaking release, but new workflows should model every ownership
boundary as an ordinary top-level step.

## Why

Hybrid steps add a second portion cursor and a private baton beneath the normal
playbook graph. The same workflow can be expressed with ordinary steps while
keeping ownership, HumanTask state, retry limits, and resume behavior visible in
one graph.

## Migration

Replace a hybrid step like this:

```yaml
mixed:
  assignee_type: hybrid
  hybrid:
    entry_portion: draft
    portions:
      - id: draft
        owner: agent
        on: {await_agent: {portion: approve}}
      - id: approve
        owner: human
        on: {accept: {portion: finalize}}
      - id: finalize
        owner: agent
        on: {await_agent: {step: next}}
```

with explicit steps:

```yaml
draft:
  assignee_type: agent
  on: {await_agent: approve}

approve:
  assignee_type: human
  human_tasks:
    - trigger: initial
      task_id: approval
      outcomes: {accept: finalize}
  on: {}

finalize:
  assignee_type: agent
  on: {await_agent: next}
```

Give each new agent step its own output artifact and checklist when the old
portions produced independently reviewable work. If both portions update the
same workspace, they may keep the same workspace artifact name while retaining
separate iteration histories.

Run strict validation after migration:

```bash
cafe playbook validate <playbook-id> --strict
```

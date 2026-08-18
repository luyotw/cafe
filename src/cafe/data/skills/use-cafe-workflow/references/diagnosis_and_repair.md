# Bounded Diagnosis And Repair

Read this reference when a workflow command, handoff, phase, or state transition
behaves incorrectly. Diagnose only far enough to classify the failure and
choose a safe disposition. Bound inspection to the failing command, active
playbook and step, supplied artifacts, blackboard and baton state, relevant
sanitized logs, and installed CAFE version.

## Classification checklist

- [ ] Reproduce read-only or with a safe focused `--single-step` run.
- [ ] Rule out project configuration, malformed project artifacts, stale
  installed skills, CLI/model mismatch, transient provider/network failures,
  rate limits, and an agent failing an otherwise valid contract.
- [ ] Choose exactly one disposition:
  - **Playbook declarative defect:** wrong graph, artifact binding, intent,
    hook/tool declaration, or planned confirmation gate.
  - **Phase declarative defect:** wrong phase/shared/chat skill contract,
    placeholder, route, or supporting skill resource.
  - **Driver or CAFE core defect:** `use-cafe-workflow`, CLI/runtime Python,
    workflow state machinery, or host execution.
  - **Unconfirmed or transient:** evidence does not distinguish product behavior
    from environment, project, provider, or agent behavior.

Do not turn a bounded incident into open-ended framework refactoring.

## Declarative repairs

For a playbook defect:

- activate `write-cafe-playbook`;
- edit only `.cafe/playbooks/`, or `src/cafe/data/playbooks/` when the authorized
  repository is CAFE;
- run its strict validation;
- if confirmation gates changed, rerun
  `cafe playbook confirmation-gates <id>` and reconfirm the kickoff contract.

For a phase defect:

- activate `write-cafe-phase`;
- edit only `.cafe/skills/`, or `src/cafe/data/skills/` when the authorized
  repository is CAFE;
- run its strict validation.

Never patch generated artifacts, installed packages, or global skill copies as
the source fix. Commit bundled source changes in the CAFE repository; configured
hooks synchronize installed copies. CLI startup performs a per-machine
fingerprint repair. If synchronization fails, recover explicitly with:

```bash
cafe skill sync-global
```

Do not use writer skills to change driver/meta skills, CAFE runtime Python,
workflow state machinery, or host infrastructure. Do not invent a
`write-cafe-driver` skill.

## Driver and core defects

Do not self-modify a driver or core defect unless the user explicitly authorizes
that source change. Stop before an unsafe or contract-bypassing workaround and
recommend following or opening an issue at
<https://github.com/luyotw/cafe/issues>.

Before recommending a new issue:

- [ ] Search open and closed issues read-only for an existing match.
- [ ] If none exists, prepare a sanitized draft containing CAFE version,
  CLI/model, playbook/step/intent, exact command, expected and actual behavior,
  minimal reproduction, relevant logs, and any safe workaround.
- [ ] Remove credentials and private project data.
- [ ] Do not create, comment on, or close an upstream issue without explicit
  user authorization.

For unconfirmed or transient failures, retry once when safe or ask one focused
diagnostic question. Continue through a workaround only when it is reversible,
within mandate, preserves the kickoff contract, and the user has been informed.

# Driver-Owned Alignment

CAFE's bundled workflows delegate semantic alignment to the
`use-cafe-workflow` driver. The driver has the conversational context needed to
compare the newest proposal delta with `.cafe/strategic_context.yaml`, the
relevant strategic documents, and the current issue artifacts.

Bundled playbooks omit `alignment:` configuration. The globally registered
`AlignmentCheckpointGate` therefore runs as an inactive compatibility hook and
does not pause a normal bundled workflow because keywords such as `roadmap`,
`scope`, `positioning`, or `external mutation` appeared in accumulated
artifacts.

Alignment is separate from other user stops:

- Clarification asks for missing requirement or implementation facts.
- Permission asks whether an external, destructive, privileged, or otherwise
  user-owned action may run.
- Alignment asks whether a concrete proposal delta contradicts or extends
  confirmed strategy, or requires a strategic choice.

## Driver Decision

The driver evaluates alignment during kickoff, before driver-confirming spec or
plan, and whenever a correction changes strategic scope. It does not repeat the
check for unchanged scope during develop, review, or PR.

The decision uses four pieces of evidence:

- `proposal_delta`: the concrete new or changed scope
- `strategic_ground`: the governing document section, mandate axis, or
  out-of-mandate item
- `mandate_level`: the resolved `agent`, `propose`, or `escalate` level for the
  governing axis
- `relation`: `within`, `contradicts`, `extends`, `missing_ground`, or
  `uncertain`

`within` proceeds without asking only at `agent` level. `propose` follows the
playbook's recommendation flow, and `escalate` stops for the user even when the
proposal fits existing documents. Every non-`within` relation also stops for one
focused alignment question. Incidental keyword mentions, non-scope statements,
generated boilerplate, phase names, and accumulated artifact history are not
sufficient evidence.

## Core Compatibility

CAFE core retains the `alignment_checkpoint` status, user-owned handoff
contract, request files, JSON decision handling, and the opt-in heuristic gate
for legacy or explicitly configured custom playbooks. The heuristic may propose
a checkpoint; the workflow driver still owns final semantic classification.

A custom playbook can still opt into `AlignmentCheckpointGate` with an
`alignment:` step block. When that happens, the workflow driver treats the
generated request as compatibility evidence and applies the same driver
classification before deciding whether the user is actually needed.

Required core checkpoints write:

- `.cafe/issues/<issue>/<step>/iteration_NNN/alignment_request.json`
- `.cafe/issues/<issue>/<step>/iteration_NNN/strategic_document_update_request.json`
  when a strategic document update is required

Plain `--user-input` text never approves a core checkpoint. A driver-resolvable
legacy/custom checkpoint uses an explicit JSON payload:

```json
{"decision":"approve","reason":"Within confirmed roadmap and mandate."}
```

Strategic document updates still require explicit user confirmation unless they
are mechanically copied or split from already confirmed material.

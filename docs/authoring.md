# Source contract authoring

CAFE provides a deterministic, noninteractive authoring API for phase skills and
playbooks. Authors supply domain procedure, artifact selection, routing and
permission decisions. The helper constructs canonical phase structure and checks
the staged candidate with the same runtime declarations, composition, primary
step defaults, strict validators, confirmation-gate classifiers and graph
simulator used by CAFE. Preview never executes authored hooks or agents.

## Preview and apply

A complete executable example is
[`tests/fixtures/authoring/pair.yaml`](../tests/fixtures/authoring/pair.yaml).
It declares a project phase and a binding playbook in one transaction. Copy the
fixture to a request file in the project to author a new workflow:

```bash
cafe playbook author --spec pair.yaml --dry-run --format json
cafe playbook author --spec pair.yaml --apply --expect-change <change_digest> --format json
```

For an independent phase request, take the fixture's companion request:

```bash
cafe skill author phase --spec phase.yaml --dry-run --format json
cafe skill author phase --spec phase.yaml --apply --expect-change <change_digest>
```

`--spec -` reads YAML or JSON from stdin. Select exactly one of `--dry-run` and
`--apply`; omitted or contradictory modes fail with exit 2. `--format text|json`
selects a readable diff/report or exactly one JSON object on stdout. Exit 0 means
a valid preview or successful/no-op apply; exit 1 means rejected input,
validation, confinement, stale preview or publication failure. Errors in JSON
mode are included in the result. Explicit `--apply` without a digest applies
only the supplied request as currently validated. The helper-first workflow
uses the digest to bind publication to the reviewed preview.

## Version 1 request

Every request contains `version: 1`, an exact repository-relative `target`, and
`mode: create|patch`. Unknown keys and duplicate YAML/JSON keys are rejected.
Requests are bounded to 1 MiB and transactions to 64 files, including resources.
A create request provides `declaration`; a patch provides `operations`. Only a
playbook-level transaction or independent phase may carry nonnested
`companions`; all targets must be distinct. Runtime fields inside declarations
are validated by runtime models; `request_schema()` exposes the envelope and
current runtime model JSON schemas.

A phase target is `.cafe/skills/<name>/SKILL.md`. Its declaration includes
`name`, `description`, `version`, and `workflow.execution_profile`. Supply
nonempty author-owned `sections.Instructions`, `sections.Output` (including
`{output_file}`), and `sections.Handoff`. Optional `Title` and `Context` are
copied verbatim. The helper supplies the canonical Role sentence and section
order. `references` maps explicitly declared `references/*.md` paths to supplied
content. Workflow reference declarations must resolve to real supplied or
existing files. Undeclared placeholders, incompatible policies and incomplete
Todo declarations are rejected; the helper does not generate domain procedure.

A playbook target is `.cafe/playbooks/<id>.yaml`. `declaration` is a complete
runtime PlaybookDefinition payload, including applicability, locale, roles,
explicit workflow/chat environments, entry point, steps and prepare behavior.
The fixture shows a minimal strict candidate. Playbook authoring may include
phase create/patch companions, so both sides validate before either publishes.
Runtime-owned metadata is never invented for output/handoff authoring intent.

## Bounded operations

Paths are arrays of named mapping keys. Ordinal edits, implicit deletion and
rename propagation are unsupported. For phase metadata, prefix a YAML path
with `metadata`. For playbooks use the runtime field path directly.

```yaml
version: 1
target: .cafe/playbooks/fieldwork.yaml
mode: patch
operations:
  - op: upsert
    path: [steps, observe, allowed_goto]
    value: observe
```

The example adds a deliberate recovery route and is an executable supported
patch after applying the pair fixture. The same operation supports scalar-token
lists such as `input_artifacts`, `allowed_tools`, required tools, overlays and
hook names. For keyed collections such as phase `prompt_inputs` or
`human_tasks` and step `human_tasks`, supply `key: placeholder|id|task_id` and a
complete value with that identity. Adding a step or transition uses its exact
mapping-key path, e.g. `[steps, inspect]` or `[steps, observe, on, await_agent]`.
Artifact producer fields, hooks and checklist containers without stable member
keys can be added as a bounded mapping field or explicitly replaced.

An already-satisfied upsert is a no-op. Conflicting existing identities are
errors. Replacement requires all of:

```yaml
- op: replace
  path: [steps, observe, allowed_tools]
  expected: []
  overwrite: true
  value: [Read]
```

For phase prose, use `[sections, Instructions]` and an exact expected section
body. For resources use `[references, references/evidence.md]` and expected old
content (`null` for an explicitly new resource). Each preserves all other bytes.
Replacement is idempotent once the exact desired content is present.

## Preservation and manual exceptions

Only declared YAML spans or exact named Markdown sections change. All bytes
outside those spans survive. A targeted container replacement with comments,
anchors, aliases, merge keys, duplicate keys, nonempty flow collections or
ambiguous boundaries fails closed. Use a careful manual source edit for these
unsupported shapes, then run strict skill/playbook validation, gate inspection
and simulation. Preview diagnostics identify the rejected operation; do not
retry with an unrestricted replacement or whole-document formatter. Unrelated
phase Instructions/Output prose and other resources remain author-owned.
Focused edits retain uniform LF or CRLF line endings. Mixed line endings and
bare CR sources are rejected before writes. Resource replacements compare
and publish the exact explicitly supplied content, including its line endings.

## Reports and authority

Versioned results contain `version`, `operation`, `status`, `changes`, `diff`,
`diagnostics`, `proposals`, `artifact_summary`, `transition_summary`,
`confirmation_gates`, `simulation`, and `change_digest`. Diagnostics include
code, severity, target, skill, step, field, message and remedy; unavailable
context is null. Stable repository-relative diff paths have no timestamps or
temporary locations. Proposals are advisory: copy an accepted operation into
an explicit request and preview it again.

All primary iteration candidates and resolved workflow shared/role/step
contributors participate. Required inputs need reachable declared producers or
entry input. An omitted `input_artifacts` retains runtime full-source visibility;
an explicit list restricts candidates, and `[]` exposes none. Serial artifact bridges are
allowed; terminal unconsumed reports are informational. Missing tools,
conflicting bindings/policies, invalid HumanTask outcomes, incomplete Todo
contracts, unreachable/dead-end steps and missing declared intent handlers block
publication. Deliberate graph cycles are reported rather than globally banned.

Effective authority and before/after assignable/mandatory gate sets are
reported. Defaults or skill selection may not silently import tools,
capabilities, hooks, publication behavior or gate ownership: changing an
authority field requires explicit declaration in the current request.
Declarations never grant credentials, host capability approval, permission to
mutate existing issues or user decision authority. Mandatory HumanTasks remain
human-owned. Gate changes flag potentially stale stop contracts without
changing issue state.

## Publication and recovery

Writable targets are the explicitly selected current project `.cafe/skills`
and `.cafe/playbooks`, or versioned `src/cafe/data/skills` and
`src/cafe/data/playbooks` in the selected CAFE source repository. Identity guards
are not permission grants. Absolute/escaping paths, symlinks, installed/native
copies, global catalogs, issue artifacts and blackboard/state locations are
refused. Existing worktree/canonical/global/builtin resolver precedence and
aliases are retained; ineffective shadowed builtin edits are diagnosed.

Preview uses temporary source views and performs no persistent writes or
catalog synchronization. Apply acquires a private per-repository lock outside
source catalogs, re-prepares, checks source/dependency digests and stages the
complete write set in a durable bounded recovery journal. Publication uses
atomic per-file replacement with original modes, then revalidates published
sources. Handled failures roll back the entire declared set. Interrupted
transactions are recovered before a later apply; preview reports pending
recovery without changing files. Ambiguous/concurrent changes stop recovery
and retain evidence rather than overwriting them. Incomplete rollback is a
failure. This does not promise instantaneous multi-file visibility to unrelated
readers or protection against arbitrary hardware failure. No active-workflow
lock, migration daemon or public recovery command is introduced.

## Python API

```python
from pathlib import Path
from cafe.authoring import decode_request, prepare, apply, request_schema

request = decode_request(Path("pair.yaml").read_text())
preview = prepare(request, root=Path.cwd())
if preview.status == "ready":
    result = apply(request, root=Path.cwd(), expect_change=preview.change_digest)
    print(result.to_dict())
```

`prepare` is read-only. `apply` shares the same candidate preparation and returns
`applied`, `noop` or `rejected`. Neither API starts a workflow, synchronizes a
catalog or executes hooks. Inspect diagnostics and report unsupported manual
operations alongside successful authoring evidence.

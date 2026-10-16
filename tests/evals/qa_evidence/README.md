# Manual QA evidence interpretation evaluation

This is one-time model validation for issue #647, separate from automated skill
loading/checklist/routing tests. It is not an installed skill, runtime validator,
repository PR gate, or task run at every CAFE phase. The consolidation reuses the
completed behavior executions; its byte/hash checks do not replace semantic
assessment or claim a measured reliability improvement.

## Retained evidence

| Files | Contents and boundary |
| --- | --- |
| [originals/](originals/README.md) | Ten unchanged files: source notes plus nine supplied original reports, checklists, artifacts, provenance and historical browser/SDK sources. |
| [historical-cases.md](historical-cases.md), [historical-rubric.md](historical-rubric.md) | Full original reports, complete fixture and historical SDK lines 155–175; separate assessor-only criteria for A1/A2/B. |
| [scope-cases.md](scope-cases.md), [scope-rubric.md](scope-rubric.md) | Isolated sufficient-evidence and scope controls C–H; separate assessor-only criteria. |
| [original-results.md](original-results.md) | All ten original-source tested-role answers and four assessor judgments, verbatim and directly readable; includes both initial Manager answers and both initial assessments. |
| [original-provenance.json](original-provenance.json) | Byte ranges/hashes, all five reconstructed metadata documents, anonymous mappings, exact assessor-input recipes, shared profiles and policy/rerun linkage. |
| [preliminary-results.md](preliminary-results.md) | Preliminary packet, rubric, four tested-role answers and assessor judgment, verbatim; explicitly not original-source acceptance. |
| [preliminary-provenance.json](preliminary-provenance.json) | All three preliminary metadata documents, byte identities, mapping, shared profiles and exact preliminary assessor-input recipe. |

Archive section headings and metadata keys retain the reviewed source identities.
They identify historical Git blobs, not live paths to open. The provenance's
`texts` entries give UTF-8 byte offsets, lengths and SHA256 in the companion
corpus. Extract those byte ranges without stripping whitespace or final newlines.
`metadata` retains original serialized length/hash and a factored JSON value:
`$record` overlays its per-invocation fields on `profiles.record`; `$command`
fills `--cd` and `--output-last-message` paths in `profiles.command`. Everything
else is literal. `load_archive()` in [run_eval.py](run_eval.py) performs this
lossless expansion. Original JSON formatting is identified by its original hash;
all reconstructed JSON values equal the reviewed objects in full.

Each archive stores the repeated command/config/event data once, retaining every
invocation's differing paths, durations, hashes, role, variant, group and round.
The common startup diagnostic about disabled code-mode-host remains recorded; it
was not a terminal failure, evidence of a test execution, or permission to enable
tools. The self-contained assessor recipes retain the exact differing task
prefaces, rubrics, packets, anonymous answer order and answer bytes, excluding
policies and version mappings from the assessor prompt. All five original
assessor inputs were available during consolidation and matched their committed
completion hashes; no assessor-input gap is hidden or inferred from a final
judgment alone.

## Pinned inputs and replay

| Input | Exact Git source |
| --- | --- |
| Baseline policies and unchanged Quinn/regression inputs | `a5d0413f8f10e8a52690bb275ed53fcbb689cb7b` |
| Initial candidate Manager, including preliminary Manager | `4155ea671e28f74f5fe80763b8f86073afd1491d` |
| Equivalent initial Manager with original packets added | `c3d7569f5636cf30b2f79b26268ee3cbbee030e7` (same Manager bytes) |
| Final evaluated candidate, candidate QA/execution checklist and original/scope packets/rubrics | `ca948f59a94129db7875a7f4dcd0d9e7db253a43` |
| Preliminary packet/rubric | Same reviewed commit's historical blobs, also preserved verbatim in the preliminary archive |

QA receives the skill body, unchanged regression checklist and Quinn role; the
candidate additionally receives its declared execution checklist. Manager receives
only the existing due-phase review excerpt, including eligibility and snapshot
boundaries. `policy_sha256` identifies the full source policy file, as in the
original records; `effective_policy_sha256` additionally identifies the assembled
policy/excerpt. Tested-role prompts contain no rubric, expected answers, version
labels, complete blackboard, historical session or streaming archive. These are
current-skill evaluations against historical evidence, not reconstructions of
the original kid-todolist agent prompt.

The replay CLI reads exact Git objects, never ambient checkout copies. It
materializes each policy/common/packet input with its commit and hash in a fresh
chosen directory, writes `prompt.txt` and `inputs.json`, and verifies the final
prompt hash against the retained execution before calling any provider. Historical
and scope packets remain isolated. From any working directory, invoke the script
by its repository path; from the repository root, for example:

```bash
CAFE_QA_EVAL_DIR=/tmp/issue647-historical-candidate-qa \
  python tests/evals/qa_evidence/run_eval.py candidate qa --packet historical --prepare-only
CAFE_QA_EVAL_DIR=/tmp/issue647-scope-initial-manager \
  python tests/evals/qa_evidence/run_eval.py initial manager --packet scope --prepare-only
CAFE_QA_EVAL_DIR=/tmp/issue647-preliminary-manager \
  python tests/evals/qa_evidence/run_eval.py candidate manager --packet preliminary --prepare-only
```

Choose `baseline` or `candidate` and `qa` or `manager` for either original-source
packet. `initial manager` replays the pre-repair Manager on either original-source
packet; preliminary `candidate manager` already uses that initial policy.
`--prepare-only` invokes no model. To deliberately run a new finite model review,
use a fresh directory and omit that option. Exact input identity does not promise
an identical stochastic answer. Do not overwrite the committed observations or
represent new answers as those previously assessed.

The confirmed provider remains `codex`, `gpt-6.1-sol`, `high` reasoning, with the
same explicit command restrictions as the retained invocations: ephemeral session,
empty working directory, read-only sandbox, document loading disabled, no user
configuration/rules, shell/search/MCP/apps/plugins/hooks/memories/delegation or
other tool features. The existing provider loop forwards structured progress,
requires a nonempty durable answer, explicit `turn.completed`, process exit 0 and
no observed tool use separately. Preserve incomplete runs as incomplete. The
optional runner's blocking partial-line deadline limitation remains the separate
non-blocking FUP-001; the consolidation does not claim to fix or certify that
bound. All retained runs finished normally below 240 seconds.

To materialize an exact archived blinded assessor input without invoking a model:

```bash
CAFE_QA_EVAL_DIR=/tmp/issue647-historical-assessor \
  python tests/evals/qa_evidence/run_eval.py --packet historical --assessment final
```

Use `initial` for either original-source packet's initial assessment, or
`--packet preliminary --assessment final` for the preliminary judgment. This
reconstructs the previously assessed answers, not fresh outputs from a new role
replay. A fresh evaluation needs fresh anonymous ordering/mapping outside the
assessor input and an independent equally restricted assessor; read its reasoning
against the actual answers instead of using keyword counts.

For consolidation identity only (no model invocation):

```bash
python tests/evals/qa_evidence/run_eval.py --verify-archive
```

This compares all 21 extracted text payloads and eight reconstructed metadata
objects with reviewed HEAD, reconstructs all 14 tested-role prompts and five
assessor prompts, and checks their retained hashes. Full historical Git objects
must be available locally; missing pinned objects are errors, not permission to
use a later checkout. The targeted public-CLI tests additionally verify pinned
materialization and exact provider restrictions with a fake external executable.

## Observed results and repair linkage

The nine supplied originals were verified by length/SHA256; both report hashes
match their historical artifact metadata. The fixture is identical at both QA
HEADs and the later eea7a862 source. The historical schedule test is identical at
QA HEADs c9d57861/416f6ace but differs from the later version; the historical input
uses the original version. See the unchanged source notes for provenance.

The historical packet hash is
`db83b88aec27a8d80f49958bccc82a1b2cc3803a249fba2ec720ce8b28463558`;
the separate scope packet hash is
`3543aa31f8a6da83be71eeb3878ca8b890e2d1926538fcd82b19f088dffb86e5`.
Every compared policy receives the same finite packet and permissions within its
group. C–H contain controlled synthetic observations, not renewed product tests.

| Final control | Candidate QA / Manager | Supported judgment |
| --- | --- | --- |
| A1: original first-stage report | Pass / Pass | Retain real SDK/Rules and local fixture evidence; identify the pre-opened gate and missing connected Family/edit/save/parent-return journey. |
| A2: original correction | Pass / Pass | Accept narrow renewal/owner-transfer success and justified local reuse; preserve inherited journey and delivery gaps. |
| B: original reorder SDK case | Pass / Pass | Distinguish the successful single-sided move from the untested interior two-neighbor branch; request evidence without inventing a cost diagnosis. |
| C: sufficient connected App/emulator | Pass / Pass | Accept stage results without extra OAuth/cloud/iOS/full-site gates. |
| D–F: backend/presentation/document | Pass / Pass | Match API/rendered/artifact evidence and valid persistence reuse. |
| G: staged delivery | Pass / Pass | Accept completed engineering while deferred capabilities remain incomplete. |
| H: observed in-stage failure | Pass / Pass | Reject completion and use existing correction handling. |
| Legitimate immediately advancing not_required | N/A / Pass | No proactive review, new pause, implementation intervention or blanket retesting. |

Both baseline roles missed B's specific branch. The initial candidate Manager
also missed B, focusing on task count/origin without requiring both neighbors.
Its historical initial assessment records that failure. The scope initial
Manager and its assessment are retained too: that assessment already passed C–H
and not_required; it must not be relabeled a failure. The final policy at reviewed
HEAD refined the existing due-review paragraph to compare combinations of
populated payload fields and prerequisite states against ordinary-use branches.
The affected candidate Manager was then rerun on **both unchanged packets**, with
fresh blinded assessments. Final results pass all applicable controls. Exact
unchanged QA/baseline prompts and responses were carried forward. Both anonymous
initial/final mappings, the failed historical answer, scope initial success and
all final judgments remain visible in the corpus/provenance.

The 14 original-source invocations comprise ten tested-role responses and four
assessments. All exited 0, emitted turn.completed, produced durable answers and
showed no tool use. Final historical QA durations were 64.87/51.76 seconds and
Manager 47.15/50.32; scope QA 42.86/48.68 and Manager 35.44/42.54
(baseline/candidate). Complete initial/assessor durations remain in provenance.
These finite observations establish no general failure rate, speedup, reliability
gain or historical Rules root cause.

## Preliminary characterization boundary

The preliminary packet hash is
`d1f34b128dd8328a4f35ab719370d25717dae6e8a5c794281be058291aea2381`.
Its report characterization came from issue input before original reports were
available; its source excerpts use the later eea7a862 version. Four tested-role
responses and one independent assessment remain separately archived. The assessor
passed candidate QA (R4) and Manager (R1), while baseline QA (R2) failed A/B and
baseline Manager (R3) failed A. All five completed normally with no observed tool
use; four role durations were 56.77/75.02 seconds for QA and 48.54/51.67 for
Manager. The preliminary candidate Manager is the pre-repair policy, not final
reviewed Manager. Cross-case contrasts could assist judgment; this preliminary
batch does not satisfy original-report acceptance or estimate future behavior.
Earlier pilots were excluded while common context/source annotations were being
finalized; none was used as a scored response or supplied to the tested model.

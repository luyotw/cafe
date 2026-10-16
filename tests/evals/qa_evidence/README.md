# Manual QA evidence interpretation evaluation

This is one-time model validation for issue #647, separate from automated skill
loading/checklist/routing tests. It is not installed as a skill, a runtime
validator, a repository PR gate, or a task run at every CAFE phase.

Use the same chosen case packet, model/effort, and tool permissions with anonymous baseline
and candidate policies. Give only that packet and the relevant policy to each test
invocation. Keep `rubric.md` and policy-version labels out of those prompts. Assess
anonymized responses against the rubric separately; read the responses rather
than treating output strings or passing pytest as a behavior score.

The baseline is source commit `a5d0413f8f10e8a52690bb275ed53fcbb689cb7b`, which
includes the two separately authorized preparation repairs. QA uses the skill
body, its regression checklist and Quinn's unchanged role; the candidate also
includes the newly declared execution checklist. Manager uses the bounded
existing due-phase review excerpt, including its eligibility and snapshot
boundary. The cases and common QA context are identical between versions. These
inputs do not reconstruct the historical kid-todolist agent prompt.

The initial characterization source-code excerpts were from
[kid-todolist tasks.tsx](https://github.com/luyotw/kid-todolist/blob/eea7a862bee6b192870f10a63713db796c288796/functions/test/browser/tasks.tsx)
and the
[schedule integration case](https://github.com/luyotw/kid-todolist/blob/eea7a862bee6b192870f10a63713db796c288796/functions/test/schedule-journeys.integration.vnext.ts#L156).
A's initial report characterization came from the confirmed issue #647 input.
The first iteration did not have the original reports; its `cases.md` and
`observations/` remain an explicitly preliminary characterization comparison.
The supplied originals now support the separate original-source evaluation below.
C–H are controlled scenario facts,
including synthetic success/failure observations, not claims that kid-todolist
was tested in this change.

Each invocation must finish with an explicit `turn.completed`, a non-empty final
answer, and process exit 0; record tool-use observations independently. Preserve
interruptions as incomplete, enforce a bounded timeout and child-exit cleanup,
and forward actual structured progress to stdout. The configured model is the
confirmed Codex `gpt-6.1-sol` with `high` reasoning. Each fresh ephemeral session
has shell, network search, MCP, apps, plugins, hooks, memories and delegation
disabled, uses an empty working directory, and receives no blackboard or expected
answers. Disable repository/user document loading for the bounded packet.

To replay the bounded review from the repository root, set `CAFE_QA_EVAL_DIR` to
a fresh temporary directory. In it, create `baseline` and `candidate` directories
and write `qa.md` and `manager.md` from the respective Git versions of
`src/cafe/data/skills/cafe-qa/SKILL.md` and
`src/cafe/data/skills/use-cafe-workflow/references/running_workflow.md`. Copy this
directory's chosen packet into that temporary directory as `cases.md`. Use
`historical-cases.md` and `scope-cases.md` in separate directories and fresh
sessions so the scope/sufficient-evidence controls cannot reveal answers to the
historical negative controls. Then run the following
manually; the script never loads the assessor rubric:

```bash
python tests/evals/qa_evidence/run_eval.py baseline qa
python tests/evals/qa_evidence/run_eval.py candidate qa
python tests/evals/qa_evidence/run_eval.py baseline manager
python tests/evals/qa_evidence/run_eval.py candidate manager
```

The common regression reference and Quinn role come from the checkout and must
be unchanged between the compared policies. Anonymize the four resulting answers
before giving the chosen packet and its matching assessor-only rubric
(`historical-rubric.md` or `scope-rubric.md`) to a fresh, equally restricted
model session. Check its reasoning against the actual answers and retain the
mapping outside its prompt. Keep initial pilots and interrupted runs out of the
scored comparison.

Finite observations support only these cases and this model configuration. Even
if both policies pass, do not claim a measured reliability improvement or a
proven historical root cause. The characterization alone cannot complete the
original-report acceptance row; use the actual-source comparison below.


## Preliminary characterization comparison (iteration 001)

The four recorded answers and their separate terminal/process evidence are in
`observations/`. All four used the same case packet SHA-256
`d1f34b128dd8328a4f35ab719370d25717dae6e8a5c794281be058291aea2381`,
exited 0, emitted `turn.completed`, produced a durable final answer and showed no
tool use. The disabled code-mode-host startup diagnostic is retained in metadata;
it was not a terminal error, substitute execution evidence, or a reason to enable
more tools. QA durations were 56.77/75.02 seconds (baseline/candidate); Manager
48.54/51.67 seconds. These are observations, not a speed or reliability claim.

The assessor received cases, rubric and anonymized answers only, without policies
or the version mapping. Its answer is retained alongside the later-revealed
mapping. It graded candidate QA (R4) and candidate Manager (R1) as passing A–H,
plus the Manager not_required condition. It graded baseline QA (R2) A/B and
baseline Manager (R3) A as failing. The baseline QA sent executable evidence gaps
to need_clarification; the baseline Manager did not identify the pre-opened local
gate. The developer checked these explanations against the actual answers rather
than accepting keyword counts. The candidate's description of the opened local
gate and authorization bypass is semantic gate evidence; literal use of the word
PIN is not a scoring requirement by itself.

These are single responses to a batch of controlled cases; cross-case contrasts
can assist judgment. They do not estimate a general failure rate. The pilots
preceding the scored batch were excluded because the common QA context and source
line annotations were being finalized; no pilot response was used to tune the
skill or supplied to the tested model. All scored prompts used the corrected
identical packet and unchanged common QA context.

The missing original-source input was subsequently supplied through the existing
clarification HumanTask. The preliminary characterization result above is retained
for audit, and is not substituted for the actual original-report evaluation.

## Original-source comparison (iteration 002)

The original reports, artifact hashes, checklists, browser fixture and historical
SDK test are retained under `originals/`, with their supplied provenance. Every
one of the nine supplied source files was independently verified by byte count
and SHA256, and each report hash matches its historical artifact.json. The later
eea7a862 SDK file was checked as provenance but is not the original QA case source.
See [the source notes](originals/README.md).

`historical-cases.md` embeds both complete unmodified reports, the complete
original browser fixture and historical SDK lines 155–175, identically used at
QA HEADs c9d57861 and 416f6ace. It contains no issue #647 diagnosis or expected
answers. Its A1/A2/B review questions keep the reported staged scope while asking
which claims the supplied evidence supports. `scope-cases.md` separately retains
the sufficient App/emulator evidence, backend, presentation, documentation,
staged-delivery and observed-failure controls C–H. These are controlled scenario
facts, not actual kid-todolist executions in this issue.

The historical packet SHA256 is
`db83b88aec27a8d80f49958bccc82a1b2cc3803a249fba2ec720ce8b28463558`;
the scope-control packet SHA256 is
`3543aa31f8a6da83be71eeb3878ca8b890e2d1926538fcd82b19f088dffb86e5`.
Both tested policy versions receive the exact same packet within each group.
The pre-change baseline, confirmed model/effort, common QA context, empty working
directory and tool restrictions remain as described above. No full blackboard,
session or streaming archive is supplied. Historical empty effective checklist
gates do not imply that current baseline instructions were in the historical
prompt; this is current-skill validation against historical evidence.

The actual-source answers, assessor judgments and later-revealed anonymous
mappings are retained in `original-observations/`. Final behavior results are:

| Control | Candidate QA | Candidate Manager | Evidence fit |
| --- | --- | --- | --- |
| A1: original first-stage report | Pass | Pass | Retains local real-SDK/Rules and fixture results; identifies the pre-opened gate and missing connected Family/edit/save/parent-return sequence. |
| A2: original correction report | Pass | Pass | Accepts narrow renewal/owner-transfer evidence and valid local UI reuse while preserving the inherited journey gap and pending delivery. |
| B: historical SDK reorder case | Pass | Pass | Distinguishes afterKey=null/beforeKey=a from an unobserved interior move with both neighbors; requests evidence without inventing an ordering cost/root cause. |
| C: sufficient connected App evidence | Pass | Pass | Accepts the committed stage without OAuth/cloud/iOS or full-product gates. |
| D–F: backend, presentation and documentation | Pass | Pass | Uses the matching API/presentation/artifact evidence and justified persistence reuse. |
| G: explicit staged delivery | Pass | Pass | Accepts completed stage-one engineering; deferred capabilities stay incomplete. |
| H: observed in-stage failure | Pass | Pass | Rejects the completion claim and uses existing correction handling, preserving uncertainty about cost/root cause. |
| Legitimate immediately advancing not_required | N/A | Pass | Adds no proactive review, new pause, implementation intervention or blanket retesting. |

Both baseline roles missed B's specific two-neighbor gap on the original-source
packet, while preserving other bounded evidence and scope. The initial candidate
Manager also missed B by focusing on task count/origin. Its failed answer,
initial blinded assessment and mapping are retained rather than discarded.
The existing due-review paragraph was then refined to compare combinations of
populated payload fields and prerequisite states with ordinary-use branches.
The adjacent Manager contract check failed before this refinement and passed
afterward; it guards instruction placement, not semantic behavior by itself.

Only the affected candidate Manager was rerun on both unchanged packets after
that repair. Passing QA answers and baseline answers were carried forward because
their exact prompts, policies, common context, tool permissions and packets were
unchanged. Fresh independent assessors received the final anonymized answers
without policy versions or mappings. Their final judgments pass the candidate
on every applicable historical and scope control. The developer independently
read those answers and checked the judgments, including that the local gate is
the PIN gate named in the source report and that the missing neighbor combination
is explicitly distinguished from the successful head move.

`original-observations/completion.json` retains final and initial completion
records, commands, packet/prompt/policy/output hashes, bounded startup diagnostics,
actual durations, terminal events, process exits and tool-use observations. All
14 retained invocations (10 tested responses, four assessor responses) exited 0,
produced durable final answers and emitted turn.completed, without observed tool
use. Every paired group's commands have identical tool restrictions; only output
paths differ. Each invocation remained below the 240-second bound. The final
historical QA durations were 64.87/51.76 seconds and Manager 47.15/50.32 seconds;
scope QA 42.86/48.68 and Manager 35.44/42.54 (baseline/candidate). No speedup,
failure-rate estimate, general reliability gain or historical root cause is
claimed from these finite observations.

The original-report acceptance obligation left pending in iteration 001 is now
complete. These are model evidence-interpretation evaluations, not renewed QA or
product fixes for kid-todolist, and no historical runtime artifacts were edited.
They remain separate from automated skill loading, effective-checklist and
existing-route tests and introduce no per-PR or per-phase model gate.

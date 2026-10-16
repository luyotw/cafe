# Manual QA evidence interpretation evaluation

This is one-time model validation for issue #647, separate from automated skill
loading/checklist/routing tests. It is not installed as a skill, a runtime
validator, a repository PR gate, or a task run at every CAFE phase.

Use the same `cases.md`, model/effort, and tool permissions with anonymous baseline
and candidate policies. Give only `cases.md` and the relevant policy to each test
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

The source-code excerpts are from
[kid-todolist tasks.tsx](https://github.com/luyotw/kid-todolist/blob/eea7a862bee6b192870f10a63713db796c288796/functions/test/browser/tasks.tsx)
and the
[schedule integration case](https://github.com/luyotw/kid-todolist/blob/eea7a862bee6b192870f10a63713db796c288796/functions/test/schedule-journeys.integration.vnext.ts#L156).
A's report characterization comes from the confirmed issue #647 input. The full
original report was not supplied or retrieved; this packet must not be described
as a verbatim original report evaluation. C–H are controlled scenario facts,
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
directory's `cases.md` into that temporary directory. Then run the following
manually; the script never loads the assessor rubric:

```bash
python tests/evals/qa_evidence/run_eval.py baseline qa
python tests/evals/qa_evidence/run_eval.py candidate qa
python tests/evals/qa_evidence/run_eval.py baseline manager
python tests/evals/qa_evidence/run_eval.py candidate manager
```

The common regression reference and Quinn role come from the checkout and must
be unchanged between the compared policies. Anonymize the four resulting answers
before giving the cases and assessor-only rubric to a fresh, equally restricted
model session. Check its reasoning against the actual answers and retain the
mapping outside its prompt. Keep initial pilots and interrupted runs out of the
scored comparison.

Finite observations support only these cases and this model configuration. Even
if both policies pass, do not claim a measured reliability improvement or a
proven historical root cause. Evaluate the full original QA report separately
when available; the characterization alone cannot complete that acceptance row.


## Recorded characterization comparison

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

**Outstanding acceptance:** A used the issue-provided characterization, not the
full original QA report. Obtain that report (without the full blackboard), freeze
a bounded source packet that excludes issue #647's diagnosis, and repeat the
paired blind assessment before claiming the original-report acceptance row is
complete. The passing characterization comparison does not close that obligation.

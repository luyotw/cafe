# Issue 573 Kickoff Preparation Measurement

## Status

The confirmed formal comparison uses Codex CLI `0.156.1`, `gpt-6-astra`, medium reasoning, and sandbox disabled for every condition. The 21 formal preparations (six baseline, six cold, six warm, and three changed-input) completed on 2026-09-29. The two failed Codex attempts and one Claude HTTP 429 remain historical invalid attempts, not observations. Two separately authorized Codex diagnostics are excluded from the benchmark attempt count. The historical data do not establish performance acceptance; the Case B baseline has a delivery-workload mismatch documented below. The changed-input result metadata also records a request hash mismatch; see the final measurement reconciliation below.

The required pre-change revision is `6ce6bddade03e6ee31a60f437accd4d557467c50`. The measured baseline remains tied to its read-only source revision; normal Manager guidance changed only after all six baseline observations were captured.

## Fixed cases

The accepted plan defines two representative cases. The intended fixed inputs follow. The historical Case B prompts did not consistently preserve the delivery endpoint; those observations remain recorded but are not a matched PR comparison.

| Case | Required shape | State |
| --- | --- | --- |
| A | A bounded documentation change with a straightforward effective graph. | Fixed below. |
| B | A request requiring multiple phase requirements and repository-informed delivery. | Fixed below. |

The planned conditions are baseline at the pre-change revision, improved cold with isolated empty stores (including preference save cost), and improved warm in a fresh Manager context. Each condition requires three actual runs per case. A separate changed-input case requires three actual runs.

### Fixed inputs

Conversation locale is `zh-TW`; repository content locale is the confirmed `en-US`. The following are the exact user requests supplied to each fresh Manager session:

- **A:** `請在 README.md 新增一個既有 cafe prepare CLI 用法範例，僅補充用法文件，不改程式碼。交付端點為本機 commit。請準備完整 kickoff 契約。`
- **B:** `請新增使用者與專案層級的偏好檢視、設定與清除操作，並包含測試與文件。交付端點為以 develop 為目標的 PR。請準備完整 kickoff 契約。`
- **Changed input:** Case A with this one material change: `請將用法範例放在 docs/cli.md，而不是 README.md。` All other Case A answers and preferences stay fixed.

UTF-8 request SHA-256 and byte counts: A `19fb10197b5d26f654d99b4b0cd563d14a1be142c9ccfb6ed48a11afda78b280` (174 bytes); B `960e3b8ed55a5b7de5c8455f836164b946c4c2285a994d126b56dacd246704be` (180 bytes); changed input `159c42e3cdd330c3fa00644c5b6e3309e2ccb6f774c8b9ede08144e338014181` (253 bytes).

Fixed accepted answers and preferences for every case: the requested scope and endpoint above are confirmed; no extra independent scope is implied; overall clarification remains manager-confirmable and mandatory HumanTask/user-owned gates are preserved. Use the confirmed #573 phase model mapping (`develop` and `pr`: `codex:gpt-6-luna`; other phases: `codex:gpt-6-astra`, with no fallback), `standard-qa` as the effective graph, and the confirmed repository content locale. Each session may prepare only a complete reviewable proposal. The benchmark must not make a case commit, create a PR, activate a workflow, or perform another case delivery action. These are experiment fixtures, not changes to #573's confirmed contract or saved user preferences.

The baseline source is `6ce6bddade03e6ee31a60f437accd4d557467c50`; its `src/cafe/data/playbooks` tree is `967383ef57a3c262ce89443a751f88e9abdd8d59`. The implementation source before guidance changes is `192c6d8c620aada1ca144336384a2ee8241eb7f6`; its playbook tree has the same digest. The confirmed #573 Manager contract fixture SHA-256 is `56a4b38e50178727b0a90640603e81c0b5d577a74141746e37cce6421a5b09e4`; the repository language settings fixture SHA-256 is `17ca7d3e6557e5ef41202dacb6ac030009b462fa78a4da8dc6f5e4ccc1c65a2e`. Case A's unchanged `README.md` SHA-256 is `0c8d8ed1aff71bace723c555a5dbcf45df7dd187bf034bf2775bd2856e7b76a0`. The plan's existing runtime/catalog/probe preflight is held equivalent by not running or injecting it in either condition. The only evidence inputs supplied to both conditions are the same contract/language fixtures, source checkout, and explicit request/answers above; new preference/evidence stores are isolated per observation and their starting state is recorded in the run table.

## Environment and invocation

- Repository: `/home/luyotw/cafe`
- Baseline revision: `6ce6bddade03e6ee31a60f437accd4d557467c50`
- Implementation revision: to be recorded after the code is complete.
- Python environment: CPython 3.12.2, as reported by the repository pre-commit hook.
- Available Manager CLI executables: `claude`, `gemini`, and `codex` are installed in the local environment.
- Formal Manager CLI: Codex CLI `0.156.1`, model `gpt-6-astra`, reasoning `medium`. Every baseline/cold/warm/changed-input invocation uses the same `--dangerously-bypass-approvals-and-sandbox` flag, `--ephemeral`, `--ignore-user-config`, exact CLI/model/effort and fixed case prompt. Each call receives a fresh checkout/session. The source checkout is read-only; outputs and isolated CAFE config/cache are outside it. The bypass applies only to these explicitly authorized proposal-only measurement invocations. It does not grant benchmark case delivery, preference persistence, activation, or any global permission change.
- Historical Claude-only tool-path check (no provider call): Claude Code help confirmed all listed flags; the fixed helper runner (`/tmp/cafe-573-tool-surface-xtoekfnu/manager_helper.py`, SHA-256 `d5c957e948891cb6ea43c84ea4798db269f73199e6b39726007d6c48625d068b`) accepted only `discover|assemble|render`, used `shell=False`, and successfully ran `discover` from this checkout. The helper wrote its catalog cache and locks only beneath isolated `/tmp/cafe-573-tool-surface-xtoekfnu/{config,cache}`; provider calls: 0. This Claude-specific check is retained only as superseded history; the Codex comparison above is the active formal method.
- Manager reasoning settings: `medium`; every baseline/cold/warm/changed-input session uses this same CLI, exact model, effort, tool list, permission mode, allowlist and a fresh CLI session.
- Superseded Claude authorization: an earlier direction reserved 21 Claude preparations within 23 attempts. The later user direction replaced that provider/method with exactly 21 formal Codex preparations; this historical Claude cap does not constrain the active comparison.
- Fixed Manager instruction template SHA-256: `086dbc4cc1d3f258b2bd79af259339ae10eeaca3305604e8bf72870880a7ce46` (1,751 UTF-8 bytes). Attempt 1 used the detached baseline worktree; attempt 2 used a read-only `git archive` of the same revision with no Git metadata, `--ignore-user-config`, `--skip-git-repo-check`, and `sandbox_network_access=true`.
- No case action is authorized. The sessions were instructed to remain proposal-only; the second used a read-only source archive with no Git metadata and an empty `HOME`. No case action was run.

## Run records

The previous three invalid attempts remain recorded below as history. They are not valid baseline observations. The two diagnostic invocations requested separately by the user are excluded. All six new observations use Codex CLI `0.156.1` / `gpt-6-astra` / medium, `--dangerously-bypass-approvals-and-sandbox`, a fresh ephemeral session, the exact fixed case prompt, and the read-only baseline source at `6ce6bddade03e6ee31a60f437accd4d557467c50`. Proposal SHA-256 and full artifacts are retained at the paths shown. The complete rendered proposal was checked for scope, acceptance, endpoint, gates, assumptions and confirmation boundary; no unresolved-choice question was asked. Mandatory confirmation remains separate from clarification count.

| Case | Condition | Run | Proposal evidence | Elapsed | Tool spans | Evidence bytes | Clarifications | Human wait | State |
| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | --- |
| A | Invalid baseline attempt (not an observation) | Codex-1 | `/tmp/issue573-kickoff-benchmark/A-baseline-1/proposal.txt` (SHA-256 `9810ae3aab6f1fc171c9fc9fae56d83339bf5d084948c8ede79694eda56de368`; incomplete) | 81.870 s | 0 shell; 2 MCP request/result round trips | 0 source bytes (1,751 prompt bytes) | 0 | 0 s | Invalid: `bwrap` loopback setup failed; 123,459 input / 2,151 output tokens |
| A | Invalid baseline attempt (not an observation) | Codex-2 | `/tmp/issue573-kickoff-benchmark/A-baseline-2/proposal.txt` (SHA-256 `6b0bded128bc9b64324e923d9531eed0234ff84edf4ceb0563c5e886a2483ca7`; incomplete) | 68.199 s | 0 shell; 2 MCP request/result round trips | 0 source bytes (1,751 prompt bytes) | 0 | 0 s | Invalid: same sandbox startup error; 93,970 input / 1,855 output tokens |
| A | Invalid baseline attempt (not an observation) | Claude-1 | `/tmp/issue573-kickoff-benchmark/A-baseline-claude-1/streaming.jsonl` (no proposal) | 0.925 s | 0 tools; 1 rejected request | 0 source bytes (1,751 prompt bytes) | 0 | 0 s | Invalid HTTP 429 before prompt processing; 0 input / 0 output tokens |
| A | Baseline | 1 | `/tmp/issue573-kickoff-benchmark/codex-formal/A-baseline-1/proposal.txt` (SHA-256 `e1b17efc299c6c19379b6f3a84292addaa79f1e8935ad487527c9d4af94d7a40`; 9729 bytes) | 243.184 s (2026-09-29T12:02:04.480006Z–2026-09-29T12:06:07.663560Z) | 16 completed command spans; per-span timestamps unavailable | 304818 captured command-output bytes; 1,751 prompt bytes | 0 unresolved-choice questions | 0 s | Valid; exit 0; source `6ce6bdd`; checkout clean |
| A | Baseline | 2 | `/tmp/issue573-kickoff-benchmark/codex-formal/A-baseline-2/proposal.txt` (SHA-256 `f610b7698ac10e129ea62298b983d6666adf57d16eb70045913dcd8de1826922`; 11472 bytes) | 315.179 s (2026-09-29T12:06:07.833163Z–2026-09-29T12:11:23.012240Z) | 18 completed command spans; per-span timestamps unavailable | 347503 captured command-output bytes; 1,751 prompt bytes | 0 unresolved-choice questions | 0 s | Valid; exit 0; source `6ce6bdd`; checkout clean |
| A | Baseline | 3 | `/tmp/issue573-kickoff-benchmark/codex-formal/A-baseline-3/proposal.txt` (SHA-256 `9c757cbb768444cdd4c611a543370313603613034e6f257bc044d76c89e76808`; 10062 bytes) | 319.010 s (2026-09-29T12:11:23.152945Z–2026-09-29T12:16:42.162876Z) | 21 completed command spans; per-span timestamps unavailable | 418006 captured command-output bytes; 1,751 prompt bytes | 0 unresolved-choice questions | 0 s | Valid; exit 0; source `6ce6bdd`; checkout clean |
| B | Baseline | 1 | `/tmp/issue573-kickoff-benchmark/codex-formal/B-baseline-1/proposal.txt` (SHA-256 `7fae27941cad291c7682f0e0f71c14a19f75a3165b37868272f9386df3c4f5bf`; 9301 bytes) | 238.081 s (2026-09-29T12:16:42.315539Z–2026-09-29T12:20:40.396729Z) | 16 completed command spans; per-span timestamps unavailable | 276609 captured command-output bytes; 1,751 prompt bytes | 0 unresolved-choice questions | 0 s | Valid; exit 0; source `6ce6bdd`; checkout clean |
| B | Baseline | 2 | `/tmp/issue573-kickoff-benchmark/codex-formal/B-baseline-2/proposal.txt` (SHA-256 `905341cd237b272fd34556e3eb8772f7cefcbe16aa5b35e5348908ea4be2e751`; 10534 bytes) | 350.333 s (2026-09-29T12:20:40.547913Z–2026-09-29T12:26:30.880477Z) | 19 completed command spans; per-span timestamps unavailable | 336270 captured command-output bytes; 1,751 prompt bytes | 0 unresolved-choice questions | 0 s | Valid; exit 0; source `6ce6bdd`; checkout clean |
| B | Baseline | 3 | `/tmp/issue573-kickoff-benchmark/codex-formal/B-baseline-3/proposal.txt` (SHA-256 `664297e4e7317e0cec266b866e63627bfe381f190db40b4f79c163afbe79b95d`; 10790 bytes) | 241.438 s (2026-09-29T12:26:31.055437Z–2026-09-29T12:30:32.493896Z) | 18 completed command spans; per-span timestamps unavailable | 314693 captured command-output bytes; 1,751 prompt bytes | 0 unresolved-choice questions | 0 s | Valid; exit 0; source `6ce6bdd`; checkout clean |
| A | Cold | 1 | `A-cold-1/proposal.txt` (`1d8993b8…`) | 297.052 s | 23 commands; 12.782 s union | 395,860 output bytes | 0 | 0 s | Complete proposal; clean source |
| A | Cold | 2 | `A-cold-2/proposal.txt` (`d8ee6860…`) | 317.940 s | 23 commands; 14.119 s union | 444,220 output bytes | 0 | 0 s | Complete proposal; clean source |
| A | Cold | 3 | `A-cold-3/proposal.txt` (`c2284849…`) | 305.851 s | 20 commands; 11.615 s union | 418,611 output bytes | 0 | 0 s | Complete proposal; clean source |
| A | Warm | 1 | `A-warm-1/proposal.txt` (`ca11f00b…`) | 311.648 s | 21 commands; 8.054 s union | 535,324 output bytes | 0 | 0 s | Complete proposal; clean source |
| A | Warm | 2 | `A-warm-2/proposal.txt` (`5317dac3…`) | 335.052 s | 18 commands; 5.926 s union | 513,512 output bytes | 0 | 0 s | Complete proposal; clean source |
| A | Warm | 3 | `A-warm-3/proposal.txt` (`8a45f42c…`) | 346.205 s | 22 commands; 20.035 s union | 476,189 output bytes | 0 | 0 s | Complete proposal; clean source |
| B | Cold | 1 | `B-cold-1/proposal.txt` (`82ca28d0…`) | 333.106 s | 21 commands; 10.795 s union | 403,529 output bytes | 0 | 0 s | Complete proposal; clean source |
| B | Cold | 2 | `B-cold-2/proposal.txt` (`70839432…`) | 299.201 s | 17 commands; 14.596 s union | 260,487 output bytes | 0 | 0 s | Complete proposal; clean source |
| B | Cold | 3 | `B-cold-3/proposal.txt` (`f97e08a8…`) | 367.717 s | 17 commands; 12.025 s union | 476,571 output bytes | 0 | 0 s | Complete proposal; clean source |
| B | Warm | 1 | `B-warm-1/proposal.txt` (`487897c3…`) | 253.898 s | 18 commands; 3.642 s union | 513,890 output bytes | 0 | 0 s | Complete proposal; clean source |
| B | Warm | 2 | `B-warm-2/proposal.txt` (`9287588b…`) | 345.146 s | 22 commands; 8.301 s union | 534,132 output bytes | 0 | 0 s | Complete proposal; clean source |
| B | Warm | 3 | `B-warm-3/proposal.txt` (`e7149bcb…`) | 371.385 s | 18 commands; 6.973 s union | 518,002 output bytes | 0 | 0 s | Complete proposal; clean source |
| A | Changed-input warm | 1 | `changed-input-warm-1/proposal.txt` (`0664e29c…`) | 266.553 s | 18 commands; 10.255 s union | 450,819 output bytes | 0 | 0 s | Complete proposal targets `docs/cli.md`; request hash mismatch |
| A | Changed-input warm | 2 | `changed-input-warm-2/proposal.txt` (`c9c7f021…`) | 263.718 s | 21 commands; 9.473 s union | 366,302 output bytes | 0 | 0 s | Complete proposal targets `docs/cli.md`; request hash mismatch |
| A | Changed-input warm | 3 | `changed-input-warm-3/proposal.txt` (`3ec01ccf…`) | 280.568 s | 21 commands; 7.624 s union | 444,908 output bytes | 0 | 0 s | Complete proposal targets `docs/cli.md`; request hash mismatch |

For these baseline runs Codex JSONL records command start/completion ordering but does not include per-item timestamps; therefore the wall-time union of tool spans and separate Manager reasoning/orchestration residual are unavailable. The recorded elapsed duration is end-to-end Manager preparation time with fixed answers and no human waiting. Captured command-output bytes are measured from completed JSONL tool results. Codex exposes token totals, but reasoning duration is not separately reported. No external research tool spans occurred.

## Current measurement limitations

Baseline preparation medians/ranges are A 315.179/75.826 s and B 241.438/112.252 s. Cold medians/ranges are A 305.851/20.888 s and B 333.106/68.516 s. Warm medians/ranges are A 335.052/34.557 s and B 345.146/117.487 s. Warm is 29.201 s slower than cold for A and 12.040 s slower for B; versus baseline, warm is 19.873 s slower for A and 103.708 s slower for B. These observations do not demonstrate the required warm Manager-level improvement. The changed-input median/range is 266.553/16.850 s. All 15 cold, warm and changed-input sessions completed with exit code 0 and clean read-only checkouts. Their raw results retain exact proposal hashes, usage, per-command spans, and command-output byte counts.

The historical Claude reset, Claude CLI validation, and old Claude attempt cap below describe the superseded Claude comparison only. Do not mix historical invalid calls, separate diagnostics, or Claude records into current condition statistics.

## Historical Claude pre-reset verification

Before the provider reset, the baseline archive was compared with Git revision `6ce6bddade03e6ee31a60f437accd4d557467c50`: all 684 tracked paths, blob contents, and file modes match exactly, and no path is writable. The archive's nineteen lost executable bits were restored without adding write permission. The helper runner remains `/tmp/cafe-573-tool-surface-xtoekfnu/manager_helper.py`, SHA-256 `d5c957e948891cb6ea43c84ea4798db269f73199e6b39726007d6c48625d068b`.

The fixed A and B request hashes remain `19fb10197b5d26f654d99b4b0cd563d14a1be142c9ccfb6ed48a11afda78b280` and `960e3b8ed55a5b7de5c8455f836164b946c4c2285a994d126b56dacd246704be`. Their prompt hashes are respectively `086dbc4cc1d3f258b2bd79af259339ae10eeaca3305604e8bf72870880a7ce46` and `dc1516efca94b75887b2ffccf30c6c1bc3396585fcf908e7d6f91c4454f01e92`. These statements describe the superseded Claude run preparation, not the current Codex baseline. Current Codex baseline outputs are recorded above.

The PLAN-013 targeted regressions were rerun: the seven kickoff modules passed 30 tests; the Manager skill selection passed 91 (72 deselected; one existing Pydantic serializer warning); locale passed 10, catalog resolution 20, and event-driver boundary 4. These local checks used no provider calls. That local regression evidence is retained as history. All 21 formal preparations are now recorded; historical invalid calls and separately authorized diagnostics remain excluded.

## Final measurement reconciliation (2026-09-29)

The 21 formal preparations are present under `/tmp/issue573-kickoff-benchmark/codex-formal/`. The 12 improved cold/warm records use source `0abb5b4d12abf28ca945d8580ad3cbcb691d412f`; all sessions completed their turns, exited 0, produced complete proposals, and left their read-only source checkouts clean. Their model reasoning duration and human wait are separately unavailable in Codex telemetry; the instructions fixed answers and the proposals contain no unresolved-choice question. No external research tool was used. Tool wall-time union is available for cold/warm and changed-input sessions; elapsed time remains the primary end-to-end measure.

For changed-input runs 1–3, the accepted plan reports request hash `159c42e3cdd330c3fa00644c5b6e3309e2ccb6f774c8b9ede08144e338014181` (253 bytes), while all three result records report actual hash `f6106e8ea6e6dea6fdc4de1769aee636fc2ed30e9ad951a2489a3afaae8e4455` (245 bytes) and `request_hash_match: false`. Each generated proposal applies the sole stated material target change to `docs/cli.md`, preserves the other fixed gates and endpoint, and records no case action. This supports the observed target-change behavior, but the eight-byte/hash discrepancy is an unresolved protocol-fidelity limitation and is not represented as an exact fixed-request match.

The observations retain their original proposals and authority boundaries, but the Case B baseline proposals do not preserve the accepted PR endpoint. Acceptance criteria 1–8 have implementation, documentation, and targeted regression evidence in the committed feature files recorded in the develop ledger. Criterion 9 remains incomplete because of the Case B endpoint mismatch and changed-input hash limitation. Criterion 10 has end-to-end elapsed time, command counts, command-output byte counts, clarification count, and available command wall-time union; Codex does not expose model reasoning duration separately, so pure reasoning and residual attribution remain unavailable. Criterion 11 is unmet: the historical observations do not establish a valid end-to-end improvement; the report does not infer speedup from helper/tool metrics. Criterion 12 and overall performance acceptance therefore remain unmet. No benchmark case was delivered, committed, published, activated, or cleaned up.

## Supplementary single B warm run (2026-09-30)

This one-off run follows the user's direction to try one complete warm preparation and look for an obvious improvement. It is supplementary to, and does not replace or relabel, the original 21 formal observations or fill the accepted three-observation-per-condition requirement. Exactly one provider invocation was started; there were no retries, additional provider probes, or case actions.

| Case | Condition | Run | Source / invocation | Proposal evidence | Elapsed | Tool spans | Captured tool output | State |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| B | Warm, supplementary | 1 | Source `fa720165a5dda62c743aa4710b8707ffc8518186`; Codex CLI `0.156.1`; `gpt-6-astra`; medium; sandbox disabled; exact fixed request 180 bytes / SHA-256 `960e3b8ed55a5b7de5c8455f836164b946c4c2285a994d126b56dacd246704be`; prompt 2,210 bytes / SHA-256 `c76fd11dca7a6f505d7711d78e6aed0d0f9c7d4de3bcb955a8c6912026b8db66` | `/tmp/issue573-kickoff-benchmark/codex-formal/B-single-warm/proposal.txt` (SHA-256 `52e67454ebda645929f8f00daac91acd0e72e416346444f09eab0bae97feb24c`; 12,066 bytes) | 487.533 s (2026-09-29T16:17:30Z–16:25:37Z) | 41 completed shell command round trips; no external research spans | 764,546 captured command-output bytes; 2,118,609 input tokens (1,963,392 cached), 13,605 output tokens | Complete rendered proposal; exit 0; checkout clean; no case action |

The isolated paired-store copy and local evidence refresh took approximately 5.9 seconds, recorded separately from Manager elapsed time. Immediately before the invocation, fixed request bytes/hash, prompt inclusion/hash, source revision and repository identity were checked. Local discovery reported `ready`; delivery evidence and both exact model identities (`openai:gpt-6-astra:gpt-6-astra`, `openai:gpt-6-luna:gpt-6-luna`) were fresh validated hits with no diagnostics. Manager discovery again reported delivery and both models as hits; assembly was `ready` and render was `rendered`. The preliminary local assembly diagnostic was intentionally `incomplete` because it had no Manager-produced formatter decisions; it was not treated as a proposal or as evidence of a complete assembly.

The proposal preserved the exact request, confirmed `standard-qa`, locale and phase-model answers, user-owned gates, and the explicit PR endpoint `develop`; it also disclosed that general `CONTRIBUTING.md` guidance says `main`. The complete proposal remained review-only and performed no implementation, commit, PR, workflow, preference update, delivery, or cleanup action.

The 487.533-second PR preparation cannot be compared causally with the 241.438-second local-commit baseline median or the formal warm runs with incomplete evidence. These are different delivery workloads; the numerical difference does not measure an implementation slowdown.

The valid warm hits did not remove repeated exploration in this Manager session. The 41 command round trips and 764,546 output bytes exceed the prior B warm range of 18–22 commands and 513,890–534,132 bytes. The command log shows repeated reads across kickoff/model-selection guidance and repeated reads of engineering/settings guidance. These tool-output bytes include diagnostic JSON and local program output, so they are a bounded proxy for evidence-reading volume, not unique source bytes. The manager telemetry does not separate reasoning time or provide per-command durations. A local `candidate-check` returned `not_cached` for both phase models; this was a cache-only CLI fingerprint lookup (no provider invocation or capability probe), and the proposal disclosed the missing candidate evidence rather than fabricating it.

This result leaves PLAN-014 incomplete: it neither meets the accepted three-run protocol nor demonstrates warm Manager-level improvement. PLAN-015, PLAN-010's historical test-first red gap and strict-order violation, and the earlier changed-input request-hash mismatch also remain open. No accepted criterion, historical observation, plan, spec, or gate was changed by this supplement.

## Post-change single B warm run (2026-09-30)

This is the one authorized complete warm trial after the compact-helper and repeated-report-guidance commits. It supplements the 21 formal observations and the earlier 487.533-second one-off. It does not replace prior data, satisfy the accepted three-run-per-condition protocol, or authorize a retry. The fixed request remained 180 bytes with SHA-256 `960e3b8ed55a5b7de5c8455f836164b946c4c2285a994d126b56dacd246704be`; the unchanged 2,210-byte prompt remained SHA-256 `c76fd11dca7a6f505d7711d78e6aed0d0f9c7d4de3bcb955a8c6912026b8db66`.

| Case | Condition | Run | Source / invocation | Proposal evidence | Elapsed | Tool calls | Captured command output | State |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| B | Warm, post-change supplementary | 1 | Source `79ae3c412bd06019bd559e21d9133fdf1d05abc5`; Codex CLI `0.156.1`; `gpt-6-astra`; medium; sandbox disabled; fixed request and prompt hashes above | `/tmp/issue573-kickoff-benchmark/codex-formal/B-post-change-single-warm/proposal.txt` (SHA-256 `f61a8a50ae99b0a62b1b7dc0139d0cbf77c70d5edc4d3c53663502c0e8a2c8f9`; 10,382 bytes) | 398.783 s (`2026-09-30T00:25:05.271Z`–`00:31:44.054Z`) | 28 completed shell command round trips; one completed turn; no web/MCP research calls | 414,817 bytes surfaced in completed shell-command results; 1,320,634 input tokens (1,229,056 cached), 11,236 output tokens | Complete formatted proposal; exit 0; source checkout clean; no case action |

Before the Manager invocation, a fresh copy of the prior isolated XDG stores was checked through the public helper on the current source. All 13 catalog candidates, including eligible `standard-qa`, were reusable; repository delivery evidence was a validated hit with no discovery gap; exact `openai:gpt-6-astra:gpt-6-astra` and `openai:gpt-6-luna:gpt-6-luna` assessment records were hits with no diagnostics and finite expiry. Assessment evidence age was about 29,417 seconds. Repository identity was `git:/home/luyotw/cafe/.git`. The compact helper preflight took approximately 0.7 seconds, outside the 398.783-second Manager elapsed time. Store-copy/setup wall time was not separately instrumented. The Manager used the copied stores via isolated `XDG_CONFIG_HOME` and `XDG_CACHE_HOME`; the fixed request, prompt hashes, CLI/model/effort, sandbox mode, confirmed answers, and proposal-only endpoint were preserved. No provider retry or model probe was run.

The proposal is complete and the formatter reported `assemble` complete and `render` successful. It retains the exact request, `standard-qa`, the confirmed phase chain and locales, mandatory/user-owned gates, and a proposal-only PR endpoint targeting `develop`, while disclosing that general repository guidance says `main`. Exit 0 and successful rendering establish proposal completeness only; they do not establish issue acceptance or authorize implementation/delivery actions.

The result is 88.750 seconds (18.2%) faster than the previous supplementary single run at 487.533 seconds. Captured command output decreased by 349,729 bytes (45.7%), and completed shell commands decreased from 41 to 28. This is a favorable one-run signal against that earlier individual observation, not a variance-adjusted performance conclusion. The previously reported “65.2% slower than baseline” conclusion is withdrawn: the baseline proposals stop at local commit, whereas both supplementary proposals target a PR to develop. Neither supplementary run establishes performance against an equivalent pre-change baseline.

Captured command output is the directly countable Manager-visible reading-volume proxy, not unique source bytes: commands combine source text, helper diagnostics, proposal output and other local output. The run still reread unchanged guidance: `kickoff_inputs.md` appeared in the initial reference batch and a later standalone read; `model_selection.md` appeared in two separate reads; `kickoff.md` was read in the initial batch and later sections were read again. In contrast, the complete discovery and assembly JSON were written to local files and only selected/compact facts were surfaced; no full discovery or assembly report was dumped to the Manager transcript. This supports reduced repeated report output while showing remaining guidance rereads. The JSONL has no per-command timestamps, so Manager tool wall-time union and the reasoning-versus-tool residual cannot be separated. No interactive human wait occurred during the invocation; the setup copy duration is unavailable, while the measured local helper check is reported separately above.

Raw evidence is preserved under `/tmp/issue573-kickoff-benchmark/codex-formal/B-post-change-single-warm/`: exact prompt and extracted request, isolated store copies, compact preflight summary, `run-record.json`, `streaming.jsonl`, `stderr.txt`, and proposal. The record captures the single CLI process/turn, UTC timestamps, elapsed time, hashes, usage and post-run clean-checkout result. All prior benchmark artifacts remain unchanged.

PLAN-014 remains incomplete: this single supplementary observation does not meet the accepted repetition protocol or demonstrate the required improvement over baseline, and the earlier changed-input request hash mismatch remains. PLAN-015 remains open because overall acceptance is not met. PLAN-010's historical test-first red gap and strict-order violation also remain open. No accepted plan/spec criteria or historic data were changed.

## Historical comparability correction (iteration 009)

Direct inspection of the immutable `prompt.txt`, `proposal.txt` and command outputs confirms:

- B-baseline-1/2/3 override the original PR request with a local-commit-only endpoint. All three proposals use `pr.auto_create=false`. Their 238.081/350.333/241.438-second observations are valid records of that different task, not matched observations for the accepted PR case.
- B-single-warm and B-post-change-single-warm preserve the PR-to-develop endpoint and `pr.auto_create=true`. Their identical prompt hash permits a single-pair directional comparison (487.533 to 398.783 seconds), without estimating provider variance or proving improvement over a correct baseline.
- B-warm-1/2/3 command outputs contain `delivery_evidence_missing` and `models=[]`. They did not exercise complete delivery/model warm reuse. All supplied playbook, locale and model answers upfront, and baseline did no external model research; preference dialogue savings and research savings were not directly measured.
- Before the post-change run's first discover, 13 completed commands emitted 324,469 of 414,817 bytes (78.2%). The old prompt explicitly required reading candidates, phase skills and delivery references before discover. This sequencing prevents a cache from replacing that work.
- A cache hit alone did not supply model workloads/reasoning/capability bands/limitations. The delivery fixture supplied only the general CONTRIBUTING PR convention, without current observations. Those hits cannot be treated as complete decision evidence.

No historical prompt, proposal, request, hash, time or raw trace is rewritten. Proposal-only means no case actions may execute; it does not change the proposed delivery endpoint. The historical strict-order and PLAN-010 red gaps remain. Warm-store copy/refresh took about 5.9 seconds in the earlier supplement; other pre-invocation time was phase preparation and evidence checking, separate from the 487.533-second Manager trial.

## Equal-workload correction trial (iteration 009)

The latest user direction requested the diagnosed implementation fixes and the minimum fair complete-Manager comparison. No existing pre-change PR observation matched the workload, neutral normal-guidance prompt and isolated issue identity. One new pre-change baseline was therefore paired with corrected warm preparation. The first warm attempt exposed a new schema-example defect and is retained as invalid; one additional warm on the fixed source reuses the same baseline. There is no automatic retry, model probe or repeated three-run matrix.

All three processes use Codex CLI 0.156.1, gpt-6-astra, medium reasoning, sandbox disabled, the exact 180-byte Case B request/SHA above, and the same 2,411-byte prompt SHA-256 `dec0195c910559b03308d25f742016980d9d48153ecaac66fcc0a9e349718679`. The prompt follows each revision's normal Manager entrypoint without forcing broad source reads before discovery. The endpoint is explicitly PR-to-develop with pr.auto_create=true. The new synthetic identity is `issue573-benchmark-equivalent`; no existing issue state is copied. Phase-model answers remain the historical measurement fixture (develop/pr Luna; other phases Astra), separate from the actual issue573 Develop=Astra setting.

Separate detached source worktrees and XDG config/cache directories isolate the arms. Both receive identical raw model assessments and delivery-source evidence, plus the same strategic-context overlay (SHA-256 `17ca7d3e6557e5ef41202dacb6ac030009b462fa78a4da8dc6f5e4ccc1c65a2e`). Source revisions, prompt/request/evidence hashes and checkout cleanliness are recorded before invocation. The model assessments retain their original retrieval dates and finite expiry; no provider research is added to create a warm state. Delivery conventions cite CONTRIBUTING.md, docs/script-execution-boundaries.md and docs/prepare_fields.md with verified content hashes. Dynamic remote/PR/authority observations remain missing, requiring current issue judgment. A hit does not fill those gaps.

The first warm source is `1f414f0629cd44cd76abee94233f81a55acb609b`; its new schema example incorrectly encoded reasoning as `@medium` inside the literal model ID. The resulting proposal used those altered IDs and is **not a valid matched observation**, even though it exited 0 and rendered. Its 332.824 seconds, 16 commands and 288,375 bytes remain recorded. The trace also exposed an opaque missing-preflight-field error and a full assembly dump on failed file rendering. Public CLI failing regressions preceded the corrective commit `48b13a9126e9b9c86ee5f1dc29a0e1bb022a253f`: exact model identity now survives the example, and failed rendering reports the existing formatter's validation reason with a compact response while preserving the prior output file. No formatter internals, runtime, preflight or probe code changed.

Warm fixture construction/refresh/validation took 5.683 seconds for the invalid first warm and 5.492 seconds for the corrected warm. These costs are outside Manager elapsed time. Other phase preparation, fixture design, regression execution and evidence review are also outside Manager timing and are not misrepresented as only these store-build durations.

Raw evidence directories under `/tmp/issue573-kickoff-benchmark/codex-formal/` are `B-equivalent-baseline-1`, `B-equivalent-warm-1` and `B-equivalent-warm-fixed-1`. Each retains prompt/request, setup source/hashes, untouched JSONL, stderr, final proposal and run record. `event-times.jsonl` records local receipt timestamps separately from raw provider output; command spans and their union are approximate client-observed intervals, not provider-internal timing. Elapsed minus that union combines model generation, orchestration, transport and waiting; it is not pure reasoning. Captured command-output bytes are Manager-visible tool-result volume, not unique source bytes. Fixed answers produce no interactive human wait; the final confirmation question is the required proposal endpoint.

The pair supplies the same local assessment records to both revisions, so it does not measure external model-research latency or preference dialogue savings. Single sequential observations on a shared host cannot estimate provider/run variance. Raw setup/run scripts and historical hash audit are retained under `/tmp/issue573-kickoff-benchmark/iteration009/`.

### Recorded results and proposal audit

| Observation | Source | Complete Manager elapsed | Completed shell commands | Captured output bytes | Command receipt span union | State |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| B-equivalent-baseline-1 | `6ce6bddade03e6ee31a60f437accd4d557467c50` | 383.943 s | 22 | 381,534 | 10.084 s | PR-to-develop proposal; exact fixed model IDs; clean checkout |
| B-equivalent-warm-1 | `1f414f0629cd44cd76abee94233f81a55acb609b` | 332.824 s | 16 | 288,375 | 9.048 s | Invalid model IDs introduced by schema example; excluded from matched performance evidence; preserved |
| B-equivalent-warm-fixed-1 | `48b13a9126e9b9c86ee5f1dc29a0e1bb022a253f` | 428.170 s | 22 | 256,722 | 7.422 s | PR-to-develop proposal; corrected exact model IDs; clean checkout |

Baseline proposal: 11,795 bytes / SHA-256 `23c3609c9c849c45340b93b77acdd9d28640d2daf349bc1b117ff0a539678fed`. Invalid warm: 10,562 bytes / `d26977d0cb4c19aa9d79d41c169d44e2e31e2243a0551d5037e2d3973fa33e03`. Corrected warm: 11,295 bytes / `1b61dbd33842bc45330599564a00df55d74c1aeb8cd0728effa2959d85204b2e`.

Inspection of the baseline and corrected-warm proposals confirms the exact request, user/project inspect-set-clear outcome, tests/docs, standard-qa, six exact phase model IDs, medium reasoning stated separately, zh-TW/en-US locales, mandatory/user-owned gates and PR-to-develop endpoint. No case action, activation, preference mutation or source checkout edit occurred. The proposals are not identical action plans: baseline proposes deferred `cafe close` with separate authorization, whereas corrected warm leaves cleanup empty and describes terminal `cafe close --archive-only` as a separate user option. Proposed worktree paths also differ. Thus the pair controls the request, endpoint and supplied evidence, but its distinct cleanup reasoning/validation work is an additional comparability limitation; it does not prove full closeout-plan equivalence or a causal performance change.

The corrected warm emits 124,812 fewer command-output bytes (32.7%) than this baseline, with the same 22 commands. Elapsed is 44.227 seconds higher (11.5%) in these two observations; this is no demonstrated end-to-end speedup, and cannot isolate cache implementation cost from provider variance or the different cleanup reasoning. The 373.859/420.748-second residuals after observed tool intervals are not pure model reasoning. Commands containing helper calls occupy 5.591 seconds of observed intervals in corrected warm, including surrounding request edits and rendering/translation work; pure helper time is not separately instrumented. The earlier 241.438-second local-commit median is not used as this PR baseline. Previous 487.533/398.783-second supplementary results remain separate because their prompt and issue-state conditions differ.

### Actual reuse and remaining work

- Corrected warm reaches selected-graph assembly in command 4, after 3 commands and 97,388 output bytes, versus 13 commands / 324,469 bytes before discovery in the older post-change trace. It obtains delivery and both exact-model hits with usable assessment payloads. All 13 catalog candidates remain inspectable; the selected summary does not dump every graph or every phase SKILL body. No separate playbook show/confirmation-gates loop or external model research occurs in this warm trace.
- The public `schema` is used. The initial incomplete assembly is expected, and it lists decision gaps without selecting actions. Final normalized inputs retain exact model identities; no existing workflow locale is inherited.
- The 427-byte error in item_16 identifies the invalid `implementation_direction` list where the canonical contract requires a string. The caller fixes that field and renders without diagnosing this error through source code. Item_22 rejects archive-only in closeout_plan with a compact 289-byte command output, and the previous proposal file survives. These are current product/authority decisions and input repair; the helper does not bypass the formatter to make them succeed.
- Repeated reading is reduced, **not eliminated**. Item_3 reads kickoff/strategic/model guidance; item_5 rereads strategic guidance and kickoff sections; item_11 rereads kickoff sections; item_13 rereads model-selection sections. Item_6 also reads the supplied raw delivery/model records after receiving usable hits. Items_13/14 inspect formatter/preflight input details. No complete assembly/discovery report is dumped during rendering, but normative guidance and manual proposal/cleanup decisions still consume work.
- This establishes useful local work reduction and exposes its limit: validated facts now arrive earlier and carry decision evidence, but the normal Manager still rereads available guidance and performs several proposal repairs. No further repeat is used to select a favorable time. Remaining repeated exploration stays inside #573 criterion 11; it is not moved to another issue. A prospective next bounded investigation should distinguish missing public input guidance from discretionary redundant reads using these exact commands before proposing another patch or measurement; there is no known safe cache change that removes current scope/authority judgments.

PLAN-001's historical protocol fidelity/order remains unresolved despite the new PR baseline. PLAN-010 and strict-order historical gaps remain unrepairable by later red/green tests. PLAN-014 remains incomplete: the accepted cold/warm/changed-input repetitions and exact changed-input match are not supplied by this diagnostic pair, and end-to-end improvement/repeated-inspection elimination are still unproven. PLAN-015 and overall acceptance remain open. No accepted plan, spec, historical row, or user-owned gate was changed or waived.


## Typed decision inputs and fixed-cleanup comparison (iteration 010)

The user accepted the Manager/Develop diagnosis and authorized in-scope corrections followed by a minimal fair full preparation comparison. Historical red/order and performance gates were not waived. No formatter internal, runtime, preflight producer, probe implementation, accepted spec or accepted Plan was changed.

### Corrections and test-first evidence

- `47e704f145a17a426ce29f69361f87f8181375c9` supplies a nested product skeleton derived from the existing `DeliveryContractV3` schema, known-input prefill, explicit unresolved action slots, a decision brief linking selected graph/evidence/limitations/owners, and current owner guidance projections. Public draft-file journeys fill actual decisions and render byte-for-byte equivalent complete formatter output. An unfinished template cannot become an empty action plan or activate a workflow. Closeout examples are checked by the existing validator, including rejection of archive-only inside closeout_plan.
- The first real warm exposed redundant output from embedded guidance and continued preflight-shape code inspection. `7a28330dfdb1fc89a5b2efd898fe4c6cd8ece2f1` adds a plain guidance file with disjoint source section ranges, keeps the embedded option compatible, and documents complete preflight field examples. These examples are input guidance only; original full report references, extra fields, tokens, timestamps and decisions remain with their existing owners. No missing evidence is manufactured.
- Meaningful failing public journeys precede these fixes: `/tmp/issue573-010-red.txt`, `issue573-010-guide-red.txt`, and `issue573-010-file-red.txt`. They map to U14-U16/I01/I06. The first targeted selection passed 72 tests; guidance selection passed 90 plus one preserved-owner-link correction/recheck. Final affected input/preparation selection passed 17 tests. Normal implementation commit hooks passed 330 and 331 tests without bypass. These are current evidence, not replacement historical red/order evidence.

### Why a new baseline was necessary

The prior 383.943-second baseline and 428.170-second corrected warm left cleanup unresolved and produced different action plans. The older 241.438-second median used a local-commit endpoint. None is relabeled as equivalent to a now-fixed PR/preserve-resources preparation. This iteration runs one baseline and one warm with the same explicit cleanup/worktree/gate decisions; after the trace-supported correction, it reuses that baseline for one corrected warm. All three observations are retained, with no automatic retries, provider probes, matrix rerun or case actions.

The original Case B request remains 180 bytes / SHA-256 `960e3b8ed55a5b7de5c8455f836164b946c4c2285a994d126b56dacd246704be`. All three prompts are 2,944 bytes / SHA-256 `55c24c9f92b2a9c38710cb7ea93ccd6d66bdb403468a500138fc1d9346b11bfd`: the prior neutral prompt plus the same explicit preserve-resources, deliver=[], cleanup=[], spec/plan user-owned partition and relative worktree decision. This synthetic case clarification grants no actual delivery authority. Both initial arms and the corrected warm use CLI 0.156.1, Codex/gpt-6-astra/medium, sandbox disabled, synthetic issue `issue573-benchmark-equivalent`, isolated XDG state, identical raw evidence hashes, and the same strategic overlay. Benchmark phase chains retain their fixed Luna develop/pr and Astra other phases; actual #573 Develop remains Astra.

The final proposals preserve the verbatim request, PR-to-develop, pr.auto_create=true, exact six phase models, medium stated separately, zh-TW/en-US locales, identical empty deliver/cleanup, relative worktree, event-driven mode, spec/plan user ownership and mandatory PR confirmation. Current suitability/availability/action-target gaps remain visible. All three checkouts remain clean. Preflight classifications are equivalent (runtime current/0.5.1; catalog identical); source-revision-specific phase digests, comparison tokens, observation times and non-authoritative decision wording differ and are not claimed to be byte-identical. Scope prose and provider reasoning remain nonidentical.

### Complete Manager observations

| Observation | Source | Elapsed seconds | Shell command events | Captured shell-output bytes | Command receipt interval union |
| --- | --- | ---: | ---: | ---: | ---: |
| B-decision-fixed-baseline-1 | `6ce6bddade03e6ee31a60f437accd4d557467c50` | 367.068 | 30 | 379,561 | 4.803 s |
| B-decision-fixed-warm-1 | `47e704f145a17a426ce29f69361f87f8181375c9` | 291.976 | 17 | 304,634 | 4.823 s |
| B-decision-fixed-warm-fixed-1 | `7a28330dfdb1fc89a5b2efd898fe4c6cd8ece2f1` | 309.861 | 16 | 240,022 | 3.920 s |

The latest warm is 57.207 seconds (15.6%) faster than its fixed-decision baseline, with 14 fewer shell commands (46.7%) and 139,539 fewer captured output bytes (36.8%). It is **17.885 seconds slower than the first warm**, despite one fewer command and 64,612 fewer bytes. Different implementation revisions are not pooled as repeated observations. The result is a single-pair improvement signal; it does not estimate variance or prove a causal/stable speedup. It is not compared as a matched speedup against 383.943, 428.170, 487.533 or the old local-commit median.

Baseline also has two completed file-change tool events, both under its private output directory; the warm runs have none. Shell events (30/17/16) and other tool events (2/0/0) are reported separately. Concurrent command receipt intervals are unioned; these counts are observable tool-event proxies, not a reconstruction of hidden batched model request boundaries. Captured aggregated shell output is the reproducible reading-volume unit. It is not unique source bytes, proof of attention, or an exact byte count of post-truncation model context. There are no web/MCP research events or human clarification waits. Final kickoff confirmation remains the endpoint.

Residual elapsed outside command receipt intervals is 362.265 / 287.152 / 305.942 seconds. It mixes generation, reasoning, orchestration, transport and provider waiting; it is not pure model compute. Helper-containing shell envelopes occupy 1.991 / 1.869 seconds in the warm runs, including surrounding request edits, so pure helper time is not isolated. Actual warm-store build/check costs are 5.600 / 5.298 seconds, separately recorded; these do not represent all phase preparation, development, testing or evidence review. No new complete cold run was made.

Cumulative input tokens (including cached input) are 1,085,196 / 779,932 / 697,231; output tokens are 10,059 / 8,104 / 8,647. Lower bytes and fewer tools therefore coexist with a slower second warm; these observations cannot attribute the timing change to cache I/O or to pure reasoning alone.

### Trace audit: actual removed work and remaining gaps

- First warm obtains assembly at item_3 after 2 commands / 24,116 bytes; corrected warm at item_5 after 4 commands / 37,536 bytes. Corrected item_4 reads supplied raw model/delivery records before using the helper; it does not repeat them after a validated hit. Both summaries contain delivery hit with three supporting sources, useful Astra/Luna assessments with limitations and dated provenance, all 13 candidate counts/inspectability, and the selected graph. Corrected model assessment age is about 41,210 seconds, inside the unchanged finite policy; no dates or facts are refreshed to fake a hit.
- The old corrected-warm item_16/17 implementation_direction repair and item_22/23 invalid archive-only repair do not recur. First warm item_16 and corrected warm item_15 each perform one successful complete render after filling the typed draft. Initial incomplete assembly exit 3 is expected and retained; corrected item_3 exit 1 is a file-search miss, not a suppressed render failure.
- The fixed baseline reads evidence twice (items 7/9), repeats kickoff/model/engineering sources, and performs preflight/type render repairs (items 28-32). The warm removes those repairs and the per-candidate/gate exploration loop. Current scope assessment still reads repository product/engineering evidence.
- First warm embeds guidance in item_4, reprints it in item_5, then rereads subsets in items 6/8. Corrected warm emits the summary/index without the guidance body in item_5; the caller reads the plain file in items 6/7. This removes the whole-JSON-then-whole-guidance duplicate. However, item_8 overlaps previously read ranges 445-933, workflow_progress is read before the summary in item_3, and draft JSON is read in both items 7/9. Repeated inspection is reduced, not eliminated.
- First warm reads formatter preflight fields in items 13/14. Corrected warm uses the public shapes and records actual preflight outputs/metadata in item_10 without that preflight-source lookup. It still reads formatter display code (item_14), existing preference implementation/tests (items 11/13), and phase/reasoning setup sources. These are distinct from the repaired input-type/schema lookup and remain disclosed work. No render-internal optimization or reasoning configuration change was attempted.

The remaining overlaps are chosen by the Manager despite supplied disjoint ranges and existing read-once guidance; the raw events do not reveal whether context truncation, reasoning strategy or another cause explains them. There is no evidence that cache I/O causes this behavior. No further provider call is used to hunt for a favorable observation or diagnose that uncertainty. Any further preparation investigation stays within #573; nothing is split out to remove an unmet criterion.

### Evidence and gate status

Raw directories: `/tmp/issue573-kickoff-benchmark/codex-formal/B-decision-fixed-{baseline,warm,warm-fixed}-1/`. Each retains exact request/prompt, setup hashes, stdout JSONL, stderr, separate local event timestamps, command spans, proposal and run record. Scripts and bounded audits are in `/tmp/issue573-kickoff-benchmark/iteration010/` (`result-audit.json`, `proposal-audit.json`, `actual-warm-evidence.json`, `ledger-revalidation.json`). Historical raw hash inventory still matches; none of the original 21, supplementary results, or changed-input mismatches is rewritten.

- baseline proposal: 10,797 bytes / SHA-256 `d23f7c387ffa7403dc09bd9bf2043e865ea50606e165458620b44bdf65d0279b`.
- warm proposal: 10,189 bytes / SHA-256 `c145a34f6299868abf6df75fa8448b87b6aaed0ef465433f74ddd6fbaf0f7149`.
- warm-fixed proposal: 11,265 bytes / SHA-256 `1f2c0e6b3a2f7c77b747e68c454feb5bcf76a5e8a474e2058eb995e239cd2c3a`.

PLAN-001 and PLAN-010 historical fidelity/red/order gaps remain open and cannot be repaired retroactively. PLAN-014 has new favorable single-pair evidence but remains incomplete: accepted three-per-condition Case A/B and changed-input protocol, variation, original input fidelity gaps and complete repeated-inspection elimination remain unmet. PLAN-015/overall acceptance also remains open. These historical gates never prevented the authorized technical corrections above; they and the remaining measurement conditions prevent a successful Develop handoff. No accepted criterion, Plan, spec, mandatory PR gate or closeout command is waived or changed.


## Original-condition caller/store corrections (iteration 013)

The user authorized continued bounded corrections and necessary original-condition
observations until verifiable progress, with a new verified defect/fix or concrete
information hypothesis before each invocation. The historical red/order exception
is accepted; the iteration 011 acceptance amendment is still unapproved. No
accepted specification, Plan, Manager contract, formatter internal, runtime,
preflight producer/validator, or model-probe implementation was changed.

### Fixed measurement definition

Original baseline is retained: 238.081 / 350.333 / 241.438 seconds; median
**241.438 seconds**, source `6ce6bddade03e6ee31a60f437accd4d557467c50`.
Original shell command counts are 16 / 19 / 18 and captured output bytes
276609 / 336270 / 314693 (medians 18 and 314693). Every observation below reuses
all three baselines' identical 1757-byte prompt, SHA-256
`dc1516efca94b75887b2ffccf30c6c1bc3396585fcf908e7d6f91c4454f01e92`.
The embedded request is 180 bytes / SHA
`960e3b8ed55a5b7de5c8455f836164b946c4c2285a994d126b56dacd246704be`.
Its original PR request followed by a fixed local-commit answer is preserved.
The proposal endpoint is local commit / pr.auto_create=false; proposal-only
forbids executing case actions. CLI 0.156.1, gpt-6-astra, medium, sandbox disabled,
ephemeral session, ignore-user-config and original argument order are unchanged.
Neither the later 367-second PR baseline nor a fastest warm is the denominator.

Each source revision is a separate diagnostic observation, not a repetition or a
new median. There is no automatic retry or additional model/provider probe. All
adverse timings, tool failures, original 21 observations and later supplements
remain preserved. Checks below are the existing required read-only preparation
checks, not extra model probes. Actual #573 Develop remains Astra; the fixed
synthetic proposal phase chains retain their original Luna develop/pr answers.

### Observations, including every unsuccessful correction

| Run suffix | Source | Complete elapsed (s) | Delta vs 241.438 | Shell events | Captured output bytes | Shell receipt interval union (s) | Warm store build/check (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| stores-1 | `eac5ef3e2ef4` | 289.147 | +47.709 s (+19.76%) | 19 | 305391 | 11.193 | 5.664 |
| actions-1 | `99b155ac5806` | 256.137 | +14.699 s (+6.09%) | 20 | 355287 | 8.022 | 5.584 |
| overview-1 | `7fd8fb3f2475` | 297.136 | +55.698 s (+23.07%) | 21 | 316200 | 6.804 | 5.984 |
| fields-1 | `2de684148784` | 338.831 | +97.393 s (+40.34%) | 22 | 300255 | 9.404 | 6.261 |
| reports-1 | `04331ff97fc5` | 247.066 | +5.628 s (+2.33%) | 19 | 294747 | 10.568 | 5.663 |
| capture-1 | `8887c70b5082` | 280.198 | +38.760 s (+16.05%) | 15 | 286135 | 8.374 | 5.864 |
| null-1 | `dc7f3446589e` | 298.522 | +57.084 s (+23.64%) | 16 | 335077 | 8.346 | 5.678 |
| dependencies-1 | `dc422c0e140b` | 257.470 | +16.032 s (+6.64%) | 16 | 291189 | 7.032 | 5.789 |
| race-1 | `4734c7c63b5a` | 302.110 | +60.672 s (+25.13%) | 22 | 418441 | 7.264 | 5.537 |

The previous iteration 012 observation remains 279.371 seconds / 19 commands /
376096 bytes: its Manager overwrote inherited XDG locations and missed the
prepared stores. It is not relabeled as a warm hit.

### Concrete correction chain and trace evidence

1. `eac5ef3e2ef43161e00b83438f1e0856653242c8`: public store locator and pinned
   continuation argv retain effective XDG/config/cache and repo identity while
   keeping output directories separate. Explicit isolation overrides are honored.
   Actual stores-1 item_5/6 uses the prepared stores and useful delivery/model
   assessments. It exposes description shape/count errors in items 17/18.
2. `99b155ac5806ff632ca1abb2ce36269d72815ce9`: public action shapes and existing
   formatter-owner assembly validation remove those description repairs.
   actions-1 renders once at item_20; remaining candidate/source reading stays visible.
3. `7fd8fb3f247581ce7b7ccd6a2feafed381fc9c65`: selected summaries retain a compact
   comparison overview for every effective candidate, with eligibility, roles,
   provenance and inspectability. The public journey selects an alternative graph
   from that overview, reuses the index, and renders equivalent complete output.
   overview-1 removes candidate YAML loops but still inspects formatter fields.
4. `2de68414878492b1e9e7d68a9f53015cb626fb4e`: all public adapter field types and
   real parser choices are projected from existing flag maps/owner declarations.
   fields-1 still manually reconstructs preflight reports and repeats checks.
5. `04331ff97fc56f8602a887b1e106941d5cf0cecc`: full raw report files plus actual
   current metadata map through the input adapter without source-field loss,
   fabricated tokens/timestamps, or preflight execution changes. reports-1 item_20
   uses the mapping, but reruns earlier checks merely to save their outputs.
6. `8887c70b50822b00696937b4cd7996f544c3c35c`: capture-report receives existing
   check JSON stdin, preserves original bytes and caller-supplied actual time,
   and references it in the editable draft while leaving decisions unresolved.
   capture-1 item_13 runs each check once and captures both; its item_15 reveals
   an added adapter null restriction, repaired in item_16. This failure is retained.
7. `dc7f3446589eb315ebac15fb79a5bf580f5f93ec`: restores the unchanged formatter's
   acceptance of explicit absent post-change evidence; no invented explanatory
   fact is required. null-1 removes that repair but still reads lifecycle/PR sources.
8. `dc422c0e140bccef563f2c252d2d525472c62c1b`: explicitly referenced delivery
   source dependencies outside heuristic prefixes can now be reused when present
   in the repository inventory, contained in the repository after resolution,
   and fingerprint-matched. Related edits invalidate; unrelated implementation
   edits preserve reuse; stale refresh and outside-repository symlinks fail.
   This permits a normally refreshed warm record to contain actual lifecycle/hook
   evidence rather than only three general PR conventions.

Every correction has a meaningful public behavior red before green. Current logs
are `/tmp/issue573-013-{stores,actions,overview,fields,reports,capture,null,dependencies}-*.txt`.
Targeted checks cover U07-U11/U14-U16 and I01/I03-I06 as appropriate; they exercise
normal CLI journeys, real formatter output equality, explicit isolation, source
invalidation, incomplete decision refusal, and no activation. Normal commit hooks
passed 328, 333, 333, 334, 335, 336, 336 and 338 tests, respectively. The actions
selection's partial-argv compatibility failure was corrected before its commit;
it and the capture null regression remain recorded. Later red/green does not
rewrite the historical PLAN-001/010 order or missing-red evidence.

### Evidence state and comparability limits

All runs use private per-run source/config/cache roots and the normal evidence
refresh interfaces. Actual helper outputs, not just prelaunch warming, identify
the effective paths, repository key, delivery hit and both exact-model assessment
payloads/provenance. Model evidence files and their original dates are unchanged;
no availability probe, refreshed date, dynamic observation, case decision or full
proposal was injected. Earlier runs use three general PR conventions. The dependencies and race
runs instead have nine documented general facts backed by eight source
files, including lifecycle.py, cafe-pr/SKILL.md, completion_and_authority.md,
.githooks/pre-commit and engineering-guidelines.md. This evidence-content treatment
is explicit; it is not claimed to be byte-identical to earlier warm fixtures.
Each actual source fingerprint is recorded and verified in the run's evidence.

The original runner did not save ambient XDG values or Python resolution; these
cannot be reconstructed exactly. Current runs explicitly isolate XDG and select
their source through PYTHONPATH, remove BENCH shortcut variables, and have no
strategic overlay. Clone origin matches the original local baseline repository;
source files are read-only. Current time, provider state and checkout paths differ.
The latest source already contains preference functionality; current scope judgments
therefore discuss gaps rather than the original baseline's greenfield scope.
Identical prompts fix endpoint/models/locales, not every checkout, cleanup,
clarification policy, scope wording or generated length. reports-1 proposes a
current checkout with an explicit detached-HEAD limitation; others propose worktrees.
No identical-workload or stable causal claim follows from prompt equality alone.

Elapsed is the entire invocation through the final complete user-facing proposal,
not just the formatter call or last event receipt. reports-1's last event at
246.523 seconds is distinct from its full 247.066 seconds; the interim report was
corrected. Local warm store build/check time is separately listed above. Source
inspection, tests, commits and phase preparation are outside Manager elapsed and
are not all represented by the roughly six-second store setup figure.

Shell events are observable completed commands, not hidden provider round trips.
UTF-8 aggregated shell output bytes are a reproducible reading-volume proxy, not
unique bytes, attention, or exact post-truncation context. Command receipt intervals
are unioned, not summed across parallel commands. Residual elapsed combines model
generation/reasoning, orchestration, transport and provider waiting; it is not
pure reasoning. Helper-containing shell envelopes do not isolate pure helper CPU
time. No human clarification wait occurs before complete confirmation. No new
cold/changed-input matrix or variance estimate is supplied by these distinct
revisions, and their results must not be pooled as repeated measurements.

Raw run directories are `/tmp/issue573-kickoff-benchmark/codex-formal/B-original-conditions-<suffix>-1/`.
Each preserves prompt/request, setup/source/evidence hashes, raw streaming JSONL,
event receipt timestamps, stderr, command spans, proposal, run record, isolated
stores and copied Manager artifacts. Actual evidence audits accompany completed
runs. Setup/runner scripts, source fact preparation, and ledger revalidation are
under `/tmp/issue573-kickoff-benchmark/iteration013/`.

Full accepted performance/overall conditions remain open. The historical red/order
exception does not approve the pending iteration 011 amendment. Original A/B
cold/warm repetitions and valid fixed changed-input comparative coverage remain
unsatisfied; precise pure-reasoning/wait attribution is unavailable from these
traces. Repeated general guidance and some source inspection remain visible.
PLAN-014/015 are not marked complete, and Review/QA and mandatory human confirmation
remain required. No existing requirement is silently removed or split into another
issue.


### Interrupted-run reconciliation and presentation diagnosis

The ninth observation, race-1, finished before the Manager stopped the worker;
there was no unfinished benchmark at interruption. Revision
`4734c7c63b5af364c8c3e06c62f616ff2b8b63c6` fixes parallel capture lost updates with
the existing file lock. Its public concurrency red/green and full-render journey
pass, and hooks pass 334 tests. Both report references survive in the actual
race-1 draft; item_9 contains correct stores, eight delivery sources/nine facts
and useful exact-model payloads. Nevertheless reading grows to 418441 bytes,
22 commands and 302.110 seconds. Store hits alone did not address presentation.
A later negative-input guard, `74a9a15071858fe5e37b445b4dfcae50f29692fb`, rejects
an unknown delivery source with no fingerprint rather than accepting equality
of two absent values (8 targeted tests, 339 hook tests); it has no standalone
benchmark. Historical measurements are not relabeled as testing that guard.

Reports-1 completed its final cat of the 5991-byte rendered document at
162.353826764 seconds. Its final 8891-byte agent message arrived at
246.493753575 seconds: 84.139926811 seconds with no shell tool activity.
Fields-1's corresponding gap is 121.931395268 seconds. The final documents
restructure/localize and repeat already-rendered facts, plus some useful
limitations. These intervals include output generation, translation/rewrite,
orchestration, transport and provider waiting; they are neither pure reasoning
nor entirely avoidable time. Original baseline-3 also rendered first (6666 bytes)
then produced a final message (10790 bytes), and lacks receipt timestamps.
Thus the newer path is not proved to have introduced a second generation stage;
unnecessary duplication within that existing stage is the observable concern.

The resumed public CLI regression reproduces a concrete projection defect:
`kickoff_guidance()` omitted the complete Render the proposal owner section,
while projecting full established-workflow progress and attached polling policy.
Revision `015517dcf9826559d0604d578e43ce1e7ad3f1a7` includes the presentation
section, projects kickoff-only progress rules, retains checkout/strategy/authority
and suitability rules, and routes initial reading through that projection.
The canonical presentation owner now explicitly retains already-localized
rendered blocks verbatim, translates only remaining presentation text and avoids
a second introduction/rationale/recap. Literal commands, policy semantics,
complete output, locale and the one confirmation/diagram remain mandatory.
No formatter internals, runtime, preflight or new helper interface changed.

The meaningful public missing-section regression fails before this correction;
four related guidance/draft/render journeys and 91 kickoff/preflight regressions
pass afterwards, with 339 normal-hook tests. The journey consumes disjoint
owner ranges, supplies actual decision inputs and produces exactly the unchanged
formatter's full output without activation. Projected guidance is 61485 bytes,
versus 64893 before. This is local diagnostic evidence, not proof that a real
Manager obeys the reading/presentation route or that model elapsed improves.
Logs: `/tmp/issue573-kickoff-benchmark/iteration013/presentation-{red,green,guidance-tests,commit}.txt`.


### Presentation-policy observation (retained, no improvement)

`B-original-conditions-presentation-1`, source `015517dcf9826559d0604d578e43ce1e7ad3f1a7`:
**309.257 s**, **+67.819 s
(+28.090%)** versus the original 241.438 s median;
20 completed shell commands, 347823 captured UTF-8 bytes, 7.544 s receipt union.
Warm state build/check: 5.782 s, separate from the full Manager interval.
Same 1757-byte original prompt/hash and CLI/model/effort/sandbox/endpoint settings.
The actual item_6 uses the prepared stores and useful delivery/model assessments.
One successful render occurs in item_21, full output read in item_22.
Local commit, pr.auto_create=false, full phase chains, user/mandatory gates,
limits, exact argv and confirmation boundary remain visible; no case action ran.

The formatter output is 10310 bytes, final proposal 10860 bytes. Last tool at
207.515305465 s, final message at 308.769984963 s:
101.254679498 s without tool activity. Source-to-final reading
shows rewritten product bullets, some English headings and added raw argv beside
the shell command. The document is not a verbatim same-language relay. No claim
that the revised guidance eliminated rewriting or reduced end-to-end time is made.
The public local regression established available policy and full render, not
model compliance. The actual trace remains the decisive negative evidence.

Item_7 reads the full 61485-byte guidance, then 11/12/13 reread overlapping ranges.
Item_8 reads CONTRIBUTING and strategy docs, 10 rereads CONTRIBUTING, 18 rereads
positioning/roadmap. Item_8 also searches nonexistent src/cafe/cli.py (exit 2).
Items 15/16 capture each required check once; no capture race/null repair recurs.
Item_20 reads phase/Codex configuration to address requested reasoning settings.
Original prompt explicitly asks for model/reasoning and missing evidence; these
cannot simply be removed to shorten the workload. All raw records, copied Manager
artifacts and actual cache/endpoint audit are retained in that run directory.

A further owner-policy conflict is visible: the summary permits valid evidence
reuse, but the delivery owner still directs raw repository inspection at every
kickoff and the model owner independently directs reading the same strategy,
implementation and tests. The bounded documentation correction makes validated
facts satisfy inspection only for covered unchanged conventions, reuses already
gathered observations for model judgment, and retains current mandate/strategy,
missing-fact research and current target/authorization checks. Existing public
store/summary/complete-render journeys pass (3); this does not prove actual agent
reading behavior. The next observation tests this explicit information hypothesis,
not a retry of the same source or another helper interface.


### Source-policy observation (retained, no improvement)

`B-original-conditions-source-policy-1`, source
`7cb84c83e3c964f284c76d33f8d281ab841a89cf`: **267.050 s**, **+25.612 s
(+10.608%)** versus 241.438; 18 commands, 347559 captured bytes, 7.643 s
receipt union. Warm build/check cost is 6.363 s, separate from Manager elapsed.
Actual item_7 and its retained summary confirm prepared stores and useful
source-backed delivery/model hits. One render succeeds (item_19); last full
contract read is item_21 at 177.957362686 s, final message at 266.606660232 s,
a further 88.649297546 s without tools. Formatter output is 6736 bytes and final
proposal 9167 bytes. Final presentation still rewrites and adds an introduction;
local commit/pr.auto_create=false, fixed chains/locales, gates, exact actions,
explicit gaps and proposal-only boundary remain visible. No case action ran.

The source-policy text change is not demonstrated to eliminate repeated reading.
Item_2 cats SKILL plus kickoff/input/selection/model references (102697 bytes),
item_3 rereads kickoff_inputs (23273 bytes), then 10/12/14 read overlapping
projected/owner policy. Engineering guidance is read twice (11/12).
Each runtime/catalog check executes once (16/17), and no render type repair occurs.
The first combined read includes post-confirmation prepare/activation examples
and input-maintenance material unrelated to the current proposal endpoint.
Normal reference staging is the next concrete defect to address; changing only
the projection cannot reduce a caller's direct read of those complete references.
All raw logs, final proposal, original exact prompt/hash, copied Manager artifacts
and actual cache/endpoint audit are retained. No reclassification as a speedup.


### Staged-reference correction and observation

Revision `dd00732d4ec30baadb3693f5b3fe461596e3c259` moves post-confirmation
prepare/activation/polling and direct CLI examples to `kickoff_execution.md`, and
advanced request/staged/maintenance formats to `kickoff_input_reference.md`.
The normal entry links to both; all moved blocks were checked verbatim against
the predecessor. Checkout recommendation remains in the preparation decision
section. No implementation owner, runtime, preflight, formatter, saved authority
or accepted requirement changed. The public CLI journey first exposes execution
examples in the normal proposal read, then verifies their separation plus the
same complete renderer output and no activation. Three related journeys, 95
owner checks and three adjusted owner-path checks pass. All policy assertions
remain; the initial hook attempt exposed three further old-path tests (495 pass,
3 fail), then all 498 pass with the actual linked owner paths. No hook bypass.

| Latest original-condition observation | Value |
| --- | ---: |
| Run | B-original-conditions-disclosure-1 |
| Complete Manager elapsed | 260.021 s |
| Difference from original 241.438 s median | +18.583 s (+7.697%) |
| Completed shell commands | 18 |
| Captured UTF-8 output | 293172 bytes |
| Difference from original volume median 314693 | -21521 bytes (-6.839%) |
| Shell receipt interval union | 8.364 s |
| Warm store build/check, outside Manager elapsed | 6.013 s |
| Rendered document / final message | 8311 / 10280 bytes |
| Final-tool-to-final-message interval | 95.474142208 s |

Original baseline observations remain 238.081 / 350.333 / 241.438 seconds;
the latest single run is not a median. It is +21.940 s versus baseline 1,
-90.312 s versus baseline 2 and +18.583 s versus baseline 3. The original
1757-byte prompt/hash, CLI 0.156.1, Astra, medium, sandbox-disabled ephemeral
session and local-commit/pr.auto_create=false proposal endpoint are unchanged.
Actual item_6 confirms prepared store paths, repository identity, delivery hit
and both useful exact-model assessments. This is actual caller evidence, not
just a prelaunch claim. One render succeeds (19), full output is read in 21,
and no case workflow, commit, preference mutation, PR or closeout executes.

Initial reads now use SKILL then the smaller input reference before assembly.
Nevertheless item_7 cats the full 62661-byte guide, 10/12/14/18 reread sections,
8/11 repeat CONTRIBUTING/engineering guidance and the preflight owner is reread.
Both checks run once in 16/17; capture and render repair failures do not recur.
The tool-free tail runs from 163.995439949 to 259.469582156 seconds and still
includes rewriting: the final adds an introduction, rephrases facts, repeats raw
argv and shell command and changes closeout diagram labels/status wording.
Some presentation headings/prose remain English. The fixed decisions and full
proposal sections are present, but exact complete presentation equivalence is
not established; this is not an accepted performance/functional completion sample.
The full invocation ends at 260.021 s, not at the final event receipt.

Cumulative provider-reported input (including cached input) is 623802 tokens,
cached input 557824, output 7202 and reported reasoning output 277. Original
baseline-3 has 510901 input, 447104 cached and 6900 output. These are cumulative
turn counters, not unique evidence volume or measured pure reasoning duration.
Lower captured bytes do not prove lower total model work or stable latency.
All twelve distinct-revision observations are retained; none is pooled into a
warm median or selected as proof of improvement. The observed work reduction
and correct stores are real, but the original-condition elapsed requirement
remains unmet. The original cold/changed-input coverage gaps also remain.

### Concrete boundary requiring a scope decision

The resumed work verified three successive hypotheses: include missing complete
presentation policy, remove unconditional rereading directions in owner policy,
and physically stage execution/maintenance references. Their complete times are
309.257, 267.050 and 260.021 seconds. None beats 241.438. The last run reduces
captured bytes, yet still has an approximately 95-second final presentation tail.
No new reproducible store/schema/adapter defect was identified in that trace;
another same-source run or another wording-only change would not be evidence
of a known production fix. This is not a claim that all possible in-scope
improvements are impossible.

`format_kickoff_contract.py:render` (around lines 952-1011) emits fixed English
headings/table labels/explanatory prose even with zh-TW input, mixed with localized
closeout/progress. The current owner policy requires the Manager to translate
that output. A concrete next proposal is to make that existing owner emit fixed
presentation text in the effective supported locale, retaining Manager-supplied
free-form facts, then verify exact commands, complete fields and diagram facts
at the presentation boundary. No second renderer, contract owner, tool execution,
new authority or acceptance relaxation is proposed. This would remove a required
translation operation; it does not promise to remove all final-generation time
or prevent every autonomous rewrite.

That proposal crosses the user's explicit prohibition on formatter-internal
changes and the accepted exclusion of standalone formatter optimization. It is
not implemented or measured. Manager must obtain the bounded scope decision
through existing spec/plan confirmation before such work. Preserving the current
boundary leaves this candidate unavailable and all unmet gates open. Historical
red/order exceptions remain accepted separately; no new waiver is requested.
Raw records/audits: `/tmp/issue573-kickoff-benchmark/codex-formal/B-original-conditions-disclosure-1/`.

## Iteration 014: decision reading path — implementation and unsuccessful endpoint

The user approved the bounded reading-path correction after the Manager/Develop
consensus. The earlier formatter-internals proposal remains deferred and
unapproved; no accepted criterion, plan, authority or formatter implementation
changed. Historical red/order exceptions retain their narrow meaning.

The subsequent diagnostic baseline replay (`B-baseline-timed-diagnostic-1`)
changes the interpretation of the earlier presentation hypothesis. It took
293.777 s, versus disclosure-1's 260.021 s, with preparation reads 79.132/92.845,
drafting/render/repair 102.820/65.338, post-render tools 8.509/5.813, and final
response through process exit 103.316/96.026 seconds respectively. Receipt unions
were 12.211/8.364 seconds. The replay includes a list/string validation error
and 44.111-second repair interval. Neither attributing the whole difference to
caching nor subtracting that repair establishes causation. Both versions have a
long final-response tail. Localization is a possible optimization, not an
established regression cause. The replay is diagnostic only: it does not replace
the original **238.081 / 350.333 / 241.438 s**, median **241.438 s**.

### Bounded correction and local verification

Source `3411c65e3f6d16a4379c7651839dd453cfde58f8` joins current fixed choices,
validated evidence and missing fields to explicit questions and current owner
sections. The reading list groups disjoint source ranges by file with hashes and
literal read argv. A valid current graph defers the selection-without-a-choice
section, while scope, QA applicability, suitability, authority, gates, locale,
preflight and complete presentation judgments remain. The existing generic
strategy resolver supplies document metadata; no policy store or new authority
is introduced. Literal workload coverage references are not model assignments.
Source changes, expiration and uncovered workloads restore affected questions.

The normal `assemble --summary --draft-output` response uses the typed file as
the editable copy and supplies current owner field shapes; it avoids repeating
the full draft/schema. Summary without a draft, explicit full guidance and full
assembly remain available. The normal entry uses the source reading list rather
than requiring a concatenated guide file. Formatter/runtime/preflight/probe
internals are unchanged.

Five new public CLI examples failed first on absent fixed-input/question
behavior. An initial green attempt passed four and exposed an inconsistent test
locale in the fifth; correcting the fixture preserved the existing conflict
validator. Then eight related journeys passed, followed by the final nine-case
related selection and four input unit tests. They exercise real isolated stores,
source/expiry/workload gaps, mandatory judgments even with complete fields,
source spans, one successful full render with equivalent output and no activation.
Mappings: U05/U12/U14-U16, I01/I05/I06. Normal commit hooks passed **344 tests**;
no bypass. These checks establish the adapter behavior, not actual agent compliance.

### One authorized original-prompt observation

Run: `B-original-conditions-reading-1`. The original 1757-byte prompt SHA
`dc1516efca94b75887b2ffccf30c6c1bc3396585fcf908e7d6f91c4454f01e92`, CLI 0.156.1,
Astra/medium, sandbox-disabled ephemeral session and fixed local-commit proposal
endpoint were retained. Exactly one provider invocation ran, with no retry or
probe. Historical environment values remain unreconstructible; current isolated
XDG/PYTHONPATH, source path/revision, service conditions and generated prose differ.
Delivery fixture facts and model assessment dates are the same as disclosure-1;
validity was rechecked against the actual new source/repository identity.

| Observation | Value |
| --- | ---: |
| Original baselines | 238.081 / 350.333 / 241.438 s |
| Historical baseline median | 241.438 s |
| New invocation elapsed, **failed complete endpoint** | 239.514 s |
| Arithmetic difference from historical median | -1.924 s (-0.797%) |
| Difference from each original baseline | +1.433 / -110.819 / -1.924 s |
| Completed shell commands | 14 |
| Captured UTF-8 output | 337654 bytes |
| Versus disclosure-1 output | +15.173% |
| Versus original volume median 314693 | +7.296% |
| Shell receipt interval union | 1.526 s |
| Local warm build/check, outside invocation | 5.604 s |
| Additional local normal-path check, outside invocation | 0.776 s |
| Final manual draft | 11073 bytes |
| Successful formatter output | **None** |

**This is not a qualifying speedup or a complete comparable proposal.** Item_17
attempts render without update/catalog reports; render returns 3 and creates no
proposal file. The wrapping shell exits zero, and the provider also exits zero.
Manager instead writes a manual final draft that explicitly admits the missing
reports and defers a formatter-validated contract. It did not execute either
required preflight check. Fewer commands and the lower receipt union therefore
include omitted required work, not demonstrated elimination of equivalent work.
No data was fabricated to bypass the rejecting validator.

The draft retains standard-qa, zh-TW/en-US, the six fixed primary-only chains,
local commit and pr.auto_create=false. It proposes the commit as develop work,
with empty closeout deliver/cleanup, and preserves user/mandatory boundaries.
Those fixed choices do not repair the absent complete formatter endpoint or
establish equivalence of its manually reconstructed graph/presentation. It also
assesses the already-present preference implementation rather than the original
pre-change implementation surface. Item_8's repository search exposes excerpts
from this benchmark report, another disclosed source-context difference.

### Actual caller audit against the requested repeat reads

| Prior target | Current trace and outcome |
| --- | --- |
| Full guide then kickoff/strategy slices | No guide file is produced. Item_9 prints the entire 60880-byte source union; item_11 repeats kickoff/playbook/strategy and 13/15 repeat strategy. **Not eliminated.** |
| CONTRIBUTING/engineering repeated in 8/11 | Both read once in current item_8; that specific duplicate is absent. |
| Preflight owner repeated in 12/14 | No repeated owner read after item_9, but neither check runs. This cannot count as an equivalent complete preflight improvement. |
| Schema query and implementation lookup | No schema subcommand. Item_4 still cats prepare_kickoff.py; 12 reads preference implementation/tests; 14 reads the advanced input reference. Implementation lookup is not eliminated. |
| Repeated report copying | Item_6 emits 61538 bytes; item_7 reprints 24643 bytes of brief/index. The normal brief is still too large to reliably avoid additional reads. |
| Correct cache reuse | Actual item_6 and saved summary show pinned prepared stores, correct repository identity, delivery hit and both valid model assessment payloads. Cache I/O is not the demonstrated cause of repeated policy reads. |

The pure local normal-path response measured 60204 bytes before issue-specific
fields; its decision brief alone was about 24 KB in diagnostic JSON serialization.
A file-grouped read list still allows the agent to concatenate all sources into
one large response. The observed repeated reads and manual endpoint are concrete
failures of the intended normal journey. Output truncation as their cause is not
proven by these records; captured bytes are not guaranteed post-truncation model
context. Merely adding more instructions would not establish a correction.

Receipt-based segmentation, explicitly **not** the successful-render segments:
through the last preparation read 84.502 s; thereafter through the rejected
render 44.478 s; rejected render through process exit 110.535 s. Last tool to
final message is 110.003 s. Receipt intervals are not precise subprocess CPU or
wall execution, and the remainder mixes generation, orchestration, translation,
transport and provider waiting. There is no measured successful post-render
stage. Cumulative usage: 494796 input, 425984 cached input, 6780 output and 869
reported reasoning-output tokens. None isolates pure reasoning seconds.

All original 21 observations, twelve earlier distinct-revision original-condition
observations, the diagnostic baseline replay and this failed observation remain.
No favorable median is constructed, no historical evidence is relabeled, and no
second invocation follows this failure. PLAN-001 nonhistorical coverage,
PLAN-014/015 and criteria 9-12 remain open. This reading correction did not satisfy
the requested actual-repeat-elimination/complete-proposal success condition.
Further work needs a concrete correction to the still-large normal read payload
and its incomplete-evidence transition, rather than another unchanged-source run.
The pending formatter scope proposal is not approved by this result.

Raw evidence, complete streams/receipt timestamps, command outputs, prompt,
manual proposal, copied Manager request/draft/summary and endpoint audit:
`/tmp/issue573-kickoff-benchmark/codex-formal/B-original-conditions-reading-1/`.
Local red/green/hook logs and reused setup/runner scripts:
`/tmp/issue573-kickoff-benchmark/iteration014/`.

## Iteration 015 — indexed sources and same-draft report continuation

Source: `d0f4b3ef6049dc6bc446bfebe3758a9de2099e31`. This section preserves all
prior observations, including iteration 014's **invalid** 239.514-second result.
There was one newly authorized provider invocation, no retry or probe.

| Original-condition observation | Full elapsed (s) | Completed shell commands | Captured command-output bytes |
| --- | ---: | ---: | ---: |
| Original B baseline 1 | 238.081 | 16 | 276609 |
| Original B baseline 2 | 350.333 | 19 | 336270 |
| Original B baseline 3 | 241.438 | 18 | 314693 |
| Original baseline medians | **241.438** | 18 | 314693 |
| Latest single continuation run | **272.097** | **16** | **340078** |

The latest complete observation is **30.659 seconds (12.698%) slower** than the
historical elapsed median. It is one observation, not a new median. Captured
output is **8.067% greater** than the historical output median and **0.718%
greater** than iteration 014. This does not demonstrate the required speedup or
elimination of repeated reading. Required work omitted in iteration 014 cannot
be counted as a successful reduction relative to this complete observation.

### Correction and local verification

The normal `assemble --summary --draft-output` response now uses short owner
section IDs, one field-shape index and local JSON references to one provenance
index. Question gaps refer to those definitions; formatter-supplied fixed values
refer to the editable draft. Whole-file union commands are replaced by section
ranges and one command template. Full legacy assembly, guidance and schema remain
inspectable. All seven current scope/model/authority/confirmation/locale/
preflight/presentation judgments remain, including for complete input sets.

A missing preflight report now returns a blocked assembly continuation with the
existing check argv and capture argv targeting that same draft. In the prior
failure, `formatter_inputs` in the request was an object; assembly deliberately
returned null because reports were missing, then render misleadingly reported a
type error. The adapter now stops before that null call and reports the actual
gaps. Existing complete report validation is unchanged. Capturing facts leaves
report decisions unresolved and grants no action authority.

Two public CLI regressions failed before implementation. They then passed with
source/workload/expiry/mandatory-boundary journeys (7 selected checks). Adjacent
checks exposed one legacy full-guidance response-size regression; keeping the
new continuation in the normal projection repaired it (12 adjacent passes,
followed by 3 affected passes). Normal implementation commit hooks passed **346**
tests without bypass. Tests cover real isolated evidence, provenance resolution,
model workloads/limits, same-draft complete raw-report capture, unresolved
report dispositions, exact complete formatter equivalence and no activation.
Mappings: U14-U16/I01/I06, plus retained U05/U12/I05 boundaries.

Local normal-response size was **57382 bytes**, versus the previous local
reading-path fixture's 60204 bytes. Paths/source/request context differ slightly;
this is diagnostic projection size, not end-to-end improvement. Actual Manager
pretty-printing and repeated output erased this saving.

### Observed caller behavior

Raw run and timestamped receipts:
`/tmp/issue573-kickoff-benchmark/codex-formal/B-original-conditions-continuation-1/`.
`endpoint-reading-audit.json` includes source, actual stores/payloads, sections,
fixed-decision checks and hashes of copied `manager-artifacts`.

- Item 4 still reads the complete `kickoff.md` before first assembly. The former
  generated guide union is absent, but early whole-owner reading remains.
- Item 7 pretty-prints the entire response (84261 bytes); item 8 repeats brief,
  continuation and evidence sections (40311 bytes).
- Items 10/12 repeat model selection lines 122–250 and project/global sync lines
  1–83. Items 11/13 repeat CONTRIBUTING, language policy, positioning and roadmap
  content; engineering-guidelines itself is read once.
- No schema subcommand or formatter implementation read occurs. Item 13 still
  reads helper parser lines 55–90; item 15 searches nonexistent preflight globs
  and exits 2. No formatter type-repair loop occurs.
- **Item 14 executes both required existing checks once**, captures their full
  original JSON via the returned argv into the same draft, and prints reports
  for current disposition. This is one shell tool round trip containing two
  check subprocesses and two capture subprocesses, not one logical check.
- Item 17 supplies the actual `current` / `not_requested` report decisions and
  succeeds on the **first render**. Item 18 reads the complete 9815-byte formatter
  output. The final 10964-byte Chinese proposal retains product scope, fixed
  locales/chains/gates, local commit/no PR, literal argv, empty cleanup,
  uncertainty disclosures and full confirmation boundary. It rewrites the
  rendered presentation rather than copying it byte-for-byte.

The real caller used the intended isolated XDG stores and repository identity;
validated delivery evidence and both dated exact-model assessment payloads were
hits. Model workload gaps remained disclosed. No raw evidence inspection was
needed simply to recover those payloads. No case workflow, commit, PR, model
probe, saved preference change or cleanup occurred; source checkout remained
clean. The endpoint repair is demonstrated; reading elimination is not.

### Timing and comparability limits

| Receipt-defined segment | Seconds |
| --- | ---: |
| Through last preparation read, including report checks | 87.666 |
| Then drafting through successful render | 79.723 |
| Post-render tool/readback interval | 4.174 |
| After last tool through process exit | 100.534 |
| Total | 272.097 |

Command receipt interval union: **4.288 s**. Receipt buffering and combined
commands prevent exact helper/check execution-time attribution. The final tail
includes generation, possible translation/rewording, transport and provider
waiting; it is not pure reasoning or entirely removable cost. Both the original
source diagnostic replay and earlier corrected runs already had long tails.
Usage: 657241 input tokens (583424 cached), 7992 output tokens, 412 reported
reasoning-output tokens. Captured stdout/stderr is an observable reading-volume
proxy, not proof every byte was attended to by the model.

Clone setup took 0.099 s; isolated warm write/check took 5.816 s; extra local
normal-path validation took 0.990 s. These are separate from phase investigation
and Manager elapsed. Original model evidence dates were retained; no fabricated
freshness, cached proposal or new decision answer was injected.

The original 1757-byte prompt SHA remains
`dc1516efca94b75887b2ffccf30c6c1bc3396585fcf908e7d6f91c4454f01e92`:
its fixed local-commit answer overrides its initial PR request unchanged.
CLI 0.156.1 / gpt-6-astra / medium / sandbox disabled / ephemeral session and
original argv structure are retained. Historical source is
`6ce6bddade03e6ee31a60f437accd4d557467c50`. Historical XDG/Python/provider conditions
cannot be fully reconstructed. Current source already implements much of the
case's preference feature; generated scope text, future action detail and other
nonfixed judgments differ. Fixed-input equality does not establish identical
model work or causal attribution.

The diagnostic replay's 293.777 s (including a 44.111 s type repair) remains a
diagnostic observation, not a replacement baseline or a subtractable causal
control. No new cold/changed-input measurements were performed. The original
changed-input mismatch and incomplete warm evidence remain in the record.
PLAN-001 nonhistorical coverage, PLAN-014 and PLAN-015 remain open. The narrow
historical red/order exception is unchanged; no performance acceptance revision,
formatter-scope expansion or successful Develop handoff is implied.

## Iteration 017 — current-decision view, one original-prompt observation

The authorized bounded correction is source revision
`070b5bc0cd992ed9041890add98f36d736276663`. It adds a compatible
`--summary decisions` presentation to the existing locator/assembly route,
retains bare-summary JSON, and reconciles the normal entry documents. No
formatter, runtime, preflight/probe implementation or accepted contract changed.
The public journey also exposed and fixed null assessment indexing on expiry
and a missing type reference for the composite checkout decision. Source
validation and current authority/suitability judgments remain required.

**The complete observation did not improve the historical baseline.**

| Observation | Complete elapsed (s) | Completed shell commands | Captured output bytes |
| --- | ---: | ---: | ---: |
| Original B-baseline-1 | 238.081 | 16 | 276609 |
| Original B-baseline-2 | 350.333 | 19 | 336270 |
| Original B-baseline-3 | 241.438 | 18 | 314693 |
| Original median | **241.438** | 18 | 314693 |
| Prior continuation-1, single | 272.097 | 16 | 340078 |
| New decisions-1, single | **311.545** | **21** | **333316** |

The new run is **70.107 s / 29.037% slower** than the unchanged historical
median. It is 39.448 s slower than continuation-1. Output is 1.988% below
continuation-1 but 5.918% above the original output median; shell round trips
increase from 16 to 21 versus continuation-1. This is a single observation, not
a new median or a causal speed estimate. Exactly one provider invocation ran;
there was no retry, extra probe, baseline replacement or favorable-run selection.

### Local proof required before the observation

Evidence directory:
`/tmp/issue573-kickoff-benchmark/iteration017/offline-verified/`.
The exact saved continuation-1 `manager-artifacts/request.json` (SHA
`6a80df7a00cfb86e78f5cf1cca82469618169d9fa3e42e3a747a6d4088b5feef`) and copied
original private stores were used for both public presentations. Original files
were not changed. Both calls use the same project identity, paths and source;
the observation clock is fixed to the recorded continuation-1 start solely for
this offline comparison. Evidence dates and validators are unchanged.

| Measurement | Legacy normal response | New decision view |
| --- | ---: | ---: |
| Canonical serialized JSON bytes | 56648 | 42663 |
| Actual CLI stdout bytes | 56649 | 60280 |
| Both rendered with identical indentation | 83675 | 60280 |

Canonical material decreases **24.688%** and equally indented material
**27.959%**. Actual stdout increases versus old one-line JSON; indentation is
reported separately, not counted as semantic improvement. The legacy response
here is the preserved old normal interface on current source, not a rewritten
historical item 7. Historical item 7's 84261-byte whole pretty print and item 8's
40311-byte partial reprint remain unchanged. Different path/clock/source-index
lengths prevent equating those historical bytes with the offline fixture.

All 13 candidate applicability/eligibility/diagnostic records remain visible.
Unselected role defaults/phase lists and selected artifact plumbing are deferred
to existing full discovery. Selected profiles, behavior, capability setup,
mandatory gates, human tasks and relevant routes remain visible. Discovery-only
manifest records/watched paths are deferred after full validation; source_index
shrinks from 45 to 14 records while preserving every source actually referenced
by displayed delivery/model conclusions. Missing evidence still has an explicit
inspection route. Optional unused schema/examples are deferred to existing
`schema`; required types, nested product constraints, current judgment fields and
checkout alternatives remain available. Owner section IDs/paths/hashes/ranges
remain, with duplicate reverse links removed and no whole-source-union command.

Public journeys cover locator forwarding, valid isolated stores, complete
payloads, custom graph/material changes and invalid diagnostics, model expiry,
source changes, missing workload coverage, same-draft raw report capture,
unresolved report decisions blocking render, and one unchanged-formatter render
with full semantic equivalence. They map to U05/U07-U09/U12/U14-U16 and
I01/I03/I05/I06. External report producer I/O is the fixture boundary; tests may
supply real decisions. No normal consumer Python output reconstruction is
necessary. This proves interface capability, **not autonomous model compliance**.

Red logs: `red.txt`, `red-current-types.txt`, `red-checkout.txt` in iteration017.
Related checks: 38 passed before final checkout refinement; 11 presentation/
invalidation cases passed; custom graph and checkout journeys each passed.
Existing guidance checks: 90 passed, then the owner-link assertion passed after
reconciliation. Final normal commit hooks: **353 passed** on final source.
The custom fixture initially used unsupported workload `analysis`; correcting it
to declared `planning` is recorded as a fixture correction, not a product fix.

### Actual caller and endpoint audit

Raw evidence:
`/tmp/issue573-kickoff-benchmark/codex-formal/B-original-conditions-decisions-1/`:
`prompt.txt`, `streaming.jsonl`, `event-times.jsonl`, `command-spans.json`,
`run-record.json`, `proposal.txt`, `endpoint-reading-audit.json` and
`manager-artifacts/` including original request/draft/reports, rendered proposal,
and the exact first decision-view stdout.

- **Early whole kickoff read eliminated:** item 4 only creates the output
  directory; item 5 uses `stores --summary decisions`; item 6 consumes the
  60684-byte decision view directly using the intended private stores.
- **Whole summary pretty reprint eliminated; partial repetition remains:** item
  12 executes assembly again and uses Python to print graph/questions (14700
  bytes). Item 14 filters the reading index. No claim of zero repeated assembly
  or no handwritten output filtering is warranted.
- **Policy repetition remains:** item 7 reads full kickoff/playbook/model owners;
  item 9 rereads kickoff 300–520 and playbook selection, then reads sync; item 11
  rereads sync 1–83 and kickoff 510–600. Model-selection's second read disappears,
  but kickoff/playbook/sync repeated reads do not.
- **Earlier engineering-document duplication disappears:** CONTRIBUTING,
  language-policy and engineering-guidelines are read once in item 10;
  positioning/roadmap are first read in item 21. Additional preference
  implementation/tests, store implementation and effort configuration research
  still occur. Item 11 also reads selected graph YAML already projected by the
  helper; item 12 repeats its projected data.
- **Interface exploration remains:** item 14 reads helper parser/store source;
  item 18 reads and pretty prints full schema. No failed preflight glob or
  formatter type repair occurs, but the desired no-source-lookup journey was
  not followed by this Manager.
- **Checks/capture/render remain complete:** items 15/16 execute update/catalog
  once each in parallel pipelines and capture full original bytes into the same
  draft. Item 17 reads their full reports; actual current/not_requested decisions
  are supplied in item 22, whose first render succeeds. Item 24 reads the
  complete 9903-byte formatter output. The final 10746-byte Chinese response
  retains product scope/invariants, locales/chains, exact local commit/no PR,
  empty cleanup, mandatory/user gates and complete confirmation boundary. It is
  a rewritten presentation, not byte-identical formatter stdout.

Actual first-view storage/repository identity and source-backed delivery plus
Astra/Luna payload hits were verified, including limitations and missing
workload coverage. No raw evidence inspection was needed to recover payloads.
No proposed case action, workflow activation, commit, PR, probe, preference
mutation or cleanup occurred; the measurement checkout remained clean. Complete
render success establishes endpoint validity, not acceptance success.

### Time accounting and remaining gaps

| Receipt-defined segment | Seconds |
| --- | ---: |
| Through final preparation read, including checks (item 21) | 130.107 |
| Drafting through successful render (item 22) | 73.112 |
| Post-render tool/readback (item 24) | 7.270 |
| After last tool through process exit | 101.056 |
| Complete total | **311.545** |

Tool receipt interval union: **5.018 s**. The other 306.527 s cannot be assigned
solely to reasoning: it includes generation, translation/rewording,
orchestration, buffering, transmission and service waiting. Individual helper
and check durations inside combined commands cannot be precisely isolated from
receipt timestamps. The final-response tail is included in total elapsed.
Usage: 818558 input tokens (729856 cached), 8727 output, 567 reported reasoning
output. Captured output bytes are a reading-volume proxy, not attention telemetry.

Setup is separate: clone 0.131 s; warm writes/checks 7.156 s; extra local view
validation 1.016 s. Phase investigation/testing/commit time is outside the
Manager observation and is not represented by those setup numbers.

Original prompt remains exactly 1757 bytes / SHA
`dc1516efca94b75887b2ffccf30c6c1bc3396585fcf908e7d6f91c4454f01e92`;
CLI 0.156.1, Astra, medium, disabled sandbox, fresh ephemeral session and original
local-commit override are retained. Source and warmed evidence are the treatment.
Historical XDG/Python/provider conditions cannot be fully reconstructed. Latest
source already implements the case feature; current issue naming, generated
scope/actions/strategy and presentation differ. Input equality does not imply
identical generated work. Diagnostic replay 293.777 s, including its 44.111 s
repair, remains diagnostic only. The invalid 239.514 s endpoint stays invalid.

The bounded correction has local evidence and partial caller adoption, but
repeated exploration and original performance/coverage acceptance remain unmet.
No extra observation or formatter scope change is inferred. PLAN-001
nonhistorical coverage, PLAN-014 and PLAN-015 stay open; original cold/changed
coverage and historical failures are preserved. The accepted narrow historical
red/order exception does not waive these remaining gates. Develop is not
complete and no successful Review handoff is authorized by this result.

## Direct prefill comparison — 2026-10-01

After the user requested direct code changes outside the workflow, one baseline
and one current warm preparation were measured sequentially. No workflow was
resumed. Both calls used the historical 1,757-byte prompt (SHA-256
`dc1516efca94b75887b2ffccf30c6c1bc3396585fcf908e7d6f91c4454f01e92`),
Codex CLI 0.156.1, `gpt-6-astra`, medium reasoning, ephemeral sessions, ignored
user config, and disabled sandbox. The prompt's explicit local-commit override
was retained, including its conflict with the original PR request. Each call
ended only after the complete Chinese proposal was output. Neither call
executed case delivery or activated a workflow.

Raw evidence and runner scripts are under
`/tmp/issue573-kickoff-benchmark/direct-prefill-20261001/`. Each condition has
`setup.json`, the exact prompt, `streaming.jsonl`, receipt timestamps,
`command-spans.json`, `run-record.json`, and `proposal.txt`. `comparison.json`
contains the totals. The current source is HEAD
`81fd05c5815fbba2bf40f2c1b8b468611e1c4ace` plus the recorded uncommitted overlay;
`warm/source.patch` and per-file hashes preserve that treatment. Its tracked
patch and overlay files were unchanged after the run. The baseline revision is
`6ce6bddade03e6ee31a60f437accd4d557467c50` and its checkout stayed clean.

The warm condition used isolated stores populated before timing. Existing
source-backed delivery conventions and the original dated Astra/Luna assessments
were retained; a generic local-commit argv template was added. No case product
scope, acceptance criteria or complete proposal was cached. Actual discovery
confirmed delivery/template and both model hits. Setup logs/time are separate
from Manager elapsed; this is a warm-path measurement, not cold-start cost.

| Measurement | Baseline | Current warm |
| --- | ---: | ---: |
| Complete elapsed | 240.226 s | 354.645 s |
| Through first successful contract render | 143.690 s | 229.683 s |
| From successful render through final output | 96.536 s | 124.962 s |
| Tool receipt interval union (included above) | 11.649 s | 6.239 s |
| Completed command spans | 16 | 19 |
| Captured command-output bytes | 296,372 | 546,779 |
| Input tokens, including cached tokens | 525,589 | 1,041,672 |
| Output tokens | 6,524 | 10,391 |
| Final proposal bytes | 9,957 | 12,023 |

The current observation is **114.419 seconds (47.63%) slower**. Both formatter
invocations succeeded. The first successful render is baseline item 18 and warm
item 20; source reads mentioning the formatter are not renders. Warm item 21
adds an AGENTS.md lookup after rendering. Receipt intervals do not isolate
provider reasoning, generation, buffering or network wait.

The normal consumer did not successfully adopt the prefill path:

- Warm item 8 wrote the nonexistent `phase_models` field instead of
  `phase_chain`. Item 14 assembly reported
  `unknown_formatter_field:phase_models`; its formatter draft was null. It also
  reported missing configured phase chains because the explicit mapping used
  the wrong field. The `prefilled` report records that delivery, cleanup,
  worktree, mode and other defaults were computed, but normalization discarded
  the usable draft after that input error.
- The generated `draft.json` therefore contained placeholder product fields and
  null action slots. Item 19 fixed the field name, then manually supplied
  worktree, mode/event manager, gates, review choices and actions instead of
  recovering the populated draft. The final request retained only two newly
  prefilled parser defaults.
- The Manager reread owner documents and implementation, reprinted discovery,
  and generated a larger proposal. It replaced deliver/cleanup with explicit
  empty arrays and put suggested local-commit commands in product constraints,
  citing commit ownership in develop and the local-only endpoint. Thus this
  observation does not demonstrate an effective cached-delivery-template path.

`warm/manager-artifacts/` retains the actual initial request, discovery,
assembly diagnostics, placeholder draft, complete request, checks and rendered
proposal. This is one actual end-to-end observation per condition, not a
statistical performance claim. Current source already contains the case's
preference implementation, and generated scope/action placement differs from
baseline. The same prompt therefore does not establish identical generated
work or isolate prefill alone as the cause. It does establish that this normal
caller run failed to realize the intended speedup. No follow-up provider run,
code correction, workflow acceptance or performance acceptance is inferred.

## Draft-first consumer measurement — 2026-10-01

The user requested one measurement after the new `draft` entry point and
error-preserving draft behavior were implemented. One fresh warm Manager call
used the same exact historical prompt, CLI 0.156.1, Astra, medium reasoning,
ephemeral/ignored-user-config settings, disabled sandbox, and private warmed
stores as the direct-prefill comparison. The baseline was **reused**, not rerun:
240.226 s from the immediately preceding pair. Preparation remained proposal-only.

Raw evidence is under
`/tmp/issue573-kickoff-benchmark/draft-first-20261001/`; `warm/` contains the
runner, source revision plus overlay hashes and patch, prompt, raw/timed events,
command spans, final proposal, initial draft and actual Manager artifacts.
`comparison.json` records the comparison and field-by-field draft audit.
Source patch and overlay hashes were unchanged after the call. Setup and cache
warming are recorded separately and excluded from Manager elapsed.

| Measurement | Reused baseline | Previous warm | Draft-first warm |
| --- | ---: | ---: | ---: |
| Complete elapsed | 240.226 s | 354.645 s | 257.255 s |
| Through successful formatter render | 143.690 s | 229.683 s | 158.712 s |
| After render through process exit | 96.536 s | 124.962 s | 98.543 s |
| Tool receipt interval union | 11.649 s | 6.239 s | 6.746 s |
| Completed command spans | 16 | 19 | 18 |
| Captured command-output bytes | 296,372 | 546,779 | 350,476 |
| Input tokens, including cached tokens | 525,589 | 1,041,672 | 573,698 |
| Output tokens | 6,524 | 10,391 | 7,061 |
| Final proposal bytes | 9,957 | 12,023 | 9,839 |

This observation is **27.46% faster than the previous warm run**, but still
**7.09% slower than baseline**. It does not establish a stable speedup or meet
performance acceptance. The same source/feature-state and generated-scope
limitations recorded above still apply; only one new observation was taken.

The consumer now followed the intended entry path. Item 9 invoked `draft` with
issue name, selected playbook, current CLI and the six explicit model chains.
It completed at 43.291 s with exit 3, the expected incomplete-product status,
and wrote a populated draft. `initial-draft.json` preserves that draft before
editing. The actual delivery template and model evidence were valid hits.

Item 19 loaded and edited the draft in place, then rendered successfully at
158.712 s. It retained the exact delivery argv, phase chain, worktree, Manager
mode/CLI, and permission/clarification/alignment defaults. It filled the product
contract and PR capability choice, chose the explicit user confirmation gates,
translated action descriptions, and excluded cleanup for the fixed local-only
endpoint. Language values were moved to explicit inputs without changing their
values. No unknown-field repair or loss of prefilled values occurred.

Remaining work included repeated owner/playbook reads, implementation inspection,
an additional assembly, and printing up to 18,000 characters of schema. The
last preparation/schema read ended at 90.574 s; drafting and successful render
then took 68.138 s. A complete formatter readback ended at 162.846 s, followed
by 94.409 s through final response completion. These are receipt-based elapsed
segments, not isolated reasoning or generation measurements. No source change,
workflow activation, case commit, PR, cleanup or additional provider retry was
performed. This run demonstrates adoption of prefilled drafts, while total
performance remains slightly behind the measured baseline.

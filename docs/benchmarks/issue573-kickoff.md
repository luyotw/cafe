# Issue 573 Kickoff Preparation Measurement

## Status

The confirmed formal comparison uses Codex CLI `0.156.1`, `gpt-6-astra`, medium reasoning, and sandbox disabled for every condition. Six valid pre-change baseline preparations completed from `2026-09-29T12:02:04Z` through `12:30:32Z`. The two failed Codex attempts and one Claude HTTP 429 remain historical invalid attempts, not observations. Two separately authorized Codex diagnostics are excluded from the benchmark attempt count. The complete target remains 21 valid preparations; six are complete and 15 remain.

The required pre-change revision is `6ce6bddade03e6ee31a60f437accd4d557467c50`. The current development worktree has not changed normal Manager preparation guidance, so that baseline remains reproducible.

## Fixed cases

The accepted plan defines two representative cases. Their exact request text, accepted answers/preferences, repository snapshot, effective catalog state, delivery endpoint, and Manager invocation settings are fixed below and reused unchanged across conditions.

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
| A | Cold | 1–3 | Pending | — | — | — | — | — | Not run |
| A | Warm | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Cold | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Warm | 1–3 | Pending | — | — | — | — | — | Not run |
| Changed input | Improved warm with one material input changed | 1–3 | Pending | — | — | — | — | — | Not run |

For these baseline runs Codex JSONL records command start/completion ordering but does not include per-item timestamps; therefore the wall-time union of tool spans and separate Manager reasoning/orchestration residual are unavailable. The recorded elapsed duration is end-to-end Manager preparation time with fixed answers and no human waiting. Captured command-output bytes are measured from completed JSONL tool results. Codex exposes token totals, but reasoning duration is not separately reported. No external research tool spans occurred.

## Current measurement limitations

The six baseline proposals are valid and complete. Their preparation times are A: 243.184, 315.179 and 319.010 seconds (median 315.179 s; range 75.826 s); B: 238.081, 350.333 and 241.438 seconds (median 241.438 s; range 112.252 s). Baseline median across all six is 278.309 s. Per-tool timestamps and model reasoning duration are unavailable in Codex JSONL, so tool wall-time union and the separate orchestration residual cannot be computed. No unresolved-choice clarifications or human wait occurred. Proposal SHA-256 values and token totals are preserved with each raw result.

The historical Claude reset, Claude CLI validation, and old 23-attempt cap below describe the superseded Claude comparison only. The current Codex comparison has 15 of its 21 valid preparations remaining. Do not mix historical invalid calls, separate diagnostics, or Claude records into current condition statistics.

## Historical Claude pre-reset verification

Before the provider reset, the baseline archive was compared with Git revision `6ce6bddade03e6ee31a60f437accd4d557467c50`: all 684 tracked paths, blob contents, and file modes match exactly, and no path is writable. The archive's nineteen lost executable bits were restored without adding write permission. The helper runner remains `/tmp/cafe-573-tool-surface-xtoekfnu/manager_helper.py`, SHA-256 `d5c957e948891cb6ea43c84ea4798db269f73199e6b39726007d6c48625d068b`.

The fixed A and B request hashes remain `19fb10197b5d26f654d99b4b0cd563d14a1be142c9ccfb6ed48a11afda78b280` and `960e3b8ed55a5b7de5c8455f836164b946c4c2285a994d126b56dacd246704be`. Their prompt hashes are respectively `086dbc4cc1d3f258b2bd79af259339ae10eeaca3305604e8bf72870880a7ce46` and `dc1516efca94b75887b2ffccf30c6c1bc3396585fcf908e7d6f91c4454f01e92`. These statements describe the superseded Claude run preparation, not the current Codex baseline. Current Codex baseline outputs are recorded above.

The PLAN-013 targeted regressions were rerun: the seven kickoff modules passed 30 tests; the Manager skill selection passed 91 (72 deselected; one existing Pydantic serializer warning); locale passed 10, catalog resolution 20, and event-driver boundary 4. These local checks used no provider calls. That earlier local regression evidence is retained as history. The current Codex target is 21 valid preparations; six baselines are complete and 15 preparations remain across cold, warm and changed-input conditions.

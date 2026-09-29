# Issue 573 Kickoff Preparation Measurement

## Status

The local Claude command/helper boundary check passed. Two Codex baseline A attempts remain invalid observations because their read-only command sandbox could not start in this container. The first authorized Claude baseline A invocation was also rejected before prompt processing by the account five-hour session limit (HTTP 429, zero input/output tokens, zero tools); it is an invalid attempt and counts toward the 23-call cap. The user authorized 21 valid Claude preparations within that total cap, so the remaining call budget cannot now cover all 21 valid runs. A separate Claude native `Read` diagnostic succeeded, but it did not verify the helper command path.

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
- Manager CLI: Claude Code `2.1.284`, model `claude-opus-5-5`, effort `medium`. Formal sessions use `--safe-mode --restricted --permission-mode dontAsk --permission-prompts none --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence --disable-slash-commands --tools Read,Bash --output-format stream-json`; allow rules contain `Read` plus exactly `Bash(python /tmp/cafe-573-tool-surface-xtoekfnu/manager_helper.py discover)`, `Bash(python /tmp/cafe-573-tool-surface-xtoekfnu/manager_helper.py assemble)`, and `Bash(python /tmp/cafe-573-tool-surface-xtoekfnu/manager_helper.py render)`. No `bypassPermissions`, unrestricted Bash, MCP server, or action/delivery command is enabled. The prior one-call diagnostic used `--permission-mode plan` and `Read` only; it is not a formal observation.
- Local tool-path check (no provider call): Claude Code help confirmed all listed flags; the fixed helper runner (`/tmp/cafe-573-tool-surface-xtoekfnu/manager_helper.py`, SHA-256 `d5c957e948891cb6ea43c84ea4798db269f73199e6b39726007d6c48625d068b`) accepted only `discover|assemble|render`, used `shell=False`, and successfully ran `discover` from this checkout. The helper wrote its catalog cache and locks only beneath isolated `/tmp/cafe-573-tool-surface-xtoekfnu/{config,cache}`; provider calls: 0. The actual Claude Bash allowlist is first exercised in the formal run that needs the helper.
- Manager reasoning settings: `medium`; every baseline/cold/warm/changed-input session uses this same CLI, exact model, effort, tool list, permission mode, allowlist and a fresh CLI session.
- Run authorization: the user authorized 21 valid Claude preparations and a total benchmark-call cap of 23, counting the two failed Codex A attempts as invalid observations. The prior Claude native-Read diagnostic is outside the formal benchmark. No additional provider calls are authorized by that cap.
- Fixed Manager instruction template SHA-256: `086dbc4cc1d3f258b2bd79af259339ae10eeaca3305604e8bf72870880a7ce46` (1,751 UTF-8 bytes). Attempt 1 used the detached baseline worktree; attempt 2 used a read-only `git archive` of the same revision with no Git metadata, `--ignore-user-config`, `--skip-git-repo-check`, and `sandbox_network_access=true`.
- No case action is authorized. The sessions were instructed to remain proposal-only; the second used a read-only source archive with no Git metadata and an empty `HOME`. No case action was run.

## Run records

No run records are available yet. For every run, record the exact request and accepted inputs, source revision, repository/catalog/evidence state, CLI/model/reasoning settings, start and end timestamps, complete rendered proposal reference, tool/request-result boundaries, evidence bytes delivered, external research spans, Manager reasoning/orchestration residual, and human waiting separately.

Report end-to-end elapsed time and preparation excluding human waiting. Use the wall-time union for concurrent tool spans. Count only unresolved-choice questions as clarifications; report mandatory final confirmation separately. Report each observation and median/range, cold overhead, changed-input cost, and regressions.

| Case | Condition | Run | Proposal evidence | Elapsed | Tool spans | Evidence bytes | Clarifications | Human wait | State |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| A | Invalid baseline attempt (not an observation) | Codex-1 | `/tmp/issue573-kickoff-benchmark/A-baseline-1/proposal.txt` (SHA-256 `9810ae3aab6f1fc171c9fc9fae56d83339bf5d084948c8ede79694eda56de368`; incomplete) | 81.870 s | 0 shell; 2 MCP request/result round trips | 0 source bytes (1,751 prompt bytes) | 0 | 0 s | Invalid call: `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; 123,459 input / 2,151 output tokens |
| A | Invalid baseline attempt (not an observation) | Codex-2 | `/tmp/issue573-kickoff-benchmark/A-baseline-2/proposal.txt` (SHA-256 `6b0bded128bc9b64324e923d9531eed0234ff84edf4ceb0563c5e886a2483ca7`; incomplete) | 68.199 s | 0 shell; 2 MCP request/result round trips | 0 source bytes (1,751 prompt bytes) | 0 | 0 s | Invalid call: same sandbox startup error; 93,970 input / 1,855 output tokens |
| A | Invalid baseline attempt (not an observation) | Claude-1 | `/tmp/issue573-kickoff-benchmark/A-baseline-claude-1/streaming.jsonl` (rate limit response; no proposal) | 0.925 s | 0 tools; 1 rejected provider request | 0 source bytes (1,751 prompt bytes supplied; 0 tokens processed) | 0 | 0 s | Invalid attempt: account five-hour session limit, HTTP 429; 0 input / 0 output tokens; CLI 2.1.284, `claude-opus-5-5`, medium |
| A | Baseline | 1–3 | Pending | — | — | — | — | — | Not run |
| A | Cold | 1–3 | Pending | — | — | — | — | — | Not run |
| A | Warm | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Baseline | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Cold | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Warm | 1–3 | Pending | — | — | — | — | — | Not run |
| Changed input | Improved warm with one material input changed | 1–3 | Pending | — | — | — | — | — | Not run |

## Run limitations

Codex reports session events and wall-clock bounds; it did not expose a separate model-reasoning duration in its attempts. Human wait is zero because the accepted answers are fixed inputs. The two Codex partial proposals are not semantically complete and cannot support a timing comparison. Retrying with `sandbox_network_access=true` still failed during read-only sandbox startup with the same `RTM_NEWADDR: Operation not permitted` boundary. The first formal Claude attempt was rejected before prompt processing due to the account five-hour limit, so it produced no proposal and exercised no tools. The Claude `Read` diagnostic did not verify helper execution. Local validation confirmed the Claude CLI flags, restricted exact-command configuration, and helper discovery with isolated cache/config paths without a provider call. All subsequent attempts, if the provider limit clears, must remain within the original 23-call total; the 3 failed attempts leave at most 20 calls for valid preparations, fewer than the planned 21.

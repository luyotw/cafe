# Issue 598 kickoff preparation observations

Compact preparation required fewer operations and less elapsed time in all four matched pairs observed on 2026-10-06 Asia/Taipei. Each observation ended with the first complete, validated, confirmable proposal. This is preparation evidence; it is not a measurement of implementation, native review execution or publication.

## Results

| Delivery | Evidence condition | Full seconds | Compact seconds | Full operations | Compact operations |
| --- | --- | ---: | ---: | ---: | ---: |
| PR, `feature` → `main` | Fresh | 5.633 | 1.280 | 146 | 53 |
| PR, `feature` → `main` | Valid reused | 2.795 | 1.100 | 139 | 50 |
| Direct, exact `feature` branch | Fresh | 4.104 | 1.296 | 146 | 53 |
| Direct, exact `feature` branch | Valid reused | 2.701 | 1.144 | 139 | 50 |

Times are rounded only in this table. The raw records retain monotonic elapsed measurements. No numerical speedup or operation-count threshold was used as a gate. These observed reductions support the preparation-cost acceptance criterion for the measured conditions.

## Evidence and reproduction

[Raw observation records](issue598-kickoff-records.json) retain complete proposals and rendered text, artifact SHA-256 values, source retrieval dates/fingerprints, complete preflight reports, every top-level command and exit status, reference/repository reads, and nested subprocess start/exit evidence. [Durable stdout](issue598-kickoff-observation.log) records observable progress and each separate `rendered` terminal result. The outer recorder exited 0 after all eight observations and paired-identity validation; that evidence is recorded independently in `run_evidence`. All observed top-level and nested children exited 0.

The measured source revision is `f2d70cd7bdcdfb75bb0a69378242a8486ca6e8d8`. The [recorder](measure_issue598.py) SHA-256 is `327fd7a6f82a6c99b78a9ea8d7683df3d4ba72fa6707cf3a844f98f4ddd82f84`. The implementation's final selected-chain preparation check is included in ancestor `cb28f10b78983b36db8beb9be6e5a401acccffc5`.

Run from an environment with the repository interpreter, installed `claude`, `curl` and `rg`:

```bash
.venv/bin/python docs/benchmarks/measure_issue598.py --output /tmp/issue598-observations.json
```

The recorder creates isolated temporary Git repositories, supplies a fixed already-clear request, and invokes the actual public `prepare_kickoff.py discover`, `assemble` and `render` commands. It retrieves actual primary-source pages and executes actual full-route update/catalog checks. It uses no mocked timing, fabricated preflight result or simulated rendering. Unit tests validate incomplete/failed observations and unmatched identities; their timing values are not acceptance evidence.

## Matched conditions

Both forms use a local test-only declaration copied from `streamlined`, with the same `develop`/`deliver` graph, native reviewer policy, attempt limits and mandatory gates. Its ID is `benchmark`; only `contract.mode` changes between `full` and `compact`. This counterfactual full declaration compares contract preparation without changing the execution topology. Neither form is activated or used to deliver a benchmark task.

Both inspect `src/app.py` and `tests/test_app.py`, propose those exact paths, use the same task and delivery facts, and start from Git commit `c4b2f1ae0ab167c4baac1df22f29941bb4bfac1d`. The common graph SHA-256, excluding only the contract-form declaration, is `e5482349d5906dc343a4fae0c601db2f224acff2ec3f78ad1714ae5ca25977f4`. The comparison validator requires matching baseline, graph, provider version, model, ordered phase chains, review configuration and effective native projection for every route/cache pair.

Every phase uses the single-entry chain `claude:claude-sonnet-5-5`. The actual installed version was `2.1.289 (Claude Code)`. Both forms project the same `cafe_reviewer` with `Read`, `Glob`, `Grep`, `model: inherit` and the `parent_command` checkpoint interface. The provider's [model configuration](https://code.claude.com/docs/en/model-config) and [subagent documentation](https://code.claude.com/docs/en/sub-agents) were retrieved and fingerprinted during fresh preparation or before a reused observation. Effective command projection is checked for both forms. No live model request is made; account availability and the results of a real native review are not established by this benchmark.

PR facts specify remote `origin`, source `feature`, target `main` and PR creation only. Direct facts specify the same remote, exact `feature` branch, and commit/push only. Each remote is a local bare repository. Preparation does not push, publish, merge, close an issue, save unrelated preferences or modify benchmark task contents/history. Post-observation Git checks confirm the baseline and clean status, and the recorder rejects workflow activation.

## Measurement window and operation accounting

The monotonic clock starts before reading the Manager skill entry and selected reference route, before repository inspection. Both read the entry, playbook-selection and progress references. Full reads the full kickoff/input/model/strategy references; compact reads its selected compact reference. Both perform actual Git status/baseline inspection, `rg` file discovery and task-file reads. The clock ends when the actual render command returns the complete proposal and its artifact has been retained. Human waiting and model deliberation are excluded.

An operation is one top-level subprocess, one explicit reference/repository read, or one nested subprocess. Ordinary Python function calls and every individual filesystem read inside a helper are not counted as separate tools. Network retrieval uses `curl` and counts once as a top-level subprocess; its source record does not add another operation. Nested subprocesses are recorded at the real `Popen` boundary, with their real wait/poll exit status. Each proposal is validated independently from child completion.

| Form / condition | Top-level subprocesses | Explicit reads | Nested subprocesses | Total |
| --- | ---: | ---: | ---: | ---: |
| Full / fresh | 11 | 9 | 126 | 146 |
| Compact / fresh | 9 | 6 | 38 | 53 |
| Full / reused | 6 | 9 | 124 | 139 |
| Compact / reused | 6 | 6 | 38 | 50 |

Fresh means empty case-specific preference/evidence stores and no prepared model/preflight records. Actual model-document retrieval and CLI version observation occur inside both windows. Full additionally executes its required actual update/catalog checks. Compact performs its relevant selected graph, scope, model/native configuration and endpoint checks without full-only preparation.

Reused means current dated model/provider evidence and actual unchanged update/catalog reports are available equally to both forms before entry. Their original observation timestamps and tokens are retained; no warmup cost is included in the reused window. Compact does not consume irrelevant full-route reports. Preferences remain empty in both forms, so this condition demonstrates evidence reuse rather than every preference-reuse scenario. Selected native configuration remains checked during compact discovery.

## Limits

There is one completed observation per condition, in full-then-compact order. OS caches, network response caches, filesystem load and scheduling were not reset or randomized. The installed shared global catalog remained the same; “fresh” does not mean a fresh CAFE installation or cold OS cache. The local stores and checkout fixtures are isolated, but these results are not a statistical latency guarantee.

The task and model selection are explicit and already clear. The procedure exercises the real Manager entry/reference route and public preparation commands; it does not time a conversational Manager's reasoning, an ambiguous task's research, user approval, live native review, workflow execution or external delivery. Full's extra required checks and broader graph/profile discovery contribute to its observed cost. Earlier recorder development trials are excluded from this dataset. Historical #573 results are not a baseline.

Provider support is an execution prerequisite, not a substitute for review evidence. Unsupported selected native projections, including incompatible explicit backups, produce focused gaps before confirmation/resume; no chain is silently substituted. Functional journey tests separately cover user authority, cumulative Git scope, fresh review rounds, current-content invalidation, bounded correction, exact delivery and failure/unknown non-replay behavior.

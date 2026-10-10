# Cost accounting and official rate updates

CAFE distinguishes provider-reported USD cost, token-based API-equivalent
estimates, and unavailable cost. An estimate is a rate-card calculation, not a
subscription invoice. Included credits, subscription charges, negotiated prices,
tools, taxes, regional processing and discounts are not allocated to workflows.

## Official pricing sources

Codex invocations use the official machine-readable Markdown representation of
the [OpenAI pricing page](https://developers.openai.com/api/docs/pricing.md).
Other adapters use these official sources:

| Adapter | Rate source | Refresh validation |
| --- | --- | --- |
| Copilot | [GitHub documentation YAML](https://raw.githubusercontent.com/github/docs/main/data/tables/copilot/models-and-pricing.yml) | ETag when available; semantic snapshot hash |
| Cursor | [Models & Pricing Markdown](https://cursor.com/docs/models-and-pricing.md) | Semantic snapshot hash; the page currently has no ETag or Last-Modified |
| Gemini | [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing) | Last-Modified when available plus semantic snapshot hash |

CAFE pins cached or bundled rates before launching its subprocess. When the
last check is at least 24 hours old, it schedules a background refresh without
waiting for HTTP. One daemon worker per provider/cache runs in each process;
agent startup and shutdown never wait for it. An update completed after the
card was pinned applies to later invocations. Short-lived processes may exit
before their background refresh finishes; use `pricing refresh` to explicitly
wait for an update. Individual socket operations time out after three seconds;
failed checks back off for 15 minutes. A missing network connection or changed page format retains the
last valid card, falling back to the packaged card on first offline use.
Refresh, parsing and cache errors are contained within pricing; they never
change the agent's exit status or stop a workflow. If no valid card can be read,
token usage is still retained and its estimated cost remains unavailable.

Conditional GET uses `ETag` / `If-None-Match`, or `Last-Modified` /
`If-Modified-Since` when no ETag is available. A `304` advances the check time
without changing the rate version. A successfully parsed change creates a
content-addressed snapshot; formatting whitespace and HTTP timestamps alone do
not create a new version. Numeric rates, model identities, context thresholds
and pricing-term changes participate in the version hash. An unrecognized
text-model table or overlapping effective-date schedule is rejected before replacing the current card.

The default caches are `~/.cafe/pricing/{openai,copilot,cursor,gemini}/`. `current.json` holds the latest
check and card; files named `<version>.json` retain older cards. Publication uses
atomic replacement and concurrent POSIX refreshes do not wait for one another.
Both successful cards and failure metadata are published under the same lock,
so a failed writer cannot replace another writer's newer card.
Each invocation pins its card before execution, so a refresh during that call
does not change its estimate.

```sh
cafe pricing status --json   # inspect only; no network request
cafe pricing refresh --json  # check immediately; does not rewrite history
cafe pricing status --provider all --json  # one JSON object per provider
cafe pricing refresh --provider cursor --json
cafe pricing refresh --provider all --json
```

Set `CAFE_PRICING_AUTO_UPDATE=0` to disable automatic network checks. Explicit
`pricing refresh` still works. `CAFE_PRICING_CACHE_DIR` selects an alternate
OpenAI cache directory; the other providers use `copilot/`, `cursor/` and
`gemini/` beneath it. The status command reports whether the rates are stale and
whether the last refresh failed. A refresh failure exits with code 1 while
retaining the valid fallback.

`Last-Modified` describes the web resource. It is not the date a model price
became effective, and HTML and Markdown representations can have different
timestamps. CAFE records it separately from the fetch/check times; it does not
invent an effective date.

## Invocation records and calculations

New subprocess accounting attaches `cost_records` to `TokenUsage`. These records
travel with workflow iterations and one-shot chat usage, including Manager
executions that use the shared executor. They contain an invocation ID, verified
session ID where available, CLI, model identity and its evidence source,
provenance, USD amount, completeness and raw known counters. An estimate also
stores its rate-card version/source/fetch metadata, applied decimal rates,
billable token categories, assumptions and calculation version.

An explicitly reported `total_cost_usd`, including zero, takes precedence. CAFE
does not add a token estimate on top of it. Without reported cost, Codex can
produce an estimate when the exact model and necessary counters are known.
The following rules describe the Codex calculation:

* Codex input includes cached input. Cache reads and writes are subtracted from
  total input before the ordinary input rate is applied.
* Cache creation/write aliases are charged once. Conflicting values make the
  estimate unavailable.
* Reasoning tokens are already part of output and are not charged again.
* Missing cache-write usage makes the estimate unavailable when that model has
  a separate cache-write rate. Missing optional reasoning detail alone does not
  prevent estimating a known total output count.
* Standard global API rates are the explicit comparison basis. A total input
  count below a published context boundary establishes that every constituent
  request is short. An aggregate above the boundary does not establish each
  request's context band; CAFE leaves that estimate unavailable instead of
  applying a guessed long-context rate.
* Model IDs are matched exactly. CAFE does not resolve rolling aliases or use
  requested configuration as proof of which model ran. If stdout omits the
  model, the bounded reader can use this invocation's exact Codex native
  journal `turn_context` evidence. Missing, truncated, ambiguous or mixed-model
  evidence stays unavailable.

Session continuation is priced after the existing verified baseline has been
subtracted. Retry and fallback attempts retain separate records and their known
usage; partial failures retain known subtotals and incomplete coverage. Repeated
records with the same invocation ID are not added twice. A provider's repeated
final telemetry does not create another cost invocation.

Amounts use decimal arithmetic. Status, timeline, step and workflow summaries
read persisted records and never fetch prices or recalculate historical costs.
They show reported/estimated/legacy subtotals and mark partial coverage and stale
rates. Chat records are removed from the phase aggregate before being displayed
separately, preventing the same call from being billed under both views.

Existing records remain readable. A nonzero legacy cost is shown with unknown
provenance (`legacy`); old zero values are shown as unknown because older
serializers wrote zero for absent telemetry. New explicit reported and estimated
zero values are distinguishable. Historical recomputation is not automatic;
preserve the original record if performing a separately requested recalculation.
When new invocations are added to an older iteration or chat group, its prior
money and token subtotals remain visible alongside the new records. Missing
historical chat-cost coverage stays partial in step and workflow summaries.

## Copilot, Cursor and Gemini accounting

The adapters were checked with Copilot 1.0.83, Cursor Agent
2026.10.01-e373342 and Gemini CLI 0.58.0. Tests replay the actual counter
shapes without issuing paid model requests.

**Copilot** gets a unique temporary `--usage-output-file` for each subprocess.
CAFE sums `modelMetrics`, never both `modelMetrics` and the overlapping
`agentMetrics`. Per-model native `totalNanoAiu` is preferred over estimates;
1,000,000,000 nano-AIU is one AI credit, and the official credit denomination
is USD 0.01. The record retains the native amount, denomination, source and
conversion version. This is the dollar value of reported usage, before allocating
included credits; a premium-request multiplier is never interpreted as USD.
When native billing is absent, exact token metrics can use the pinned Copilot
model rate. Promotional footnotes with unverified applicability remain unknown.

Copilot metrics are session-cumulative. Before a resume, CAFE verifies the exact
session's final native `session.shutdown` and captures its model totals, money
and API duration. After execution, it subtracts that baseline. An active,
missing, malformed, ambiguous or oversized baseline leaves the invocation
unavailable, rather than charging historical usage again. Temporary telemetry
files are removed on success and failure. Older text summaries remain readable.

**Cursor** reads `result.usage.inputTokens`, `outputTokens`, `cacheReadTokens`
and `cacheWriteTokens`. In the checked native CLI, `inputTokens` has already had
cache reads/writes subtracted; CAFE charges it directly, then charges the two
cache categories once. Context checks include all three input categories.
The exact reported model uses the Cursor rate card. `Auto` with no resolved
model stays unknown. Only published spelling is normalized; rolling aliases,
reasoning variants and undocumented model identities are not guessed.

Cursor estimates use the base token rates. Account-specific processing fees,
regional uplifts and legacy plan adjustments are excluded. A long-context
surcharge with no verified base threshold makes the estimate unavailable.
A dated promotional rate cannot be used after its published expiration.

Official rate updates do not require a Cursor API key. This implementation
uses the CLI's existing authentication and does not query SDK or Admin billing
APIs. It cannot certify an account's actual charge or resolve Auto from billing
records; missing model or rate evidence remains unknown, including when the
account has included usage. Unknown cost is never presented as a zero charge.

**Gemini** preserves per-model stream statistics, ignoring explicitly unused
zero router models. CLI 0.58.0 reports candidate output in `output_tokens` and
omits thinking tokens. A bounded reader verifies this invocation's exact native
JSON/JSONL journal and reconciles model/input/output/cache counters against
stdout before supplementing thinking. For a resumed session, only messages
outside the captured pre-launch baseline contribute. Output is normalized to
candidates plus thinking; `reasoning_output_tokens` is its subset and is not
charged again. Missing or conflicting journal evidence preserves known stream
usage but makes the cost unavailable. Separate tool-token dimensions are not
silently assigned a text-token rate.

Each actual Gemini model gets its own calculation record, even when the
requested model is `auto`. Models absent from the card remain unknown and
mark the combined amount partial. The comparison basis is Gemini Developer API
Standard text pricing, including when a CLI login uses a free/subscription quota
or a Vertex endpoint; this is not an estimate of that account's invoice.
Audio, video-specific multipliers, grounding charges and cache storage per hour
are not assigned text-token prices. Dated prices are selected using the pinned
invocation UTC date, so future scheduled rates do not apply early.

Claude continues to retain an explicitly reported `total_cost_usd`, including
zero. Interactive terminals continue to use their existing native accounting
readers. They do not use the subprocess estimator and remain incomplete when
they cannot certify invocation/model/cost telemetry. This includes native
session switches, unsupported sub-agent accounting, and paths with no usage
reader. Merely configuring a model does not establish complete cost coverage.

## Manager progress and retained closeout accounting

Every established Manager progress diagram ends with a worker-only USD summary
(`已使用成本（不含 Manager）` in zh-TW). Rendering rereads persisted calculations;
it does not fetch prices, reprice historical usage, write accounting, or calculate
Manager-inclusive totals. An unestablished workflow retains its short response.
Initial workflows without execution evidence show unknown coverage. Compact
contracts have no separate progress diagram; their subsequent shared progress
renderer uses the same footer.

The neutral `services/cost_summary.py` view reads all custom-named phases,
iterations and issue/phase chats, prefers `iteration.json` over `context.json`,
removes chat overlap, and deduplicates invocation IDs across the workflow.
Source-relative identities preserve each legacy residual once across storage
copies. Reported zero remains valid. Missing/default-zero, corrupt, unsupported,
conflicting and partial evidence remains unknown/incomplete; a known subtotal
is never a claim of complete coverage. Provenance and stale-rate indicators
remain attached to the recorded amounts.

Manager callback bootstrap, delivery/fallback attempts and explicit Manager
chat turns use the Manager-owned sink, independently of phase/model names.
They no longer merge into worker iteration aggregates. Failure costs are retained;
a pre-attempt gap survives interruption and unavailable telemetry. Native/host
Manager turns without supported usage telemetry and unattested historical Manager
coverage remain unknown. No arbitrary session scraping or subscription allocation
is attempted. Exact tagged/retained invocation identities are excluded from the
worker view. Historical callback blends without invocation attribution are
withheld as uncertain, including ambiguous legacy residuals; original evidence
is preserved rather than rewritten as an attribution migration.

The accounting envelope lives at
`<git-common-dir>/cafe/costs/<issue>/<workflow-id>.json`, with its own lock.
It binds the exact Git project, issue and workflow, independently of closeout
receipts. It stores original persisted invocation calculations, per-source legacy
aggregates/gaps, Manager sources and a worker snapshot. It never copies session
transcripts or credentials. Reads reject symlinks/special files and invalid
identities; atomic publications retain all admitted evidence. The 16 MiB source
and envelope limits fail visibly, without truncation. Exceeding the bound or
failing to retain an available source leaves destructive cleanup unstarted;
missing provider telemetry itself does not block cleanup when its gap is retained.

Immediately before each confirmed cleanup command, the Manager helper holds
the existing worker advancement lock, captures a fresh identity-validated worker
snapshot, publishes and verifies it, then dispatches the original argv. Separate
archive-only closeout uses the preservation helper before the unchanged lifecycle
command. Accounting cannot authorize actions, change command receipts, stop a
worker, or replay cleanup. A matching live source or exact lifecycle archive is
preferred over the equivalent snapshot, never added as extra spend. Retained
Manager sinks already bound before worktree deletion keep writing; later available
accounting can be persisted from the retained checkout with the same identity.

After verifying cleanup outcomes and lifecycle effects (or the exact completed
archive), Manager offers the human yes/no preference. No answer, a timeout,
leave, failed/pending/unknown cleanup, or an earlier phase confirmation does not
mean yes. No ends the conversation without reading/aggregating inclusive costs.
Yes captures one stable source set under the retained accounting lock, with source
version checks and bounded read-only retries, and displays the worker subtotal,
Manager amount and combined total/known subtotal in USD. `captured_at` is a real
UTC read boundary, including for untimestamped legacy records; it is not an
invented historical invocation completion time. Active/unpersisted usage,
including the response presenting the report, is outside this as-of cutoff.
Unknown Manager coverage makes the combined result an incomplete known subtotal.

## Codex native descendants

An admitted caller can pass an `AccountingScope` (workflow ID, caller correlation,
existing durable usage sink) through AgentManager, executor and conversation
transport. Codex records an open checkpoint before process submission, observes
bounded native progress when stdout delivers activity, and freezes a real cutoff
on success or recoverable failure. Accounting failures do not change exit status,
explicit terminal requirements, authorization or the configured retry/fallback
chain. Abrupt process death can leave only the open checkpoint; it is incomplete.

Local discovery supports the inspected `codex-cli 0.159.3` journal format.
SQLite is an optional read-only index, selected by `thread_spawn_edges` and
`threads` schema capabilities, including `rollout_path` or `session_path`.
Its mutable model metadata is never execution-model proof. Candidates are
validated against native `session_meta` identities/ancestry. Missing or
incompatible indexes use bounded header discovery in `sessions` and
`archived_sessions`. Symlinks, special files, conflicting identities and
read/depth/node/time bounds leave coverage gaps. Limits are 256 tree nodes,
16 levels, 8,192 directory entries, 256 KiB per line, a 4 MiB tail per journal,
16 MiB per collection and three seconds for native observation; skipped owned
ranges remain incomplete, while the entry snapshot can use a verified tail; no collector
waits for, terminates or creates an agent.

The implementation interpretation is grounded in the version-matched
[protocol](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/protocol/src/protocol.rs),
[session usage update](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/session/mod.rs),
[session state](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/state/session.rs),
[context history](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/context_manager/history.rs)
and [spawn history filtering](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/agent/control/spawn.rs).
`TokenUsageInfo::append_last_usage` advances one session's cumulative counters;
response accounting updates that session's state, while child execution uses a
separate thread/session state. Shared rollout-budget consumption is a separate
operation, not parent `token_count` accumulation. Supported parent/child native
counters are therefore exclusive. Other versions remain unknown and child detail
is non-additive when parent inclusion cannot be verified. Inclusive or overlapping
observations without exact reconciliation retain detail but withhold a complete
combined total; comparing equal numbers is never proof of overlap.

Resume and fork reconstruction can seed prior `token_count` information. Spawn
filtering removes `TokenUsageRecord` inheritance but can retain older `EventMsg`
usage depending on history mode. A fresh timestamp alone therefore cannot prove
a generic fork's zero baseline. An existing child requires an entry snapshot and
an owned causal resubmission; historical ancestry alone never admits continuation.
An inherited fork can use its evidenced pre-birth cumulative baseline; a new
per-thread `token_usage_record` counter uses the verified fresh thread state.
A new non-fork/non-referenced child born during exclusively admitted work can use
the verified initial-zero semantics. Missing or reset baseline categories stay
unknown. Sanitized replay tests use these protocol shapes and never issue paid
model calls.

Each child record preserves workflow/caller/attempt identity, root/immediate
parent/session identity, agent path, owned turns, start/end offsets and cumulative
counters, cutoff, source locator/digest/version, actual `turn_context` model,
known categories and gaps in the existing `cost_records` envelope. Transcript
bodies are not retained. Missing cached or reasoning counters stay unknown;
cached input is included in input, and reasoning in output. Totals use valid
provider totals or verified input plus output, never the sum of all categories.
Cache-write aliases are reconciled once. Monotonic open endpoint refinements
replace earlier projections; finalized replay is idempotent and conflicting final
proof remains incomplete. Unsupported overlapping physical ranges are excluded
from combined totals. Compatibility scalars are known subtotals; records and
coverage distinguish unknown from an attested zero.

Child valuation reuses the rate card pinned at entry, actual model evidence,
verified categories and the existing context-band rules. A database or configured
parent model cannot price a child. Mixed models without aligned counter boundaries,
unknown categories, unknown context bands and missing rates retain token evidence
with unavailable USD. Historical reports never fetch rates or reprice. Existing
status/timeline and the affirmative inclusive closeout report show child detail,
child/caller known subtotals and incomplete coverage. Worker-only progress excludes
Manager descendants; the existing closeout preference remains human-owned.

### Native Manager delegation bracket

The owning helper `native_delegation_accounting.py begin|finalize` validates the
current confirmed Manager contract, supplied fresh facts, persisted host binding,
workflow identity and actual `CODEX_THREAD_ID` on both boundaries. Use one explicit
correlation, begin before authorized native delegation, and finalize in cleanup at
the as-of cutoff. It writes the existing retained Manager envelope under the
accounting locks, with a shared root claim lock preventing overlapping workflow
claims. Only causal spawn/resume work within the selected host turn is admitted;
unbracketed history and unrelated turns stay outside that scope. Entry snapshots,
pinned rates, partial child records and open coverage survive archive and cleanup.
A missing entry or changed binding is rejected; accounting grants no authority.

The separate proposed **Codex App Server live subagent usage ingestion** issue
would ingest `thread/tokenUsage/updated` through an already authorized connection,
with reconnect/replay and turn correlation. It depends on this normalized interval
contract and an authorized observable App Server transport. This issue introduces
no transport, daemon or external issue mutation.

The neutral projection also accepts an explicit `exact_inclusive` parent
attestation naming the included child segment and its exact start/end evidence.
It preserves the parent's recorded amount and treats that child's detail as
non-additive. Current Codex collection emits exclusive evidence only; numerical
similarity or an unsupported inclusive flag cannot establish this attestation.

Native host delegation is restricted to the exact root turn captured at entry.
If that turn completes or aborts before finalize, descendant observations stop
at its evidenced ownership cutoff; later work in the same child session belongs
outside this bracket. Records preserve both that cutoff and the later report
cutoff. A descendant active at the ownership boundary remains partial.

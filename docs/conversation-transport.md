# Conversation transport

`cafe.agents.ConversationTransport` is an internal provider-neutral boundary over
the existing `AgentExecutor` and CLI strategies. Phase chat and event callbacks
use it for provider invocation. It is not a separately versioned package.

## API

Construct the transport with the caller-selected executor and configuration:

```python
from cafe.agents import ConversationTransport
from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentCLI, AgentConfig

executor = AgentExecutor(
    AgentConfig(name="conversation", cli=AgentCLI.CLAUDE, model="selected-model"),
    stream_output=False,
)
transport = ConversationTransport(executor)
capabilities = transport.capabilities("deliver_to_exact_session")

# The caller owns its bootstrap prompt and persistence decisions.
acquired = transport.acquire_session('say "HI"')
session = acquired.observed_session_id
result = transport.deliver_to_exact_session(
    "Delivery event-123: caller-provided content",
    session_id=session,
    delivery_id="event-123",
    on_acceptance=persist_acceptance,
    on_usage=persist_usage,
)
```

The four operations are `acquire_session`, `deliver_to_exact_session`,
`open_interactive_session(initial_prompt=None, ...)`, and `run_one_shot(prompt,
...)`. Every invocation is one attempt. Acquisition clears the resume target
for that attempt, uses the existing callback command, and establishes no event
acceptance. Exact delivery requires nonempty destination and correlation IDs;
the prompt must contain the correlation ID. The configured model is the exact
requested model when present.

`acquire_session` permits only these keyword-only options: `required_evidence`,
`on_usage`, `allowed_tools`, `allowed_directories`, `execution_control`, and
`environment_overrides`. It rejects resume/session overrides, delivery/event IDs,
and `on_acceptance` with `TypeError` before executor invocation. A caller's existing
configured session is restored after the attempt, while acquisition always launches
without a resume target or delivery correlation. Exact delivery accepts the same
keyword-only options plus `on_acceptance`, with its required `session_id` and
`delivery_id` validated before invocation. Unsupported required acceptance evidence
cannot turn acquisition into delivery.

Noninteractive operations reuse executor tool/directory translation and accept
`allowed_tools`, `allowed_directories`, `execution_control`, and
`environment_overrides`. Interactive launch accepts environment overrides and
inherits the terminal and current working directory. One-shot execution keeps
the existing streaming output; `on_response(AgentResponse)` receives its normal
response separately from compact evidence. No response snapshot is added to a
transport result.

`required_evidence` is a frozenset containing any of `session`, `model`, `usage`,
and `acceptance`. Capability admission occurs before launch. Acquisition
inherently requires session support; exact delivery inherently requires session
and acceptance support. Unsupported operations/guarantees raise
`AgentExecutionError` with `failure_code="unsupported"` and no process launch.
A supported format that omits required evidence fails after its single attempt;
optional missing evidence stays unknown. A false acceptance value means the
provider contract did not establish acceptance, not permission to retry.

## Results and failures

`TransportCapabilities` separates operation support from session, model, usage,
and acceptance observation support. Support describes a recognized evidence
format, not a guarantee that every invocation will report those fields.

`TransportResult` is an immutable dataclass with only:

| Field | Meaning |
| --- | --- |
| `observed_session_id` | Provider-verified identity, distinct from a request target |
| `reported_model` | Provider-reported model; configured model is never a substitute |
| `accepted` | Relevant delivery acknowledgement, or unknown |
| `completed` | Recognized terminal evidence, or unknown |
| `usage` | Existing `TokenUsage` summary, or unavailable |
| `failure_code` | Normalized transport failure, or none |
| `error_excerpt` | Sanitized diagnostic, at most 400 characters |
| `returncode` | Process termination code, independent of acceptance/completion |

Unknown values are `None`. Session/model values are limited to 512 characters;
oversize values are invalid, not truncated usable identities. Small private pure
helpers in the existing agent values module share scalar validation and conflict
comparison across batch evidence, streaming observation and parsed-model checks.
Session identities are trimmed after validating their raw length; reported model
strings retain their original spelling. Missing values remain unknown. Provider
record recognition stays in CLI strategies, and failure precedence, sticky stream
conflicts and early acceptance versus final result projection keep their existing
timing and outcomes. Returned usage
turn summaries retain at most 64 numeric entries and contain no provider payload.
The facade adds no transcripts, raw record lists, request snapshots, evidence
files, databases, or recovery store. Existing response output remains separate.

Execution failures expose the same compact result on
`AgentExecutionError.transport_result`. Already observed acceptance and verified
partial usage survive later output failure. Acceptance observers run synchronously
at the first verified acknowledgement, so callers can persist acceptance before
later output. Observer/persistence failures propagate as caller errors; they do
not become provider rejection or permission to replay.

## Provider support and evidence limits

This matrix describes repository adapters and captured-stream/process-double
tests. No live provider session or paid provider call was used for verification.

| Provider | Acquisition/exact identity | Acknowledgement | Model/usage limits |
| --- | --- | --- | --- |
| Claude | `system/init.session_id` | Matching init followed by `stream_event.message_start` | Init/message-start model when supplied; existing usage/cache/cost/duration parser |
| Codex CLI | `thread.started.thread_id` | Matching thread followed by invocation-scoped `turn.started` | Model unknown when absent; existing usage/cache/reasoning/cost/turn parser |
| Gemini | `init.session_id` | Matching init plus user message containing delivery ID | Reported init model and existing result `stats` parser |
| Cursor | `system/init.session_id` | Matching init plus user record containing delivery ID | Reported init model; existing duration statistics, no general verified token counts |
| Copilot | Unique successful terminal `result.sessionId` | Correlated `user.message` and valid terminal result | Structured model only when supplied; ordinary usage/model summary parser; structured usage guarantee unsupported |

All five adapters have existing interactive and one-shot invocation builders.
Interactive launch advertises no session/model/usage/acceptance verification.
Copilot's ordinary filesystem session discovery remains caller compatibility
behavior; it is not verified transport identity. Its structured callback result
must meet the terminal evidence contract. Default/nonconforming adapters do not
gain verified operations merely by supporting interactive launch.

Identity/model conflicts and truncated streams remain uncertain. Claude/Codex
acknowledgements are invocation-scoped; explicit conflicting correlation cannot
be ignored. Gemini/Cursor/Copilot require their existing delivery-bearing user
record. Acknowledgement means acceptance by the destination, not completion or
workflow authority. The callback's existing conclusive-nonacceptance allowlist
remains caller policy; new mismatch/unknown failure names do not expand it.

## Accounting and caller ownership

Each actual subprocess contributes once to executor accounting. `usage=None`
means no recognized statistics; explicitly reported zero is still `TokenUsage`.
Providers may verify only a subset of its fields. Existing supported token,
cache, reasoning, cost, duration and turn statistics are reused.
`on_usage(TokenUsage)` forwards one call's usage once, independently of early
acceptance. Forwarding does not accumulate usage a second time.

`cafe.core.usage.merge_token_usage_stats` is the existing Phase merge primitive;
Phase retains its compatibility delegate. The caller admits and pins an existing
iteration metadata path before invocation. `iteration_usage_sink` merges under
the workspace write lock, preserving other metadata and concurrent statistics.
Reads and publication use no-follow directory descriptors and the admitted
iteration/timestamp identity. Independent calls read the latest legitimate
metadata under the same lock, then pin that read's inode through publication.
Atomic exchange retains a substituted destination for validation and
restoration; it never follows that destination into another file. Linux
`renameat2(RENAME_EXCHANGE)` and macOS `renameatx_np(RENAME_SWAP)` provide this
operation. Unsupported platforms/filesystems return an observable persistence
error without an unsafe replacement. If restoration itself fails, the displaced
metadata is retained at the temporary name and the error reaches the caller for
recovery; it never authorizes provider replay. Each merge exclusively creates a
mode-0700 private staging directory (`.usage-<metadata filename>`) beside the
admitted metadata and pins its descriptor. The mode-0600 source is exchanged
across those pinned directory descriptors. Cleanup verifies and unlinks only its
owned staging entry, then removes the empty owned directory. It never truncates
published metadata: already-open Phase/chat readers and hardlinks, including
aliases introduced during cleanup, retain complete old JSON. Normal success and
failed exchange before publication leave no staging objects. Unexpected entries,
cleanup failures and failed rollback retain complete recovery data; a pre-existing staging
object blocks reuse without being consumed. Staging is never read as usage
evidence or iteration authority. File and directory descriptors close on errors
as well as success.

The cleanup protection boundary is cooperative CAFE writers using the workspace
lock. Private directory permissions do not isolate arbitrary same-account
processes that can alter that namespace at a final syscall boundary. Enforced
isolation from those processes requires separate investigation. The publisher
retains the admitted root/ancestor binding and validates source/destination
substitutions, with explicit errors and rollback; it does not claim a stronger
same-account isolation guarantee.
It never creates an iteration or resolves workflow authority. Chat selects its
configured phase's existing iteration. Callback selection uses the event step
and event-time metadata, not callback `attempt`, and excludes newer iterations.
Standalone calls still return usage and account it without inventing a store.
Persistence failure is observable and never authorizes another delivery.

Chat retains role/playbook validation, configuration, skill/environment setup,
display, session save and baton handling. It explicitly selects the executor's
existing `with_session_recovery` seam for previously supported stale-session and
prompt-length recovery. Ordinary executor defaults retain their recovery wrapper.
The facade never invokes that seam or chooses another provider/model/session.

The callback retains its locks, durable intent/session/dispatch state, prompts,
provider order, exact binding, acceptance writes, fallback and uncertain-outcome
recovery. Its normal calls retain the existing 60-second, 64-KiB, 128-line limits
and isolated bootstrap directory. Persisted acceptance/replay starts no new call.
Legacy single-transport session persistence remains caller-owned compatibility.

## Codex host exception

The existing host path queues one input to an already running confirmed Codex
App thread through a short-lived `codex app-server proxy`. It does not acquire a
child CLI session. It remains in the callback skill rather than the facade.

The host path preserves the original thread ID, confirmed model, inherited host
environment/cwd, eligibility checks, not-loaded resume to that same thread,
interruption rejection, matching queue acknowledgement, FIFO and user-stop
control. `thread/queue/add` wakes the dispatcher. It does not use `queue/start`,
force dispatch, override model/cwd/permissions, create a replacement thread or
daemon, or replay uncertain delivery. Only the proxy closes; the shared host and
thread remain alive. Queue acknowledgement proves acceptance, not completion.

These host RPC and lifecycle constraints cannot share ordinary child-process
invocation without introducing the excluded general host protocol. Existing host
regressions protect this exception.

Issue #465 still owns the manager-specific user-facing command and its policy.
This issue does not deliver or claim to test that future caller. Package
extraction should wait for all three CAFE callers to exercise the API, an actual
external consumer, independence from workflow/manager modules, and demonstrated
benefit from independent versioning.

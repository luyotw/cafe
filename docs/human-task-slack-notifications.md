# Slack notifications for HumanTasks

CAFE can make one Slack Incoming Webhook attempt when any built-in, global, or
project playbook creates a new, durable HumanTask. This path is optional. The
task inbox remains authoritative whether delivery succeeds, is disabled,
deduplicated, denied, or fails.

Each Incoming Webhook is bound to one Slack channel. Operators define named
**destinations** in the login user's machine-owned `~/.cafe/credentials.yaml`
and reference those names in private `~/.cafe/config.yaml` repository routes.
The same resolver serves HumanTasks and workflow callback failure notices. Each
notification selects exactly one destination; fan-out is not supported.

Project configuration, playbooks, hooks, tasks, agent responses, files in the
working directory and environment variables cannot choose a destination, URL
or credential path. CAFE resolves the login account's home independently of
mutable `HOME`.

## Set up the supported path

1. Create an Incoming Webhook for each channel that should receive notices,
   following your workspace's Slack administration policy. CAFE does not
   create Slack apps or webhooks.
2. Create the fixed credential store privately, using your login account:

   ```bash
   mkdir -p ~/.cafe
   install -m 600 /dev/null ~/.cafe/credentials.yaml
   ${EDITOR:-vi} ~/.cafe/credentials.yaml
   chmod 600 ~/.cafe/credentials.yaml
   ```

   For an existing installation, use the migration steps below instead of
   overwriting credentials. Populate the store with real channel-bound URLs
   in place of the placeholders:

   ```yaml
   version: 1
   slack:
     destinations:
       default:
         webhook_url: https://hooks.slack.com/services/...
       openfun:
         webhook_url: https://hooks.slack.com/services/...
       operations:
         webhook_url: https://hooks.slack.com/services/...
   ```

   `default` is required and is used when no project route matches, including
   notifications without a repository identity. The store must be a regular
   file owned by the login user, with no group/other permissions and no hard
   links. Symlinks (including broken ones), directories, FIFOs, sockets and
   devices are rejected. Never commit credentials or copy them into project
   files, playbooks, tasks or scripts.
3. Optionally configure delivery and named routes in `~/.cafe/config.yaml`:

   ```yaml
   notifications:
     human_tasks:
       enabled: true
       transport: slack
       projects:
         /home/you/work/cafe:
           destination: default
         /home/you/work/openfun:
           destination: openfun
         /home/you/work/production-tools:
           destination: operations
         /home/you/work/another-tool:
           destination: operations
   ```

   Set `enabled: false` to record disabled outcomes without requiring any
   credential. Omitting the transport setting retains Slack as the default.
   An omitted or null `projects` value means no routes. Any nonempty project
   map still requires a login-user-owned, regular, single-link private config
   (`chmod 600 ~/.cafe/config.yaml`), even though named routes contain no URL.
   The config must not be a symlink. Do not commit this machine configuration.

   Routes use normalized **exact absolute repository paths**. CAFE resolves
   linked worktrees back to their parent repository, so the parent checkout
   route also covers `.cafe/worktrees/*` workflows. Multiple repositories may
   share one destination. Explicit `destination: default` is valid. Relative
   keys do not match, and unmatched malformed sibling route values do not
   block another repository. Multiple keys resolving to the active repository
   must agree on one destination; conflicting names fail closed.
4. Run a supported workflow normally. A real durable pending HumanTask, such
   as an output-review or permission task, causes one immediate delivery
   attempt. Inspect its receipt as described below. Delivery failure leaves
   the task available in the normal inbox.

No project-specific playbook, skill, credential or notification script is
required.

### Store validation and fail-closed routing

The store's root contains exactly `version` and `slack`; `version` is integer
`1` (not a boolean or string), and `slack` contains exactly `destinations`.
Every mapping key must be a string. Each destination contains exactly one
string field, `webhook_url`. Names are case-sensitive and match
`^[a-z][a-z0-9_-]{0,63}$`. The reserved `default` destination is mandatory.
Unknown fields, duplicate keys, YAML anchors, aliases, merge keys and explicit
tags are invalid at every level. The file must use UTF-8.

CAFE reads at most 64 KiB of raw credential bytes and accepts at most 128
destinations, including `default`. It validates **every** destination before
selecting a route: an invalid unused webhook invalidates the entire store.
Only HTTPS URLs on `hooks.slack.com` with a `/services/...` webhook path are
accepted. Port may be omitted or explicitly `443`; userinfo, other ports,
query strings, fragments and HTTP redirects are rejected.

A selected v1 route contains exactly `{destination: <name>}`. URLs, arbitrary
credential paths, multiple destinations, mixed legacy/new fields and extra
fields are invalid. A legal name absent from a valid store produces
`slack_credentials_destination_missing`; it never falls back to `default`.
Missing `default` is instead `slack_credentials_invalid` for the entire store.
Machine config retains its existing 64 KiB limit and 128-project route limit.

Each normal resolution attempts exactly one no-follow, non-blocking open of
`~/.cafe/credentials.yaml`. A successful open is authoritative for metadata,
bounded reading and parsing, even if the path is then replaced or removed.
Only that open returning `ENOENT` selects legacy mode for that invocation.
An observed v1 store that is unsafe, unreadable, empty or invalid fails closed;
it never reads the legacy fallback or selects a legacy inline URL. A store
created after the `ENOENT` observation takes effect on the next resolution.
CAFE never creates, migrates, rewrites or deletes operator credentials.

### Rotate a webhook

Create the replacement webhook through Slack's administration interface.
Prepare a private copy of `credentials.yaml`, replace the chosen destination's
URL and retain valid entries for all other destinations. Install the copy at
the fixed path with mode `0600` and login-user ownership. Using a replacement
file on the same filesystem and renaming it avoids exposing partial YAML to
notifications. Repository routes keep the same destination names.

Verify a genuine new notification and its secret-free receipt before revoking
the old webhook in Slack. An invocation that already opened the old inode may
finish using that credential; subsequent resolutions use the replacement.
Keep any temporary files or backups private, since they contain credentials.

### Migrate from the deprecated legacy sources

Legacy `~/.slack-webhook` and private per-project inline `{webhook_url: ...}`
routes remain supported **only while the fixed v1 store is absent**. In legacy
mode, a matching inline URL takes precedence over the fallback file. Selected
`destination` routes are invalid in legacy mode. Conversely, inline URLs are
invalid selected routes once the v1 store exists. Legacy removal timing and
test credential migration are outside this change.

For a single fallback credential:

1. Temporarily set `notifications.human_tasks.enabled: false` in machine
   config. Preserve its previous value for restoration, and keep a private
   backup of existing settings and credentials.
2. Stage `~/.cafe/credentials.yaml.new` with mode `0600`, login-user ownership
   and the v1 schema above. Copy the URL from `~/.slack-webhook` into `default`.
   The staged filename is for manual preparation; CAFE only reads the fixed
   `credentials.yaml` path.
3. Install the staged store at `~/.cafe/credentials.yaml`, restore the previous
   enabled setting and verify the receipt for a genuine new HumanTask. Keep
   the deprecated fallback private for rollback; CAFE no longer reads it.

For multiple inline repository routes:

1. Disable notifications and preserve private backups as above.
2. Stage a v1 store with the old fallback URL as `default`. Copy each inline
   webhook into a named destination such as `openfun` or `operations`. Reuse
   one name when multiple repositories intentionally share a webhook.
3. While delivery is disabled, replace every applicable inline route with its
   destination reference. For example:

   ```yaml
   # Before: old private machine route
   /home/you/work/openfun:
     webhook_url: https://hooks.slack.com/services/...
   ```

   ```yaml
   # After: replacement route
   /home/you/work/openfun:
     destination: openfun
   ```

   Keep only the replacement entry; do not retain duplicate repository keys.
   Preserve absolute repository paths and keep config mode `0600`.
4. Install the staged store at the fixed path, restore the previous enabled
   setting and verify new notifications for routed and unrouted repositories.
   All destinations must be valid, including those not used by the first test.

### Roll back in a complete sequence

1. Disable notifications while changing formats.
2. **First restore** a private, valid legacy `~/.slack-webhook` and any former
   inline `{webhook_url: ...}` routes in private machine config. Replace all
   `destination` routes with their legacy equivalent (or remove them if the
   intended legacy behavior is the fallback). Restore ownership and mode
   `0600` on credential-bearing files.
3. **Only then** move `~/.cafe/credentials.yaml` aside to a private backup or
   remove it, so the fixed path is absent and the next resolution selects
   legacy mode.
4. Restore the prior enabled setting and verify a new notification receipt.

Deleting the store while leaving selected `destination` routes is an invalid
half-migration and fails closed. An invalid store left at the fixed path also
prevents legacy fallback; repairing credentials or deliberately completing the
rollback is required.

### Keep coverage-test notifications separate

The global pytest bootstrap marks every test process, whether pytest starts in
the repository root or a Git worktree. HumanTasks materialized by test fixtures
and workflow callback failure notices use only the separate fixed credential
`~/.cafe/test-slack-webhook`; subprocesses spawned by those tests inherit the
same route. With `CAFE_TEST_RUN_SLACK_NOTIFICATIONS=1`, CAFE bypasses the normal
v1 store, named destinations, project routing and legacy fallback. The credential must be a private
regular file owned by the login user. If it is missing or invalid, test-run
HumanTask delivery fails closed and never falls back to the normal HumanTask
channel. The marker selects this one fixed test path; it cannot supply a URL, channel,
destination or credential path from project content.

## What the notification contains

The notification identifies the repository and CAFE issue, then presents the
current phase and required action in readable language. Workflow IDs, HumanTask
IDs, raw task-type identifiers, and terminal commands are intentionally omitted
from Slack. Use CAFE's task inbox to find and complete pending work; Slack is a
discovery aid only and cannot inspect, answer, approve, cancel, or complete a
task.

Standard plan clarification messages ask the user to answer clarification
questions. PR local-review messages ask the user to review changes and follow-up
proposals, then decide whether to request fixes or confirm continuation. The PR
phase is labeled as preparation and review; the notification does not imply merge
authorization. Unknown task types retain the generic return-to-CAFE action.

## Notification language

Every notification is written in the workflow's stored conversation language.
`docs/language-policy.md` owns how that value is resolved and stored; this
document records only which text actually exists.

The notification text is developer-authored, and it is authored for **English
and Traditional Chinese only**. A workflow whose conversation language has no
authored text — including Simplified Chinese, which is never treated as
interchangeable with Traditional Chinese — receives that one message in English.
The fallback is silent: nothing is added to the delivered message, no separate
warning is sent, and the workflow's stored language is unchanged, so a later
message in a supported language is unaffected. A workflow created before the
language contract existed has no stored language, and its notifications use the
same English default without anything being written back to it.

Accepting a language tag is not a claim that CAFE supports it. General CLI help,
command output, and diagnostics are English today; that statement records current
behavior and is neither a commitment to keep it English nor a promise to
localize it.

## Trust and credential boundary

The package-owned `cafe.slack.human_task` capability declares only non-secret
inputs: the six required fields — repository, CAFE issue, workflow ID, step,
task ID, and task type — plus the optional conversation locale that selects the
message language. Its registered network effect is fixed to `hooks.slack.com`,
and its
symbolic credential is `slack_human_task_webhook`. Prompts, raw agent output,
task feedback, project-defined fields, and credential values are never passed
to the capability, notification, or receipt.

The trusted package adapter validates the whole machine-owned v1 store and
selects an exact repository destination reference or `default`. Only an absent
v1 store permits the deprecated private inline route / `~/.slack-webhook`
resolver. HumanTasks and workflow callback failure notices share these rules;
test runs use only the separately provisioned fixed test credential above. It accepts HTTPS Slack Incoming
Webhook URLs only, rejects redirects, and bounds the connection attempt to five
seconds. The URL is used as the outbound request destination but is not put in
the message, repository, HumanTask record, project-hook input, log, or receipt.
Project-authored hooks remain sandboxed and never inherit this capability or
credential.

## Inspect delivery receipts

Every allowed, disabled, skipped, deduplicated, denied, failed, or successful
decision appends a receipt to the issue blackboard. The receipt is correlated by
`workflow_id` and `task_id` and contains the request decision and outcome
without the webhook value or task prompt.

For a focused local inspection:

```bash
jq '.capability_receipts[]
  | select(.capability == "cafe.slack.human_task")
  | {workflow_id, task_id, success, category, code, decision, outcome}' \
  .cafe/issues/<issue>/blackboard.json
```

Interpret the stable fields as follows:

| Result | Receipt evidence | Meaning |
| --- | --- | --- |
| Successful | `success: true`, `outcome: success` | Slack returned HTTP 200 with `ok`. |
| Disabled | code `human_task_notification_disabled`, `outcome: disabled` | The machine configuration explicitly disabled delivery; the task remains pending. |
| Skipped | code `human_task_notification_config_invalid`, `human_task_notification_transport_unsupported`, or `human_task_notification_not_actionable` | The machine configuration is unusable, the provider is unsupported, or the task is no longer actionable; no post occurs. |
| Deduplicated | code `human_task_notification_deduplicated`, `outcome: deduplicated` | CAFE already recorded a delivery decision for this task, so it does not post again. |
| Denied | `success: false`, decision outcome `deny` | The exact request failed registered argument, effect, credential, permission, or package policy checks; the adapter did not run. |
| Missing or unreadable credential | code `slack_credentials_missing`, `slack_credentials_empty`, `slack_credentials_unreadable`, or `slack_credentials_unsafe` | Repair the selected machine credential file, ownership and permissions; v1 errors never trigger legacy fallback. |
| Invalid credential | code `slack_credentials_invalid` | Repair the entire v1 schema and every destination URL, or the legacy URL if the v1 store is absent. |
| Missing named destination | code `slack_credentials_destination_missing` | Define the referenced name in the valid v1 store or correct the selected machine route. |
| Unsafe route config | code `human_task_notification_config_unsafe` | Restore login ownership, a regular single-link file and private permissions on nonempty machine routing config. |
| Slack/transport failure | code `slack_http_error`, `slack_response_not_ok`, `slack_timeout`, or `slack_transport_error` | Slack rejected the post or could not be reached. |
| Interrupted | code `slack_notification_interrupted` | CAFE durably began an attempt but stopped before its final outcome could be recorded; it does not resend because Slack may already have accepted the post. |
| Internal fail-closed error | code `slack_notification_internal_error` | The trusted path could not complete evaluation; inspect local installation/runtime health. |

## Recover safely

A notification result never completes, erases, redirects, or changes the
HumanTask. A replacement task atomically marks only its explicitly superseded
predecessor cancelled; the cancelled task cannot be completed, while unrelated
pending tasks remain actionable. Inspect and complete the same pending task
through the normal inbox:

```bash
cafe task ls
cafe task inspect <task-id>
cafe task complete <task-id>
```

If a process stops after the task is durable but before an attempt begins, the
next workflow resume performs the missing attempt. Once the attempt has begun,
CAFE records that state before outbound I/O; an interrupted attempt is not sent
again because Slack Incoming Webhooks offer no idempotent resend contract. Fix
credentials or connectivity before the next HumanTask is created. CAFE does
not report a failed or interrupted attempt as a success, so the receipt remains
the accurate record of that attempt.

## Scope

This feature does not provide bidirectional Slack interaction, task completion
from Slack, callbacks, a daemon, scheduling, reminders, due dates, an SLA,
automatic retries, dynamic destinations, or a generic provider interface. The
former `notify-slack.sh` phase scripts are retired; project scripts remain
untrusted and cannot become a notification authority path.

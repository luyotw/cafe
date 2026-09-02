# Slack notifications for HumanTasks

CAFE can make one Slack Incoming Webhook attempt when any built-in, global, or
project playbook creates a new, durable HumanTask. This path is optional. The
task inbox remains authoritative whether delivery succeeds, is disabled,
deduplicated, denied, or fails.

The webhook selects one channel when it is created in Slack. CAFE does not
accept a channel, destination, webhook URL, or credential path from a playbook,
project hook, task, agent response, or environment variable.

## Set up the supported path

1. In Slack, create an Incoming Webhook for the channel that should receive
   CAFE HumanTask notifications. This is an operator action governed by the
   workspace's Slack administration policy. Installing or running CAFE does not
   authorize CAFE to create a Slack app or webhook.
2. Configure the machine-owned transport in `~/.cafe/config.yaml` (this setting
   is optional because Slack remains the compatibility default). Disable it
   explicitly when this machine should record a disabled outcome without making
   an outbound request:

   ```yaml
   notifications:
     human_tasks:
       enabled: true
       transport: slack
   ```

3. Create the fixed user-owned credential file and restrict it to your account:

   ```bash
   install -m 600 /dev/null ~/.slack-webhook
   ${EDITOR:-vi} ~/.slack-webhook
   chmod 600 ~/.slack-webhook
   ```

4. Put only the channel-bound Incoming Webhook URL in that file, on one line.
   The supported form is `https://hooks.slack.com/services/...`. Never commit
   this file or copy its value into `.cafe/config.yaml`, a playbook, a task, or
   a script. CAFE resolves the login account's home directory independently of
   `HOME` and rejects credential files that are symlinks, non-regular files,
   owned by another user, hard-linked, or accessible by group/other users.
5. Run any supported workflow normally. Do not invoke a notification script or
   synthetic hook. When CAFE durably creates a real pending HumanTask, such as
   an output-review or permission task, it makes one immediate attempt.

No project-specific playbook, skill, credential, or script is required in a
clean repository.

### Keep coverage-test notifications separate

`scripts/test-coverage.sh` marks only its own process as a coverage test run.
HumanTasks materialized by test fixtures then use the separate fixed credential
`~/.cafe/test-slack-webhook`; it must also be a private regular file owned by
the login user. If that credential is missing or invalid, test-run HumanTask
delivery fails closed and never falls back to the normal HumanTask channel.
The marker chooses only between these two package-defined paths: it cannot
supply a URL, channel, or credential path from a project or environment value.

## What the notification contains

The notification identifies the repository, workflow ID, step, HumanTask ID,
and task type. It also includes the supported commands:

```text
cafe task inspect <task-id>
cafe task complete <task-id>
```

Use `cafe task ls` to find other pending work. Slack is a discovery aid only;
it cannot inspect, answer, approve, cancel, or complete a task.

## Trust and credential boundary

The package-owned `cafe.slack.human_task` capability declares exactly five
non-secret inputs: repository, workflow ID, step, task ID, and task type. Its
registered network effect is fixed to `hooks.slack.com`, and its symbolic
credential is `slack_human_task_webhook`. Prompts, raw agent output, task
feedback, project-defined fields, and credential values are never passed to the
capability, notification, or receipt.

The trusted package adapter reads the fixed `~/.slack-webhook` credential only
after the capability request passes validation and policy; the coverage test
runner is the sole exception and uses the separately provisioned fixed test
credential above. It accepts HTTPS Slack Incoming Webhook URLs only, rejects
redirects, and bounds the connection attempt to five seconds. The URL is used
as the outbound request destination but is not put in the message, repository,
HumanTask record, project-hook input, log, or receipt. Project-authored hooks
remain sandboxed and never inherit this capability or credential.

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
| Missing or unreadable credential | code `slack_credentials_missing`, `slack_credentials_empty`, `slack_credentials_unreadable`, or `slack_credentials_unsafe` | Repair the fixed user credential file, ownership, and permissions. |
| Invalid credential | code `slack_credentials_invalid` | Replace the file contents with a valid channel-bound Slack HTTPS Incoming Webhook URL. |
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
automatic retries, multiple or dynamic destinations, or a generic provider
interface. The former `notify-slack.sh` phase scripts are retired; project
scripts remain untrusted and cannot become a notification authority path.

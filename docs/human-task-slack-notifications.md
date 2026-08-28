# Slack notifications for HumanTasks

CAFE can make one Slack Incoming Webhook attempt when the built-in `standard`
workflow creates a new, durable HumanTask. This path is optional. The task inbox
remains authoritative whether delivery succeeds, is denied, or fails.

The webhook selects one channel when it is created in Slack. CAFE does not
accept a channel, destination, webhook URL, or credential path from a playbook,
project hook, task, agent response, or environment variable.

## Set up the supported path

1. In Slack, create an Incoming Webhook for the channel that should receive
   CAFE HumanTask notifications. This is an operator action governed by the
   workspace's Slack administration policy. Installing or running CAFE does not
   authorize CAFE to create a Slack app or webhook.
2. Create the fixed user-owned credential file and restrict it to your account:

   ```bash
   install -m 600 /dev/null ~/.slack-webhook
   ${EDITOR:-vi} ~/.slack-webhook
   chmod 600 ~/.slack-webhook
   ```

3. Put only the channel-bound Incoming Webhook URL in that file, on one line.
   The supported form is `https://hooks.slack.com/services/...`. Never commit
   this file or copy its value into `.cafe/config.yaml`, a playbook, a task, or
   a script. CAFE resolves the login account's home directory independently of
   `HOME` and rejects credential files that are symlinks, non-regular files,
   owned by another user, hard-linked, or accessible by group/other users.
4. Run the built-in `standard` workflow normally. Do not invoke a notification
   script or synthetic hook. When CAFE durably creates a real pending
   HumanTask, such as an output-review task, it makes one immediate attempt.

No project-specific playbook, skill, credential, or script is required in a
clean repository.

## What the notification contains

The notification identifies the repository, workflow ID, HumanTask ID, and
reason human action is required. It also includes the supported commands:

```text
cafe task inspect <task-id>
cafe task complete <task-id>
```

Use `cafe task ls` to find other pending work. Slack is a discovery aid only;
it cannot inspect, answer, approve, cancel, or complete a task.

## Trust and credential boundary

The package-owned `cafe.slack.human_task` capability declares exactly four
non-secret inputs: repository, workflow ID, task ID, and reason. Its registered
network effect is fixed to `hooks.slack.com`, and its symbolic credential is
`slack_human_task_webhook`.

The trusted package adapter reads `~/.slack-webhook` only after the capability
request passes validation and policy. It accepts HTTPS Slack Incoming Webhook
URLs only, rejects redirects, and bounds the connection attempt to five
seconds. The URL is used as the outbound request destination but is not put in
the message, repository, HumanTask record, project-hook input, log, or receipt.
Project-authored hooks remain sandboxed and never inherit this capability or
credential.

## Inspect delivery receipts

Every allowed, denied, failed, or successful attempt appends a receipt to the
issue blackboard. The receipt is correlated by `workflow_id` and `task_id` and
contains the request decision and outcome without the webhook value.

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
| Denied | `success: false`, decision outcome `deny` | The exact request failed registered argument, effect, credential, permission, or package policy checks; the adapter did not run. |
| Missing or unreadable credential | code `slack_credentials_missing`, `slack_credentials_empty`, `slack_credentials_unreadable`, or `slack_credentials_unsafe` | Repair the fixed user credential file, ownership, and permissions. |
| Invalid credential | code `slack_credentials_invalid` | Replace the file contents with a valid channel-bound Slack HTTPS Incoming Webhook URL. |
| Slack/transport failure | code `slack_http_error`, `slack_response_not_ok`, `slack_timeout`, or `slack_transport_error` | Slack rejected the post or could not be reached. |
| Interrupted | code `slack_notification_interrupted` | CAFE durably began an attempt but stopped before its final outcome could be recorded; it does not resend because Slack may already have accepted the post. |
| Internal fail-closed error | code `slack_notification_internal_error` | The trusted path could not complete evaluation; inspect local installation/runtime health. |

## Recover safely

A notification result never completes, erases, redirects, or changes the
HumanTask. Inspect and complete the same pending task through the normal inbox:

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
automatic retries, multiple or dynamic destinations, a generic provider
interface, or direct notification-script execution. Project and legacy
notification scripts are not the supported authority path.

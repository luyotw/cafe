# Selecting and helping implement a verification tool

Reuse existing CI, deployment and public checks. The same CAFE waiting mechanism
can run different approved tools; CAFE core never interprets provider-specific
workflows or build names. No new checker is required when a suitable tool exists.

## Approved tool

A delivery request's verification is either an explicit `not_required_reason`
tied to the confirmed scope, or:
```json
{
  "scope": "The agreed tests, deployment and public endpoint checks must pass",
  "tool": {
    "capability": "cafe.delivery.verify",
    "owner": "cafe-deliver_development",
    "path": "scripts/verify_github_actions.py",
    "sha256": "<SHA256 of the exact script bytes>",
    "options": {
      "workflows": [{
        "path": ".github/workflows/deploy.yml",
        "jobs": {"deploy": ["Deploy", "Check public endpoints"]}
      }]
    }
  }
}
```

The options above belong exclusively to the optional GitHub tool. Other tools
use their own ordinary data, not a universal CI language. This tool requires
existing target-branch push workflows and exact required job/step names.
Succeeded workflows with skipped required jobs or steps do not pass.

A project-owned self-contained Python script may use `owner: repository` and
a repository-relative `path`. Other reusable tools belong to a declared skill's
`scripts/`. The host checks the script hash and executes those already-read
bytes with isolated Python, without shell interpolation or importing checkout
modules. Use the standard library and existing trusted CLIs. An entry hash does
not freeze helpers, templates or mutable config; pin dependencies separately
before relying on them, or keep the checker self-contained.

`cafe.delivery.verify` is the existing GitHub credential/network boundary for
the bundled GitHub tool. A different tool must name an already registered,
trusted host capability using the same `verify_delivery_tool` implementation,
the same arguments and
outputs, `idempotency: safe`, no writes/browser effects, and its actual credential
and network needs. Repository manifests cannot grant host permissions. If no
suitable registered capability exists, delivery cannot automatically run that
tool; identify the missing execution capability as implementation work. This
extends tool execution, not CI platform adapters.
Keep credentials out of options and output. The delivery action review displays the tool,
hash, options, capability manifest and repeated host-execution authority.
Host Python is not an enforced read-only sandbox; inspect the code and its
dependencies before approving it. A changed tool or boundary needs fresh review.

## Input and output

The fixed host executor supplies one JSON object on stdin:
`repository`, `target_branch`, the successful integration receipt's `commit`,
and approved `options`. Tool options cannot override those host identities.
The checker must obtain version evidence from CI or the deployed system, rather
than simply echoing the requested SHA.

Emit one JSON object on stdout and exit zero after a completed observation:
```json
{"state":"succeeded","commit":"<observed full commit SHA>","evidence":{"build":"<stable build/run identity and results>"}}
```

States are `pending`, `succeeded`, `failed` and `unknown`. Actual CI failure
is `failed` with exit zero; timeout, nonzero exit and invalid output mean unknown.
An unknown observation may set boolean `retryable: true` for a transient query
failure; credentials, invalid configuration and identity contradictions are
not transient. Output is bounded to 64 KiB. Keep timestamps and scheduling out
of evidence so final acceptance can compare a fresh observation to the shown
one; reruns must change stable evidence or become pending/failed.

## Missing tool or required check

Identify the missing capability during planning and PR preparation whenever
possible. For a clear requirement, write the script implementation and fixture
tests as normal work in the current plan/correction, rather than asking the user
to solve an engineering choice. When discovered during delivery, help with a
concrete draft in the output and route the remaining work through the injected
development/review/PR correction path. Keep drafts provisional; never execute
unapproved host code or claim a draft proves delivery. A separate issue is an
optional proposal for independent work, not a mandatory step.

Test success, pending, actual failure, unavailable tools, transient network
errors, wrong commit/version and missing evidence. The completed tool joins
the reviewed source and is frozen in a fresh delivery action decision.

## Release-triggered deployment observer

For a release workflow rather than a target-branch push workflow, select
`scripts/verify_github_release_deployment.py` under `cafe-deliver_development`
with the same existing `cafe.delivery.verify` capability. It is self-contained,
uses only read-only GitHub API calls through `gh api --hostname github.com`,
and has a shared 25-second query deadline. It neither creates a deployment nor
runs a workflow. Its approved options pin the environment, workflow and all
required jobs/steps, including the job associated with the deployment status:

```json
{
  "environment": "pypi",
  "workflow": ".github/workflows/publish-pypi.yml",
  "deployment_job": "Publish distributions to PyPI",
  "jobs": {
    "Build distributions": ["Verify release identity", "Build and inspect distributions"],
    "Publish distributions to PyPI": ["Publish distributions"]
  }
}
```

These names describe this repository's existing release workflow; other
repositories must review their actual environment/workflow/job/step contract.
Options cannot override host-supplied repository, target branch or integration
commit. Hash the exact script bytes after implementation/review and submit the
whole verification scope in a fresh delivery action review. A source/QA commit
must never substitute for the successful host integration receipt's commit.

The observer considers only the latest deployment returned for that environment,
never falls back to an older success, and proves that its full SHA equals or
descends from the integration commit using GitHub's commit comparison. An older
or unrelated deployment remains pending. For a related successful deployment it
requires a published release, resolves its tag to the deployed SHA, and binds the
status log URL to the same repository's release run and deployment job. The run's
SHA, tag, event and workflow path must match; every required job/step must finish
successfully in the current run attempt. An old deployment job cannot certify a
new attempt. Truncated job pages, identity contradictions or missing required
results never pass.

Missing/running deployments or release runs remain `pending`; completed deployment
or required workflow/job/step failures are `failed`. `unknown` with
`retryable: true` is reserved for transient timeouts, transport/rate-limit/server
failures. Invalid configuration, credentials or identity evidence produce
non-retryable unknown. Observations return the host integration commit plus stable
deployment/status IDs, deployed SHA, ancestry, release tag/version/URL, workflow
run/attempt/URL and required job/step results. No scheduling timestamps are included.
The checked release tag is version evidence, not a producer-release allowlist.

GitHub API contracts: [deployments](https://docs.github.com/en/rest/deployments/deployments),
[deployment statuses](https://docs.github.com/en/rest/deployments/statuses),
[workflow runs](https://docs.github.com/en/rest/actions/workflow-runs),
[workflow jobs](https://docs.github.com/en/rest/actions/workflow-jobs), and
[commit references/comparisons](https://docs.github.com/en/rest/commits/commits).
These read-only observations do not prove that a pending merge/deployment happened;
final delivery remains blocked until the approved action and matching deployed
release actually complete.

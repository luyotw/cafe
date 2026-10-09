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
Keep credentials out of options and output. The PR review displays the tool,
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
the reviewed source and is frozen in a fresh PR action decision.

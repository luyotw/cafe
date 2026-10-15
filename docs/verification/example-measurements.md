# Equal-coverage example measurements

One local run of the executable example on 2026-10-15. All four mandatory suites
remained selected and completed successfully; no optional coverage was removed.
Durations are observations, not a performance threshold or promised speedup.

| Run | Executed | Reused | Profile wall seconds | Caller wall seconds |
| --- | ---: | ---: | ---: | ---: |
| first | 4 | 0 | 0.293063 | 0.877683 |
| repeat | 4 | 0 | 0.258681 | 0.768090 |
| cached | 0 | 4 | 0.007887 | 0.512198 |

The third call avoids four duplicate suite executions. Context probes, Git checks,
digest verification and CLI startup still have a cost. Observed failures: none in
these three calls; pending/unchecked suites in this profile: none. Deliberate
midnight and notification faults fail in the integration tests. This synthetic
profile makes no claim about repository-wide tests or live product behavior.

After-QA escaped defects: **unknown**; after-merge escaped defects: **unknown**.
Source/window: local fixture execution only; no product QA observation, merge or
deployment occurred. Product QA and delivery retain those observation owners.

Reproduce with `examples/verification_profiles/README.md`. Raw bounded evidence:

```json
{
  "fixture_head": "4e8aaba1282450a75b597fc02a4bdb8830a7575c",
  "files": {
    "checks.py": "48bb5d3dc55807bf83cf01e3c94dad4aa69a05351f68308139ca7583f189cf0c",
    "profiles.json": "cf0d564080a88869b526310760cb6bea37da5bd1aabd0eb735bdacd7ae976ce9"
  },
  "context": {
    "TZ": "Asia/Taipei",
    "FROZEN_CLOCK": "2026-10-15T15:59:59+00:00",
    "SEED": "656",
    "BARRIER": "notification-before-response",
    "FAULT": "none"
  },
  "measurements": [
    {
      "run": "first",
      "executed": 4,
      "reused": 0,
      "selected": [
        "bootstrap",
        "logout",
        "midnight",
        "notification"
      ],
      "status": "passed",
      "suite_wall_seconds": 0.2930626440793276,
      "caller_wall_seconds": 0.8776832576841116,
      "exit_code": 0,
      "pending": []
    },
    {
      "run": "repeat",
      "executed": 4,
      "reused": 0,
      "selected": [
        "bootstrap",
        "logout",
        "midnight",
        "notification"
      ],
      "status": "passed",
      "suite_wall_seconds": 0.25868051685392857,
      "caller_wall_seconds": 0.76809037104249,
      "exit_code": 0,
      "pending": []
    },
    {
      "run": "cached",
      "executed": 0,
      "reused": 4,
      "selected": [
        "bootstrap",
        "logout",
        "midnight",
        "notification"
      ],
      "status": "passed",
      "suite_wall_seconds": 0.00788709707558155,
      "caller_wall_seconds": 0.5121977757662535,
      "exit_code": 0,
      "pending": []
    }
  ]
}
```

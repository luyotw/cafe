# Equal-coverage example measurements

One local run of the executable example on 2026-10-15. All four mandatory suites
remained selected and completed successfully; no optional coverage was removed.
Durations are observations, not a performance threshold or promised speedup.

| Run | Executed | Reused | Profile wall seconds | Caller wall seconds |
| --- | ---: | ---: | ---: | ---: |
| first | 4 | 0 | 0.255724 | 0.831356 |
| repeat | 4 | 0 | 0.283127 | 0.806261 |
| cached | 0 | 4 | 0.007169 | 0.574595 |

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
  "fixture_head": "7e2bc76073cacbcae70fcd34344dee22f23031a9",
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
      "suite_wall_seconds": 0.25572441704571247,
      "caller_wall_seconds": 0.8313561789691448,
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
      "suite_wall_seconds": 0.2831266652792692,
      "caller_wall_seconds": 0.8062605950981379,
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
      "suite_wall_seconds": 0.007168708369135857,
      "caller_wall_seconds": 0.5745947044342756,
      "exit_code": 0,
      "pending": []
    }
  ]
}
```

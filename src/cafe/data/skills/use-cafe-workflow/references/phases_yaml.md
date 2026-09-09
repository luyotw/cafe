# Issue-owned phase execution configuration

`.cafe/phases.yaml` is the only runtime execution configuration. Its schema and
validity are defined by `cafe.utils.phase_config`; this reference is guidance,
not an alternate schema.

After kickoff confirmation, serialize the exact dynamic step names and ordered
CLI/model entries to JSON and install them in the active worktree:

```bash
python3 <skill-dir>/scripts/write_phase_config.py \
  --chains-json <confirmed-chains.json> \
  --target <active-worktree>/.cafe/phases.yaml
```

The writer requires a non-empty `name` and `clis` list for each discovered
step; `role` remains optional metadata. It writes a same-directory candidate, validates every
step through the core parser, and atomically replaces the target only after
validation succeeds.

Illustrative only:

```yaml
quality_gate:
  name: Reviewer
  role: reviewer
  clis:
    - cli: codex
      model: <confirmed-exact-model>
```

Add further `clis` entries only for fallbacks confirmed in the kickoff. A
single-entry list is a valid primary-only chain.

Do not copy this example as a default, infer missing steps, or read legacy
configuration. For validation questions, inspect `src/cafe/utils/phase_config.py`.

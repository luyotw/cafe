# Correction session paired A/B

Use this protocol only when evaluating `correction_session: fresh|resume` or an equivalent correction-continuation change.

## Pair construction

1. Freeze one base repository SHA, correction input, model, reasoning effort, CLI version, resolved playbook, dependencies, and test environment for each pair. Record SHA-256 fingerprints for the exact correction input, resolved playbook, and normalized environment manifest.
2. Create two isolated clean worktrees from that SHA. Run one `resume` control and one `fresh` treatment; randomize arm order across pairs so provider timing is not consistently assigned to one policy.
3. Keep playbook, crew, CLI version, dependencies, and test environment equal. Do not run the second arm on files mutated by the first.
4. Record actual billed Codex credits for each arm. Do not substitute a rate-card estimate. Copy raw token and wall-time telemetry from the iteration metadata, not from `streaming.jsonl`.
5. Record `success`, artifact/checklist/baton correctness, and high-severity review findings for both arms. A pair with missing telemetry or uncertain quality is incomplete, not a pass.

## Manifest

Write one JSON object with `schema_version: 1`, a `protocol` object, and a `pairs` array. The protocol explicitly attests `randomized_order`, `isolated_worktrees`, and `actual_billed_credits` as booleans. Each pair contains shared `id`, `model`, `effort`, `cli_version`, `repo_sha`, `correction_sha256`, `playbook_sha256`, `environment_sha256`, and `arm_order` (`fresh_first` or `resume_first`), plus `resume` and `fresh` objects. Balance the two arm orders across the final dataset; their counts may differ by at most one. Each arm must contain:

- `policy`: `resume` or `fresh`
- `credits`, `wall_seconds`
- `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`
- `success`, `artifact_correct`, `checklist_correct`, `baton_correct`
- `high_severity_findings`

Analyze it with:

```bash
python3 <skill-dir>/scripts/analyze_correction_ab.py <manifest.json>
```

The script reports every pair, arm-order balance, protocol readiness, medians, deterministic 95% bootstrap confidence intervals, and `claim_ready`. Do not claim the 30% target until `claim_ready: yes`; directional unpaired workflow observations remain operational telemetry only.

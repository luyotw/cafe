# Original evidence supplied for issue #647

The user supplied these historical sources through
[the issue clarification](https://github.com/luyotw/cafe/issues/647#issuecomment-6205164057)
and [the original evidence gist](https://gist.github.com/luyotw/6f0aff58cb8a3227d34958fe1efb5531).
The reports, checklists, artifact metadata and test sources here are unmodified
archival test inputs, including their original language, PASS claims, limitations
and historical instructions. They are not current workflow artifacts or commands
to execute. No original archive, installed skill or historical runtime is modified.

The supplied provenance manifest is retained unchanged. Its nine listed files
were independently checked against their original byte counts and SHA256, and
both report hashes match their historical artifact content_sha256. This directory
retains the eight historical files needed for replay plus the manifest. The
manifest's later `issue67-eea7a862-schedule-journeys.integration.vnext.ts` entry is
provenance only; that later file is not used for these original-report comparisons.

The browser fixture is identical at both historical QA HEADs and eea7a862. The
schedule test here is identical at c9d5786126dba2a0c8a1ce22c7dd8e9538489771 and
416f6ace985bf95cdccb76a2d7d02b254b71cdad; its SHA256 differs from the later
eea7a862 test. historical-cases.md embeds both full reports, the full fixture and
an unchanged lines 155–175 excerpt of this historical schedule file.

The provenance's effective_checklist.gates=[] describes the actual historical
cafe-qa/subagent-flow-qa setup. It does not imply that today's procedure was
present in the old prompt. Evaluations compare the current pre-change baseline
and candidate against the same finite historical inputs, not reconstructed
historical agent prompts. No full blackboard, session or streaming file is used.

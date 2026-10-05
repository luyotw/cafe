# Defect evidence

## Request boundary
Original source/request, triggering conditions, expected behavior authority,
observed behavior, confirmed scope and explicit exclusions.

## Diagnosis and regression proof
Supported cause, facts versus uncertainty; reproduction environment/dependencies;
unfixed revision; exact test path/name and source/patch/hash; replay command,
actual RED exit status and bounded defect-specific failure evidence. Explain
why the failure is the defect rather than setup or unrelated behavior.

## Replayable regression
Complete runnable source or durable patch/reference and prerequisites, sufficient
to install the same assertion before production changes.

## Repair and verification
For repair/review: diagnosis reference, repaired HEAD, unchanged test identity,
GREEN command and actual status, focused existing checks and results, relevant
code/test scope and covering commits. For diagnosis: proposed minimal boundary
and pending repair/independent assessment, without claiming GREEN.

## Todo List
For outgoing actionable corrections, use the selected route's canonical source
and ID prefix with Work, Closure and Evidence fields. With no corrective work
write exactly `No actionable work.` under this heading.

## Todo Progress
When causal work is supplied, retain item ID, status, source fingerprint,
Files, Commit, remaining work and next action. Completed evidence follows the
Files/Commit contract and requires a clean worktree.

## Assessment and remaining work
For review: independently assess cause, expected behavior, RED/GREEN relevance,
repair correctness and scope. Findings use the declared route's canonical Todo
source/prefix with Work, Closure and Evidence. Record interrupted/inconclusive
work, missing decisions/access, invalidated evidence and current next action.

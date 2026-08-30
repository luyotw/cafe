## Exit Audit

[ ] If the current result is a provisional pass, re-check the cumulative `merge-base({base_branch}, HEAD)..HEAD` change map against every fixed risk trigger and add any missed obligation; if blockers already remain, record why the exit audit was skipped instead of manufacturing pass evidence
[ ] Before routing a pass, freeze the current HEAD and require every triggered obligation to be `closed_fresh` at that HEAD; evidence already produced earlier in this iteration at the same HEAD remains fresh and need not be rerun, but `closed_reused` can never pass the exit audit

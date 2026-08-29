## Review Preflight

[ ] Read {agent_file} and every supplied requirement, plan, implementation artifact, and feedback item; establish the bounded scope for this review iteration
{spec_read_instruction}{plan_read_instruction}{feedback_instruction}[ ] Inspect `git log {base_branch}..HEAD` and the worktree once: no new commit or any uncommitted work means development is incomplete; sensitive data or an unwanted committed file is a critical finding
[ ] Compare branch commit messages with recent `{base_branch}` history in one pass; when style differs, report the affected SHAs, expected language/body style, and complete non-interactive repair commands

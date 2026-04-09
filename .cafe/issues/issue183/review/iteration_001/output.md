## Todo List

### Code Quality
- [x] `src/cafe/skills/importer.py:79`-`src/cafe/skills/importer.py:123`: import flow only checks for `SKILL.md` and then copies the folder as `imported`, so a skill whose frontmatter name does not match the folder is reported as successfully imported even though `SkillLoader` immediately flags it as invalid at `src/cafe/skills/loader.py:87`. This violates the spec/plan requirement that invalid skill folders be skipped or failed with clear feedback during import.
- [x] `src/cafe/agents/cli/claude.py:20`, `src/cafe/skills/workspace.py:9`, `src/cafe/data/playbooks/default.yaml:5`: automatic project-skill exposure is implemented only for Claude, but the default main workflow uses Gemini for `pm` and `reviewer`. Imported project skills therefore are not automatically available across the project's main agent flows, which misses the acceptance criterion and the plan's Task 3 / DoD.

### Testing
- [x] `tests/unit/test_cli_catalog_commands.py:124`, `tests/unit/test_skill_loader.py:86`, `tests/unit/test_claude_cli.py:208`: the new coverage only proves happy-path import, overwrite handling, and Claude-specific exposure. There is no test for rejecting invalid skill metadata during import, and no test that non-Claude workflow CLIs can see imported project skills, so the two regressions above shipped without protection.

### Delivery
- [x] `./.cafe/issues/issue183/develop/iteration_001/output.md:9`: the develop handoff claims verification is complete, but the working tree still contains uncommitted tracked changes in multiple files (`git status --short`), so the implementation is not in a clean, reviewable state.

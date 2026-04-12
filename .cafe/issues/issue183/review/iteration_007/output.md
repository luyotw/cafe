## Todo List

### Code Quality
- [x] `src/cafe/ui/cli.py:4861`, `src/cafe/skills/remover.py:63`: `cafe skill rm` trusts the CLI-provided skill names as raw path fragments. Inputs like `../other`, `../../src`, or an absolute path are joined directly into the removal target and then deleted, so a user can remove content outside `.cafe/skills`. This is a destructive path-traversal bug in the new command, and it affects the non-interactive path that the blackboard handoff explicitly requested.

### Testing
- [x] `tests/unit/test_cli_catalog_commands.py:242`: the new coverage only exercises normal skill names. There is no regression test proving `skill rm` rejects `..` segments or absolute paths, so the path-escape bug above ships completely unguarded.

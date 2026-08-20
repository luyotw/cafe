# Script execution boundaries

Every workflow-managed script launch is classified before path resolution. Unknown launchers fail closed. A path, skill override, symlink, or approval never changes the assigned class.

| Launch path | Class | Trust source | Environment / credentials | Network | CWD and writes | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Skill and phase script hooks | sandbox | workflow declaration | constructed allowlist; no credentials | denied unless sandbox policy declares it | workflow cwd; declared roots | execution receipt |
| Permission retry | sandbox | agent permission boundary | constructed allowlist; no credentials | sandbox policy only | original sandbox roots | execution receipt |
| Long-running operation child | sandbox | workflow operation request | constructed allowlist; no credentials | sandbox policy only | issue workspace; declared roots | operation and execution receipts |
| Prepare/close lifecycle script | lifecycle | user-owned canonical declaration | constructed allowlist; no credentials | denied | declared cwd and local write roots | declaration identity and execution receipt |
| Registered adapter | capability | immutable package registry | manifest grants only | manifest destinations only | manifest cwd/write effects | policy decision and capability receipt |
| Bundled GitHub helpers | capability | immutable package registry | manifest grants only | GitHub only | declared issue artifacts | policy decision and capability receipt |

Fixed internal executables used to implement CAFE itself (for example Git queries and the Python worker bootstrap) are not workflow script launchers. They must accept typed internal inputs and cannot be used as an arbitrary-script bridge.

## Migrating custom hooks

Existing custom hooks are attempted as sandbox execution and are never grandfathered into host authority. Inspect what the hook actually needs:

1. Keep local, workspace-scoped behavior as a sandbox hook. Verify its receipt reports `sandbox` and `workflow` trust.
2. For a user-owned `prepare` or `close` script with narrow local writes, run `cafe trust lifecycle` and verify the canonical digest, stage, cwd, and write roots. Revoke and recreate the declaration whenever identity or scope changes.
3. For credentials, external network access, or privileged mutation, define a package-owned registered capability with exact effects and policy. Do not point a capability at a project or global skill script.

An unavailable sandbox backend, changed lifecycle identity, unknown launcher, ambient credential dependency, or undeclared network/write request is a safe denial. Correct the boundary and retry; CAFE does not automatically promote or fall back to host execution.

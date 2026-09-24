# Versioning policy

CAFE uses Semantic Versioning for releases. Release numbers describe the
largest externally meaningful change shipped in that release; they do not name
roadmap phases or reserve a number for a product theme.

## Choosing the next version

Apply the highest-impact change present in the release:

| Change | Version increment |
| --- | --- |
| Backward-compatible user-visible capability, CLI surface, workflow behavior, or public contract | Minor |
| Bug fix, documentation, tests, packaging, or internal refactor with no new public capability | Patch |
| Backward-incompatible public-contract change after 1.0 | Major |

Before 1.0, a necessary backward-incompatible public-contract change increments
the minor version and requires explicit migration guidance. A release containing
both features and fixes increments the minor version. Patch releases must not
quietly introduce a new public capability.

The public contract includes documented CLI commands and options, playbook and
skill declaration formats, supported configuration, HumanTask and capability
payloads, and persisted workflow state that an installed release promises to
read. Package-private Python modules and undocumented implementation details are
not public API.

## Roadmap and milestones

Roadmap stages describe product direction independently of release numbers. A
stage may span several releases, and a release may advance more than one stage.
Historical plans may retain their original version-based names, but those names
do not reserve future release numbers.

GitHub release milestones use exact `vX.Y.Z` titles only after their scope is
selected. Feature work makes the milestone a minor release; fix-only work makes
it a patch release. Product themes whose release scope is not yet selected use
a descriptive title ending in `(version TBD)`.

Issues and pull requests may use `semver:minor` or `semver:patch` to record their
release impact. The release milestone always takes the highest impact among its
included changes.

## Release checklist

Before changing package metadata:

1. Compare the proposed release with the latest published tag.
2. Identify the highest-impact shipped change using this policy.
3. Confirm that the GitHub milestone and release branch use the resulting
   version.
4. Update `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, the latest-release link,
   and the matching release notes together.
5. Document migrations for any changed public contract.
6. Run `./scripts/release-check.sh` from the final release commit.

## Stability at 1.0

Version 1.0 means CAFE is ready to preserve its documented public contracts
across the 1.x series. New backward-compatible capabilities increment minor;
fix-only releases increment patch; incompatible public-contract changes require
a new major version. Reaching 1.0 does not require completing every roadmap
stage.

# CODEX — 0.1.1 implementation instructions

Repository: THEFLDR/HA-IntegrationManager
Existing source branch: CODEX/0.1.1
Target branch: main
PR title: PR - CODEX/0.1.1

## Objective and version contract

Prepare Integration Manager 0.1.1 and standardize project release versions as
X.Y.Z, with three decimal integer components and no leading prefix, suffix,
whitespace, or leading zeroes except the component zero itself. The first GitHub
Release Candidate will be named and tagged 0.1.1. Represent Release Candidate
status with GitHub's prerelease metadata, keeping the version itself unchanged.

1. Create this instruction file in the existing branch before implementation.
   A previous GitHub Contents API attempt was blocked by a Code Scanning Ruleset.
   Do not modify or bypass Rulesets; report any blocked commit or push.
2. Update the integration manifest and internal version constants from 0.1.0
   to 0.1.1.
3. Require the same exact X.Y.Z version in GitHub tags, releases, release
   metadata, packaged manifests, and Home Assistant. Reject malformed release
   tags at publication, promotion, discovery, and packaging boundaries.
4. Update publication/promotion scripts, GitHub Actions inputs and descriptions,
   release discovery, comparisons, packaging, and self-update where necessary.
5. Update existing tests and add focused version-handling regression tests.

## Preserve behavior

- Keep immutable GitHub Releases. Publish new versions initially as prereleases.
- After Sandbox testing, promote the exact same release, tag, and artifacts to
  Stable. Never rebuild, retag, or replace assets during promotion.
- Preserve installation, removal, rollback, cleanup, dashboard, release-channel,
  and native self-update behavior.
- Resolve and test against the latest available stable Home Assistant Core on
  every code PR and main-branch validation. Exclude beta, release candidate, and
  development Core versions; print the exact installed version in logs and the
  test summary. Fail visibly on incompatibility without an older-version fallback.
- Retain the existing minimum supported HA version as a separate requirement.
- Keep CI efficient, including its documentation-only fast path; avoid duplicate
  jobs, matrices, and test runs.

## Restrictions

- Do not change GitHub Rulesets or repository settings, or bypass blocked writes.
- Do not modify README, Wiki, changelog, historical instruction files, or unrelated
  documentation. This new instruction file is the documentation exception.
- Do not introduce unrelated features or refactoring.
- Preserve domain integrationmanager, display name Integration Manager, and the
  existing icon unchanged.
- Preserve legitimate THEFLDR attribution. Verify the actual author/committer and
  the account's verified noreply email before committing; do not invent identity
  information or rewrite existing commits.
- Do not create Git tags, publish releases, merge automatically, or request an
  additional Copilot review.

## Validation and delivery

Run the complete relevant Python suite, frontend tests, Ruff lint and format
checks, packaging validation, and dependency audits. Review the complete branch
diff against main before submission. Check the repository for unintended legacy
release-tag references; historical documentation and rejection tests may retain
explicit examples of the old format.

Create exactly one PR from CODEX/0.1.1 to main titled PR - CODEX/0.1.1. Summarize
the changes, test results, exact tested Home Assistant Core version, and any
remaining blockers, including Ruleset failures. Creating the PR does not authorize
release publication, tag creation, or merging.

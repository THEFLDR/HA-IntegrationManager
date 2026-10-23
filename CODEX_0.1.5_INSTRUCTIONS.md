# CODEX 0.1.5 — Development instructions

Repository: THEFLDR/HA-IntegrationManager
Working branch: CODEX/0.1.5
Target version: 0.1.5
Pull request title: PR - CODEX/0.1.5
Base branch: main

## Read before editing

Inspect the current branch and existing architecture, including:
- .github/workflows/ci.yml, release.yml and promotion.yml
- scripts/release.py, scripts/package.py, version checks and relevant tests
- custom_components/integrationmanager/frontend/panel.js
- custom_components/integrationmanager/__init__.py and panel registration
- approved-icon.png and custom_components/integrationmanager/frontend/icon.png
- custom_components/integrationmanager/frontend/approved-icon.svg

Read and honor existing repository guidance. Inspect GitHub's current branch/PR state again before submitting the PR.

## A. Automatic Release Candidate publication

After a successful **push-to-main Validation** run, automatically publish a Release Candidate for the new version, using the **exact validated main commit SHA** and the existing publication and artifact-verification machinery.

1. Extend the existing Publication workflow rather than creating a parallel release workflow. A suitable approach is a carefully guarded workflow_run trigger for the completed Validation workflow, while retaining workflow_dispatch as an owner-only manual recovery path.
2. On automatic execution, require ALL of: upstream workflow name/identity Validation; event push (not pull_request); head branch main; conclusion success; upstream repository is the owner repository; SHA is the currently expected main HEAD; version in manifest.json and const.py agrees and is a valid bare X.Y.Z tag. Verify the upstream workflow run and SHA, not the workflow_run context's default GITHUB_SHA.
3. Detect whether the version is new. For an already published version with the expected matching tag/release, exit successfully and do nothing. No duplicate releases, no rebuild, no replacement of immutable assets. Fail clearly on mismatched/conflicting tags, draft/partial releases or unexpected state; do not silently overwrite.
4. For a genuinely new version, use the existing scripts/package.py and scripts/release.py publication path, retaining the immutable artifact, checksum/digest verification, tag target and exact successful main Validation requirements. Keep GitHub Actions permissions minimal; use concurrency to serialize publication and avoid duplicate races.
5. The publication must remain a **prerelease / Release Candidate**. Do not automatically promote to Stable. Do not automatically install on Staging or Prod. The Sandbox's existing update mechanism may detect the new RC.
6. Do not run another full test suite during Publication: Validation has already passed. Keep actions minutes and duplicate checks low.
7. Preserve manual workflow_dispatch publication with tag and SHA as a safe recovery option; adapt the workflow to handle both event types. Do not require manually entering tag/SHA for the automatic path.
8. Test positive, negative, repeat, concurrency/idempotency and event-origin cases (including PR success, failed main Validation, doc-only main push, duplicate tag, manifest/const mismatch, stale main SHA and preexisting release). Prefer fast local/unit/workflow static tests; avoid triggering real releases during PR validation.
9. Release notes must not claim a manual restart is the only route now that Repairs supports a restart confirmation.

## B. Dashboard table layout

In the Installed custom integrations table, use this exact column order:
Integration | Installed version | Release Channel | Release Channel Mode | Health | Update | Details / actions

- Move Release Channel Mode out of the small, compressed line under Release Channel and into its own table cell.
- Render each existing native entity state as an individually clickable control opening HA's native more-info dialog.
- When no mode entity exists, show an unobtrusive em dash; do not fabricate an entity.
- Preserve existing integration/service name links, state refresh, expansion controls, keyboard accessibility, mobile horizontal scrolling, and all actions.
- Keep the Global Settings and Manager Settings layout otherwise unchanged. Do not reintroduce redundant entity listings.
- Add/adjust frontend tests for the seven-column table, click behavior and absent mode.

## C. Approved icon — exact artwork, no redesign

The user-approved visual is already present in the repository at:
- approved-icon.png
- custom_components/integrationmanager/brand/icon.png
- custom_components/integrationmanager/frontend/icon.png

These are byte-identical and must remain the visual source of truth.

An SVG **container** of that exact approved PNG has been committed on this branch:
custom_components/integrationmanager/frontend/approved-icon.svg

IMPORTANT: This SVG embeds the original raster PNG for **pixel-identical rendering**; it is not a hand-traced, resolution-independent vector illustration. Do not describe it as a true vector conversion. Do not create any replacement artwork, house/gear/update-arrows concept, recoloring, simplification, new composition or altered gradients.

Goal: display this exact approved icon as the Integration Manager dashboard/sidebar icon **if Home Assistant supports it through a documented/supported custom icon registration approach** for custom panels. Research the supported mechanism in HA Core/frontend versions relevant to this repository, and implement the smallest robust integration.

- Prefer using the already-shipped approved PNG directly where HA supports it; use the faithful SVG asset only if technically appropriate and permitted by frontend security/CSP.
- Do not assume that panel_custom.sidebar_icon accepts arbitrary image URLs. Do not use private HA internals, unsupported monkey-patches or brittle CSS sidebar DOM modifications.
- If HA only accepts icon names but supports a stable custom iconset provider, implement/register a properly namespaced icon and reference it; ensure loading order, cache-busting, and graceful fallback. If a supported custom sidebar icon is genuinely unavailable, retain a standard fallback and report the limitation explicitly in the PR rather than claiming success.
- Keep existing brand artwork, transparency, aspect ratio and visual appearance unchanged. Avoid adding large duplicate assets unnecessarily.
- Verify SVG rendering matches approved-icon.png exactly; ensure any image URL served by the integration is authenticated/secure as appropriate for the existing frontend static route. Test panel registration and icon loading/fallback.

## D. Scope and release hygiene

- Bump runtime version and manifest to 0.1.5 and keep packaging/version checks consistent.
- Preserve all 0.1.4 features: service identity, migrated canonical entity IDs, health, backup, progress, repairs, last update check, rollback, security, promotion workflow.
- Update only files needed for this version and its tests. Do not edit README, Wiki, changelog or unrelated documentation; do not alter GitHub Rulesets/branch protection.
- Keep all code, UI strings, tests and repository documentation in English.
- Run relevant focused tests during development and one complete final validation before PR submission; minimize redundant GitHub Actions minutes.
- Check the latest repository state before opening the PR.
- Open exactly one PR: **PR - CODEX/0.1.5** from **CODEX/0.1.5** into **main**. Do not merge, tag, manually publish a release, or promote.
- In the PR description include test results, auto-publication event guards, release idempotency design, and whether a custom HA sidebar icon is truly supported (including any fallback/limitations).

# CODEX 0.1.6 — Development instructions

Repository: THEFLDR/HA-IntegrationManager
Working branch: CODEX/0.1.6 (already created from main)
Target version: 0.1.6
PR title: PR - CODEX/0.1.6
Base: main

## Context and constraints

This is a private custom integration for the owner's HA environments. Home Assistant Core 2026.10.2 is the current tested version, and the manifest declares minimum 2025.12.0. Read current repository guidance and inspect the latest main/working branch before edits. Keep changes pragmatic and focused. Codex makes all code changes. No branch protection/Rulesets edits, unrelated documentation changes, README/Wiki/changelog changes, or automatic merge/release/promotion.

Preserve 0.1.5 auto RC publication, update channels, native Update entity, optional backup, Repairs, health checks, service identity, canonical entity IDs, dashboard and release immutability.

## A. Persistent, honest self-update status UX (highest priority)

The user reports that clicking the native Home Assistant Update control shows a progress bar for about one second, then it disappears. Only the restart notification suggests that installation succeeded.

Verified existing cause:
- custom_components/integrationmanager/manager.py sets update_in_progress=True during self_update and resets it to False immediately after staging files.
- It already emits update_phase values such as checking_release, downloading, validating_archive, backing_up, installing_files, restart_required and failed, but phase is not a durable, clear user-facing status flow.
- custom_components/integrationmanager/update.py advertises native PROGRESS, with update_percentage=None, and exposes update_phase/restart_required/last_error as attributes.
- The native HA more-info progress indicator is expected to disappear when in_progress becomes False; do NOT misrepresent restart waiting as active installation merely to keep the native bar visible.

Implement a durable, immediately visible and visually polished **self-update status section in the Integration Manager dashboard** (not an extra sensor unless strictly necessary), linked to the native Update entity and existing runtime events. Suggested clear stages:
  Checking release -> Downloading -> Validating -> Backing up (only if requested) -> Installing files -> Files installed / Restart required -> Completed after HA restart.
  Also show Failed with the actual error, and Cancelled where appropriate.

Acceptance criteria:
1. On native Update click (including Settings / native more-info), the dashboard status updates automatically from the HA entity state/attributes without requiring the user to click a custom dashboard update button or manually refresh. Existing dashboard live-state logic must not replace an in-progress status with stale overview data.
2. Show a readable progress/step indicator or status bar, using native HA colors/theme tokens and responsive styles, with clear current phase and finished phases. Show indeterminate progress while actual work has no reliable denominator; do not invent percentages or stage-weighted pseudo-percentages.
3. After files are successfully staged, **persistently display an unmistakable success state: 'Files installed — restart required'**, the target version if known, and a link/action to Home Assistant Repairs or an existing supported restart-confirmation route. The update status must NOT silently disappear when update_in_progress becomes False. Do not claim the new version is running before restart.
4. After a successful restart with the new version, show a truthful 'Update completed — running X.Y.Z' result in the dashboard, ideally persisted until the next update operation or explicitly dismissed. Distinguish new running version from pending staged version. Detect unexpected/mismatched version after restart and show a warning rather than false success.
5. On failed backup, failed download, invalid archive, failed file change, or cancellation, keep a visible failure/cancellation result with actionable message; do not claim files installed when they were not. Preserve any existing recovery/rollback/health logic. A partial file change requiring restart must be reported as such even if the update ultimately raised an exception.
6. Persist the minimal update status metadata across dashboard reload and HA restart using the existing Store; avoid new polling loops, timers, extra sensors or a generic state-machine framework. Prefer deriving live state from the existing native Update entity attributes, with minimal new fields such as pending target version and last outcome.
7. Keep the native UpdateEntity feature and correct in_progress semantics; do not fake progress to force HA's native bar to stay. The native entity may expose additional useful, truthful attributes (target version, result, restart_required) so Settings' more-info dialog conveys the outcome too. Update notifications and Repairs must remain coherent.
8. Tests: phase transitions, native Update entry point, dashboard live updates, persistent restart-required state, successful restart/version confirmation, failed update and backup, partial-file-change failure, cancellation, restart mismatch, fresh install and regression. Verify light/dark, desktop/mobile, accessible labels and keyboard navigation.

## B. Approved icon: genuinely vector-derived sidebar icon, with an honest compatibility boundary

The user insists the artwork be derived **one-to-one from the approved image**, not redesigned. The approved original is byte-identical in:
- approved-icon.png
- custom_components/integrationmanager/brand/icon.png
- custom_components/integrationmanager/frontend/icon.png

The 0.1.5 frontend/approved-icon.svg embeds that PNG; it is NOT genuine vector art. Do not use the embedded PNG container as proof of vectorization. Never create a different house/gear/arrows logo or modify the approved branding.

Verified Home Assistant developer API: https://developers.home-assistant.io/blog/2020/05/09/custom-iconsets/ — `window.customIconsets[namespace]` async loader returns `{path: string, viewBox?: string}`. HA's sidebar renders icon identifiers via ha-icon, whose custom iconsets expect SVG path geometry, typically monochrome with currentColor. HA Core frontend exposes `add_extra_js_url(hass, url)` / `remove_extra_js_url`; panel_custom.async_register_panel accepts sidebar_icon string. Research supported HA 2025.12.0 and 2026.10.2 frontend behavior and load ordering before coding.

Goals:
1. Investigate and produce a **genuine SVG path-based vector tracing of the approved artwork** appropriate to a small sidebar icon. Preserve the recognizable geometry and composition: house, circuit branches, surrounding ring/nodes, layered stack. Derive paths from the original image, not from new generative artwork. Optimize at actual HA sidebar size (~24px) and in light/dark themes.
2. Be transparent about the fundamental limitation: the native iconset API returns a single SVG path, so it CANNOT render the approved PNG's full multicolor gradients and glossy shading pixel-identically. Do not claim exact color reproduction or pixel identity. The existing full-color approved PNG remains unchanged everywhere it is already used.
3. If the traced monochrome icon is visually faithful/readable, register it through the documented customIconsets mechanism and use a namespaced icon identifier as the sidebar_icon. Use supported frontend extra-module registration, lifecycle cleanup, cache-busting and a fallback MDI icon if the custom provider is unavailable. Avoid brittle DOM/CSS hacks, private frontend internals, unrelated HACS dependencies and external hosted assets.
4. If it is not possible to deliver a legible, recognizable path icon at sidebar size through supported APIs, do NOT silently substitute a redesign. Retain mdi:puzzle, provide the vector prototype as a clearly labeled **unapproved candidate** (not shipped as active sidebar artwork), and explain the limitation and visual trade-off in the PR. User approval is required before accepting a materially simplified silhouette as final.
5. Add a comparison preview/screenshot in PR or otherwise provide a practical visual preview of the traced icon next to the approved PNG at real sidebar size. Do not assert visual approval on the user's behalf.
6. Test iconset registration, panel icon identifier, frontend initialization/reload, supported min HA version, theme colors and graceful fallback. No extra background services or large image payloads in iconset JS.

## C. Release hygiene

- Bump const.py, manifest.json and any required version assertions to 0.1.6.
- Preserve automatic RC publication on successful main Validation. No release during PR.
- Update only necessary code/assets/tests and this instruction file if clarifications are needed.
- Run focused tests during development, then one complete final validation; minimize redundant GitHub Actions minutes.
- Before PR, fetch current main and working branch to ensure no unintended overwrites; review full diff.
- Create exactly one PR: `PR - CODEX/0.1.6` from `CODEX/0.1.6` into `main`.
- Do not merge, tag, release, promote, or modify Rulesets.
- PR description must distinguish tested vs untested behavior, show the icon preview/limitations, explain persistent update status semantics and report test results.

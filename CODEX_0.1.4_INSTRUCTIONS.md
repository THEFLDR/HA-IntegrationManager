# Codex Assignment — Integration Manager 0.1.4

## Objective
Deliver a focused UX, update-lifecycle, and health-monitoring release for the private Home Assistant Integration Manager. Use the existing code and native Home Assistant mechanisms; avoid new subsystems or gratuitous entities. Preserve all working 0.1.3 behavior.

## 1. Entity IDs and migration
- Target canonical entity IDs for the four existing entities:
  - `binary_sensor.integrationmanager_health`
  - `update.integrationmanager_update`
  - `sensor.integrationmanager_release_channel`
  - `sensor.integrationmanager_release_channel_mode`
- Fix fresh-install ID generation despite the native service's display name "Integration Manager" (which currently produces `integration_manager_*`). Investigate HA's actual entity-registry naming behavior; do not assume `suggested_object_id` alone overrides the service name.
- Safely migrate only existing integration-owned, default-generated `integration_manager_*` entity IDs when the canonical ID is free. Preserve unique IDs, registry IDs, user customizations (including intentionally renamed IDs), disabled/hidden states, entity associations, and config entries. Never overwrite a conflicting entity; report/skip safely. Document in the PR that changed IDs may require updating user automations/dashboards.
- Idempotent upgrade/reload; no duplicate entities.

## 2. Dashboard simplification and navigation
- In Global Settings keep global release-channel selection. In the dedicated Integration Manager settings area keep only Release Channel Mode, conditional Override Channel, and dashboard-only Check for Updates. Remove redundant Manager entity links and their state summaries from the upper settings area; keep the settings concise.
- Make the integration name in each installed-integration table row clickable. For Integration Manager open its actual native HA Service detail page; for other integrations open a native HA device/service page only if a unique, valid destination exists. Use supported HA frontend navigation and registry information, not a hard-coded guessed route or synthetic detail view. For ambiguous/missing destinations, render a non-link name.
- Make each provided table state directly clickable to open the corresponding native HA entity more-info dialog: Effective Channel, Release Channel Mode (secondary value), Health, and Update. Preserve live human-readable states, unknown/unavailable, absent entities ("Not provided"), disabled/hidden behavior, and accessible keyboard interaction. No duplicate Manager controls in expanded details.
- Preserve compact responsive table, per-integration detail actions, installation/removal/cleanup, themes, mobile behavior and global/manager channel semantics.

## 3. Native update backup and progress
- Current `ManagerUpdate.async_install(version, backup)` ignores the `backup` argument. Implement **optional**, default-off backup via the supported HA Core backup interface for the tested versions; advertise `UpdateEntityFeature.BACKUP` only when it really works. Verify supported backup behavior in HA OS and gracefully handle unsupported installations. If the user requests backup, wait for confirmed successful completion before modifying files; fail clearly and make no file changes if backup fails. Do not claim backup succeeded when it did not.
- Use the native Update entity's `UpdateEntityFeature.PROGRESS`, `in_progress` and `update_percentage` to report truthful progress through download, archive validation, optional backup, file installation and completion. Percentages must be derived from measurable progress; use an indeterminate phase when no trustworthy percentage is available, never fabricate elapsed-time or step-weighted percentages. Avoid resetting progress prematurely; ensure errors/cancellation/rollback leave a coherent state. No separate Update Status entity.
- Keep existing release selection, channel gating, locks, rollback, six-hour release checks and self-update behavior. Keep backup optional and no new mandatory dependencies.

## 4. Native HA Repairs restart experience (HACS-style)
- Replace/augment the persistent restart notification with a native HA Repairs issue marked fixable. A "Fix issue" flow must ask for confirmation and then invoke the supported `homeassistant.restart` service; do not restart without explicit user action.
- Study HACS's current `custom_components/hacs/repairs.py` and issue creation as an implementation reference; verify HA Core APIs and translations against supported HA versions, particularly 2026.10.2 and the declared minimum. If feature compatibility differs, provide a safe fallback without breaking setup.
- Create an issue after successful file changes that require a restart, including self-update; handle removal/cleanup where appropriate. Avoid duplicate/stale issues; reconcile after restart and clear the issue only when the new runtime is actually active or the restart requirement is resolved. Maintain the existing pending-domain guard and accurate restart-required state.
- Do not add a Restart Required sensor. A plain persistent notification is not a substitute for the requested Fix issue workflow.

## 5. Last successful update check
- Add exactly one new diagnostic timestamp sensor, **Last Update Check**, displaying the timezone-aware date/time of the most recent successful Integration Manager release check. Do not overwrite it on failures; no extra network polling. Persist/recover meaningfully across HA restarts, or explicitly document and test a narrower supported semantic if HA APIs constrain this.
- Associate the sensor with the same native Service, preserve its registry identity, and expose it in the native service detail page. Include it in health/diagnostic reporting as appropriate without inventing another dashboard column or additional sensors.
- Update service registration/migration logic to recognize the new entity while keeping the original four untouched.

## 6. Expanded Health checks (existing binary sensor only)
- Keep the existing `binary_sensor.integrationmanager_health` and `BinarySensorDeviceClass.PROBLEM` (off=OK, on=Problem). Expand beyond the current `not coordinator.last_update_success` to check, using existing state/events without redundant polling:
  1. Release discovery: last successful/failed GitHub release retrieval and validation; distinguish transient connectivity warnings from persistent failures.
  2. Update subsystem: coordinator/update-entity availability and operation failures.
  3. Installation: most recent install outcome, including failure/rollback; an active operation is not itself a problem.
  4. Backup: most recent *requested* backup outcome; no-backup-selected is not a failure.
  5. Service registry: service exists, has the right entry type and config-entry ownership, and all existing four entities (plus the new timestamp sensor once registered) are associated, including disabled entities. Avoid false positives during initial setup/teardown.
  6. Configuration: valid, coherent global channel, Manager mode and override/effective-channel selection.
  7. Filesystem: required integration files/manifest exist and are readable, using bounded/off-thread checks as appropriate, not costly repeated scans.
- Provide clear attributes such as `failed_checks`, `warnings`, `last_check`, and `last_error`, with consistent semantics and meaningful recovery. Never label unverified checks as passed; represent startup/unverified states explicitly. If an accurate health state is not determinable, prefer `unavailable` rather than a misleading OK. Avoid flip-flopping during routine release checks and avoid making transient GitHub outages immediately critical. Do not mark restart-required as an error.
- Keep this lightweight and event-driven/coordinator-driven. No new Health entities, separate health polling loop, or general monitoring framework. Ensure automatic recovery once a problem resolves.

## 7. Service metadata cleanup
- Remove manufacturer `THEFLDR` from the native Service info card, including already-registered services (an omitted field alone may not clear persisted registry metadata). Preserve Integration Manager name, icon, model if appropriate, service entry type, software version, ownership, and author/codeowner/repository attribution elsewhere. Confirm the correct HA registry update API rather than assuming a field is automatically cleared.

## 8. Version, tests, delivery
- Bump to **0.1.4** throughout applicable runtime, manifest and tests; keep bare `X.Y.Z` release tags.
- Add focused backend and real-HA tests for fresh and migrated IDs, collisions/custom IDs, service links/metadata, repairs issue/fix flow, successful/failed backup, honest progress reporting, restart lifecycle, last successful check, expanded health checks and recovery. Add frontend tests for clickable table cells/integration names, settings cleanup, accessible interactions, and no regressions.
- Test against dynamically resolved latest stable HA Core plus compatibility with declared minimum where feasible. Run focused tests, full Python/frontend tests, Ruff, actionlint, packaging and applicable audits. Minimize duplicate GitHub Actions work; one final full validation.
- Review the complete diff, identify any limitations honestly in the PR description, and check remote repository changes immediately before PR submission.
- Work **only** on the existing branch `CODEX/0.1.4` and read this file from that branch. Open **one** PR into `main`, titled **PR - CODEX/0.1.4**. Do not merge, tag, release, create another branch or request extra Copilot review.
- Do not modify GitHub Rulesets, repo settings, README, Wiki, changelog, unrelated documentation, workflow architecture or release scripts. Preserve the domain `integrationmanager`, Integration Manager display name, existing icon/artwork, and THEFLDR codeowner attribution.

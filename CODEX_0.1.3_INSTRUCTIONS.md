# Codex Assignment — Integration Manager 0.1.3

## Objective
Deliver a focused Home Assistant UX release: a clickable native service detail page for Integration Manager, plus a compact, scalable Integration Manager dashboard. Preserve all v0.1.2 functionality.

## 1. Native service detail page
- Inspect the currently tested Home Assistant Core implementation and Google Translate TTS as a reference for how a service row opens a native Service info detail page. Confirm the actual supported registry/API mechanism before implementing it; do not assume config-entry subentries are required.
- Register Integration Manager as a **service**, not a physical device. If the supported implementation uses a service-typed entry in HA's device registry, set the proper service entry type, name, manufacturer and ownership. Avoid generic/physical device entries.
- Associate existing Health, Update, Release Channel, and Release Channel Mode entities with the service so clicking its row opens the native detail page instead of merely filtering the entity list.
- Preserve existing config entry, entity unique IDs, user customizations, disabled states, entity IDs and integration identity. Make upgrades from 0.1.2 idempotent; prevent duplicates, orphaned registry entries or unintended deletion of user data. Account for the v0.1.1 legacy device migration.
- If current supported HA APIs cannot provide this UI behavior safely, report the limitation rather than building a fragile imitation.

## 2. Compact dashboard
- Replace the tall per-integration card list with a concise, responsive, accessible list/table displaying integration name, installed version, effective release channel/mode when available, health and update status. Handle missing entities without invented statuses.
- Add expandable per-integration details (or an equivalently compact native pattern), keeping native entity more-info actions, installation, removal, cleanup and other existing controls accessible.
- Separate compact Global Settings and Integration Manager Settings sections. Preserve global channel and Manager Managed/Default/Override semantics, effective channel, optional override, and dashboard-only Check for Updates. Preserve six-hour automatic refresh and native self-update.
- Avoid duplicating Manager's own entity controls in the general installed list when they are already shown in its dedicated section; still show the integration in the list and allow details.
- Prefer supported Home Assistant frontend components and design tokens where appropriate, but verify compatibility with the custom panel and the supported HA versions. Do not depend on undocumented internals when a stable/simple alternative exists. Follow HA light/dark themes and mobile widths.
- Keep live entity states human-readable: Health OK/Problem, Update Up-to-date/Update available, channel Stable/Release Candidate, mode Managed/Default/Override. Preserve unknown/unavailable states.

## 3. Version, tests, delivery
- Bump the integration version to 0.1.3, retaining bare X.Y.Z release tags.
- Add focused backend/real-HA tests for service registry ownership, detail-page prerequisites, idempotent upgrades, and entity associations; add frontend tests for compact view, expand/collapse, state updates, settings, and actions.
- Test against dynamically resolved latest stable Home Assistant Core. Run Python/frontend tests, Ruff, actionlint, packaging and applicable audits, minimizing duplicate CI work.
- Review complete diff and document any UI limitations in the PR description.
- Work only on CODEX/0.1.3; open one PR into main titled PR - CODEX/0.1.3. Do not merge, tag, release, or request extra Copilot review.
- Do not change GitHub Rulesets, repo settings, README, Wiki, changelog or unrelated docs. Keep changes focused, preserve existing icon, domain integrationmanager, display name Integration Manager and THEFLDR attribution.

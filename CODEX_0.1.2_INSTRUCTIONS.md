# Codex Assignment — Integration Manager 0.1.2

Implement the narrowly scoped 0.1.2 HA UX bugfix release on branch CODEX/0.1.2. Full instructions are in the accompanying Codex prompt.

1. Remove artificial device registration and clean up the existing 0.1.1 device safely while preserving entities.
2. Fix native integration identity/name and icon (investigate HA branding behavior; do not assume frontend icon is sufficient).
3. Add dashboard-only Check for Updates using the existing coordinator; retain six-hour polling.
4. Render binary health and update states as OK/Problem and Up-to-date/Update available in dashboard, including live updates.
5. Keep Health as a regular entity, Release Channel and Release Channel Mode as diagnostic sensors. Remove source/mode attributes from Release Channel.
6. Implement or accurately derive Managed/Default/Override from actual configuration semantics; inspect repository before deciding behavior and report any unresolved semantics rather than inventing them.
7. Keep domain integrationmanager; do not forcibly rename existing entity IDs. Owner will regenerate IDs in HA.
8. Bump version to 0.1.2; preserve all existing release/install/self-update functionality. Add migration and regression tests.
9. No unrelated refactoring, Ruleset edits, README/Wiki/changelog edits, tags, release, or merge. Run full relevant tests against latest stable HA Core, inspect diff, create one PR titled PR - CODEX/0.1.2.

# CODEX — v0.1.0 implementation instructions

Repository: THEFLDR/HA-IntegrationManager
Branch: CODEX/0.1.0
PR title: PR - CODEX/0.1.0

## Scope and non-negotiable rules
Implement only working integration code, targeted automated tests, release packaging, and required GitHub Actions. This assignment file is the sole documentation exception. Do not edit README, GitHub Wiki, changelogs, docs, or other documentation: documentation will be a separate release. Do not copy the previous overengineered project. Never change repository Rulesets, protection or settings. No GitHub App, authentication, PEM, tokens, webhook, HA-to-GitHub writes, status ledger, instance registration, or environment gating. Public GitHub read-only access only.

**Commit identity:** Owner requires THEFLDR as the sole human contributor, not Stefan Felder, Codex, or User1. Before any commits verify actual git author/committer identity and the account's verified GitHub noreply email; do not invent email addresses, spoof identity, rewrite others' commits, or proceed if correct attribution cannot be established. Report a blocker for owner action. Confirm attribution on resulting commits. Do not request Copilot reviews.

## Product
Build a small Home Assistant custom integration named Integration Manager (domain integration_manager), manually installed first on HA OS, subsequently self-updated through its own native HA Update entity.

Exactly three principal functions:
1. Browse, install, and uninstall custom integrations from an owner-controlled central public repository catalog.
2. Display a dynamic overview/dashboard of all installed custom integrations and their existing native Health, Update, and Release Channel entities, including integrations not listed in the catalog. Read HA state and registries directly; no central health tests, cloned entities, state ledger, or polling of other integrations.
3. Set the instance-wide global release channel: **Stable** or **Release Candidate**, default Stable.

### Catalog
Use a minimal public JSON catalog in this repository (e.g. repositories.json), dynamically fetched with timeout/cache/error handling. It must allow new owner-owned public repositories without updating Manager code. Validate schema, repository owner THEFLDR, integration domain and paths; prohibit arbitrary third-party URLs. Never offer Manager as an installable duplicate.

### Installation
Fetch an explicitly tagged GitHub Release and compatible packaged integration artifact. Stable selects regular releases; Release Candidate allows regular and GitHub prereleases, only recognized RC releases. Validate archive layout, paths, symlinks, sizes and destination; stage before replacing. Handle partial failures without corrupting existing files. Clearly request HA restart where necessary; do not automatically restart. Show installed status and version when available. Other integrations retain their own native Update entities and installation logic; Manager does not take over their updates.

### Uninstallation
Provide (1) remove integration files while preserving configuration/data and (2) separately confirmed cleanup of unambiguously integration-owned data through supported HA APIs, explaining limitations. Never delete shared or uncertain files or claim universal zero residue. Do not offer Manager self-uninstallation.

### Dashboard
Prefer native Home Assistant UI; dynamically discover installed custom integrations and associate their existing native Health, Update, Release Channel entities via supported registries, not hard-coded entity IDs. No health checks or state synchronization. Keep entities enabled and visible. If native dashboard limitations require a small custom panel, implement only what is necessary and document tradeoffs in PR description, not repo docs.

### Release channel contract for future standalone integrations
User-visible labels and states: Managed, Stable, Release Candidate. Internal constants may use managed, stable, release_candidate. Each integration independently owns a native Health entity, native Update entity, and Release Channel sensor. Its persisted explicit override is null | stable | release_candidate. Null means automatic: if Manager loaded, use Manager global channel and sensor state Managed; otherwise use Stable and state Stable with source default. Explicit override wins even if Manager is installed/removed; source override. Attributes include effective_channel and source, distinguishing default Stable, override Stable, override Release Candidate, managed Stable, managed Release Candidate. On Manager removal, effective fallback is Stable without overwriting persisted intent; restoration reuses global channel. Options should expose automatic/managed, Stable and Release Candidate, with clear fallback when Manager absent. Do not implement separate standalone integrations or an SDK in this repository. Provide a minimal local HA interface for compatible integrations to read Manager's global option without credentials or background synchronization.

### Native self-update
Manager owns a native HA Update entity: discover public tagged GitHub releases according to global channel, compare versions, download a validated immutable artifact, stage and replace its files, preserve HA configuration, report failures and restart requirement. Build small modular code reusable by copying into future integrations, without a runtime dependency. Cover release filtering, no update, success, download/invalid archive/partial install errors and restart behavior in tests.

## CI, release and promotion
Use clear workflow/job names, especially **CI - Validierung** for main CI, **Release - Veröffentlichung** for publishing, and **Release - Promotion** if separate. Stable, unambiguous required-check names. Thorough focused Ruff lint/format, pytest (catalog, channel, releases, install/remove, self-update, HA mocks), packaging validation, security checks for archive handling, CodeQL where supported without duplicate scans, and a sensible dependency audit. Documentation-only PRs use a fast check without bypassing required Ruleset checks. Avoid unnecessary matrices and duplicate heavy runs.

Use release tags (v0.1.0 etc.) and GitHub immutable releases, already enabled. First publish v0.1.0 as a GitHub prerelease (our Release Candidate), then manually promote the **same release, tag and identical artifact** to regular Stable after Sandbox testing. Do not rebuild, retag, or replace assets during promotion. Validate actual GitHub immutability/API behavior and report a blocker if promotion is incompatible; do not circumvent immutable releases. Require explicit owner initiation for publishing/promotion; run appropriate CI on the exact merged SHA before publication. Use least-privilege GitHub Actions permissions; no unsafe PR code execution.

## Delivery
Inspect current repo first. Keep implementation maximally modular but avoid speculative abstraction. Add meaningful tests and run them. Verify packaging, workflow names, and actual git author/committer identity. Do not change documentation, README, Wiki, Rulesets, or settings. Perform a final diff self-review. Open PR titled exactly **PR - CODEX/0.1.0** targeting main. Do not merge. Summarize functionality, test results, release workflow and remaining limitations in the PR description.

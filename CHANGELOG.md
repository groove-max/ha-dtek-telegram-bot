# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

- Add optional per-tariff meter photos to private reading review, with local optimization, preview, permission checks and single-use confirmation through HA.
- Add private DTEK document downloads and optional new-document PDF notifications, with an initial baseline and persistent delivery deduplication.
- Require the matching development integration for these features; real act availability after photo submission remains to be verified.

## [0.5.0-rc3] - 2026-10-21

**Regular GitHub release**, without the Pre-release flag. Install through the Home Assistant app store. Requires HA 2026.3.0+.

- Explain DTEK API incompatibility separately from an expired login in private menus, with links to the latest integration release and support. Do not ask users to re-enter a working password for a meter-service failure.
- Pair with **DTEK Monitor 2.0.0-rc3** to restore meter data after the authorization-format change and keep available account data usable during partial DTEK outages. Update the integration first.
- Clarify partial service failures and confirmed cabinet-group selection in the English/Ukrainian private-mode guides. Add regression coverage for the new error response.

**Upgrade:** update the integration and restart HA, then update the bot. Existing tokens, recipients, templates, sensor bindings, pinned statuses and delivery history are preserved. No meter readings are submitted automatically.

[Upgrade guide](docs/UPGRADING.md) · [Оновлення](docs/UPGRADING.uk.md)

## [0.5.0-rc2] - 2026-10-20

**Published as a regular GitHub release**, without the Pre-release flag. Install through the Home Assistant app store; the bot is an app, not a HACS integration. Requires HA 2026.3.0+, amd64/aarch64.

Version 0.5 adds private cabinet profiles alongside building alerts, private menus and history, confirmed readings, reminders, detected Telegram recipients, configurable power sources and a simpler settings wizard. Building alerts remain compatible with Monitor 1.7.0/2.0.0b1; use **Monitor 2.0.0-rc2** for the complete private workflow.

**Upgrade:** back up app data and HA configuration, update the integration first, then the app. Existing bot tokens, chats, templates, sensor bindings, status pins and delivery history are preserved. No readings are sent automatically.

- Offer a separate calibration correction during manual reading review, using the integration's captured per-tariff sensor samples and reference store. Require explicit confirmation that physical readings were taken at the displayed sample times; saving never submits to DTEK. Requires Monitor 2.0.0-rc2.
- Show modern DTEK Day/Night tariff codes as readable names in private reading entry, confirmations, history and reminders without changing the values or codes passed to the integration.
- Document regional personal profiles with the Monitor 2.0.0-rc2: Kyiv oblast and Dnipropetrovsk oblast cabinets and group lookup are experimental. The integration also supplies verified Kyiv private groups when public labels are hidden; building-mode labels remain independent.
- Treat explicitly unverified cabinet groups as a temporary notification pause instead of repeated worker errors. Preserve acknowledged schedules and resume checks when verified groups return; never substitute building groups.

[Upgrade guide](docs/UPGRADING.md) · [Оновлення](docs/UPGRADING.uk.md)

[Private mode](docs/PERSONAL.md) · [Особистий режим](docs/PERSONAL.uk.md)

## [0.5.0-rc1] - 2026-10-20

**Release candidate — for testing before the final 0.5.0 release.** Requires Home Assistant OS, HA 2026.3.0+, and amd64/aarch64. Personal mode and PNG menus require **DTEK Monitor 2.0.0-rc1**; existing building notifications also work with Monitor 1.7.0 and 2.0.0b1.

### Start here

- Use **address schedules** for a building group, channel or private chat. Use **personal cabinet** for an explicitly assigned private user's account, readings and reminders. Both can share a bot token.
- A four-step editor guides source → Telegram → notifications → review. Choose recipients detected after `/start`, or enter their numeric IDs. Drafts survive background updates.
- Update the integration first for personal mode, then the bot. Existing recipients, sensor bindings, templates and delivery history are preserved; no personal profile or submission permission is enabled automatically.
- The app store reads the repository version and may offer this RC even though GitHub marks it as a prerelease. Keep automatic app updates off if you want to stay on 0.4.1.

[English: upgrade and rollback](docs/UPGRADING.md) · [Українська: оновлення та відкат](docs/UPGRADING.uk.md)

[English user guide](README.md) · [Український посібник](README.uk.md)

### Changes since 0.4.1

- Complete the profile setup flow for new and existing profiles, with inline bot onboarding, explicit recipient selection, detailed review, and draft-safe live summaries.


- Add a four-step profile wizard, compact profile cards with one editor at a time, a single Apply action and optional tools. Preserve drafts across profile navigation, refresh and page reload; keep explicit private-owner authorization.
- Separate grid-presence sources from voltage display/quality alerts. Support binary polarity, voltage and frequency, one or two independent same-line sources, per-source freshness and optional live MQTT verification. A declared mains-powered secondary can corroborate an explicit grid loss after a grace period; missing, conflicting or restored data stays unknown. Preserve existing modes, timings and multiphase configurations without automatic migration.
- Test source combinations, contradictory/invalid/stale reports, disappearing devices, reconnects, debounce behavior and wizard draft protection. Document sensor contracts and the new setup in English/Ukrainian.

- Keep one current private menu screen, including schedule images, with persistent tracking and retryable cleanup. Preserve status, alerts and reminders; pin existing status messages without recreating them and omit the status button only when an automatically refreshed pinned status is enabled. Offer an account selector for several personal profiles. Respect Telegram's 48-hour deletion limit.
- Discover private recipients, groups and channels through the existing update collector. Add bot-specific recipient selectors alongside manual IDs, with unavailable-recipient handling, bounded metadata storage and no automatic authorization. Preserve editor drafts on refresh and isolate recipient lists when changing bot tokens. Document setup and Telegram limitations in English/Ukrainian.
- Use the shared status templates for private status requests, including overrides, voltage and schedule freshness. Add icons, clearer schedule buttons, reusable navigation and a menu return that cancels pending reading confirmations; style reading results, reminders and errors consistently. Document independent personal notification switches.
- Preserve Telegram field aliases when handling real incoming commands and callbacks, so private menus respond correctly. Keep incoming payloads out of the journal when feedback capture is disabled; test with real aiogram update models.
- Add `receive_commands` so several HA servers can send through one Telegram token while only the cabinet server receives private commands; expose the switch in the editor and document shared-token setup.

- Add personal profiles alongside existing building profiles, with an explicit private Telegram recipient, separate reading/reminder permissions and current cabinet groups only. Reuse schedule notifications and power detection without falling back to another building group.
- Add private menus for status, today/tomorrow all-group PNG tables, manual or energy-sensor reading proposals, explicit confirmation and read-only result checks. Keep account management and detailed dashboards in HA; reuse the integration's submission and calibration protections.
- Send optional rate-limited private introductions with project links to unconfigured users. Share one update consumer with test feedback; retain cursors, reject stale/replayed commands and preserve an existing webhook.
- Isolate profile state, prevent personal data from being routed to shared test chats, invalidate obsolete confirmations, and announce verified group changes even before publication without claiming an empty schedule.
- Document setup, compatibility, authorization and behavior in English/Ukrainian; add backend, browser and image regression tests. Add Pillow and Cyrillic fonts for deterministic PNG rendering.

**Compatibility:** Personal profiles and PNG menus require DTEK Monitor 2.0.0-rc1. Existing building profiles remain compatible with 1.7.0/2.0.0b1. No personal profile, submission permission or reminder is enabled automatically; private introductions default to on and can be disabled. Save and restart to apply configuration.

- Read history directly from DTEK even when HA has never submitted readings. Hide inactive records by default, retain warning statuses and add show-all/newer/older navigation. Keep submission result checks separate from official monthly settlement.
- Use the integration's calibrated cumulative-energy proposal in the reading form, with observation time and explicit confirmation. Never silently drop unavailable source data or retry an uncertain submission.
- Keep the Status button when an automatically refreshed pinned status is not configured. Allow editing the public welcome template, including its links; account data is never supplied to that template.
- Refresh English/Ukrainian guides for installation, profiles, detected recipients, independent power sources, private permissions, templates and upgrade/rollback.

## [0.4.1] - 2026-10-19

- Compare logical schedule intervals within the displayed days, preserving groups and sources while ignoring midnight splitting, timezone notation and translated titles. Migrate legacy fingerprints without suppressing real changes or acknowledging failed sends.
- Deduplicate advance warnings by group and start time, preserving old acknowledgements and sent/uncertain delivery-journal outcomes across upgrades. End-time revisions update schedules/status rather than repeating countdown warnings.
- Use continuous publication coverage for warning and pinned-status times. Mark an end at an unverified boundary as unknown; validate the status snapshot against group/revision races, fetch midnight continuations and apply the same presentation in previews and custom-template context.
- Cover upgrades, restarts, concurrent checks, failed/ambiguous sends, multiple groups, publication gaps, DST and unknown-end formatting with regression tests; document behavior in English and Ukrainian.
- Require fresh independent grid-loss confirmation for low/zero voltage in `loss_plus_voltage`. Conflicting or unavailable evidence produces an unknown state instead of false outage/recovery messages. Single-sensor detection and existing confirmation delays are preserved.
- Cover transient zero readings, missing/stale witnesses, MQTT replay and delayed/recovering loss signals with regression tests; document paired-sensor rules in English and Ukrainian.

**Upgrade:** Existing settings and Telegram delivery history are preserved; no configuration migration is needed. In paired-sensor mode, a zero voltage reading now also requires fresh confirmation from the independent grid-loss sensor. Updated integration versions provide the coverage metadata needed to mark an unverified schedule end as unknown. Custom warning templates receive `end: "невідомо"` in this case; reset the override to use the new default wording.

## [0.4.0] - 2026-10-19

- Choose a different Telegram bot for each address, or share one bot profile between several addresses. Manage profiles in the new **Telegram bots** tab.
- Keep existing addresses on the default bot and migrate its saved status and delivery history automatically. Bot profiles have independent workers, delivery journals and test destinations.
- Verify tokens with Telegram before saving; keep additional tokens in private app storage and out of configuration exports. Protect concurrent edits, token changes and deletion of profiles that are still in use.
- Send template tests only to the selected bot's explicit test chat. The production chat is never used as a fallback.
- Document multi-bot setup, backups, DTEK Monitor 1.7.0 / 2.0.0b1 compatibility, building/account boundaries, group selection and beta upgrades in English and Ukrainian.
- Add routing, migration, delivery and editor regression tests. Refresh Python setup in CI and align dependency lower bounds with the pinned runtime.

**Upgrade:** Back up the app before updating. Existing addresses continue using the default bot; no new profiles are required. Use **Save and restart** after changing profiles or address assignments. Additional bot tokens are included in app backups, not in exported address YAML.

## [0.3.13] - 2026-10-17

- Preserve configuration and per-template drafts during refresh, validation, previews and delayed saves; reject conflicting saves from another tab.
- Keep selected template addresses stable when the address list changes; support empty configurations, keyboard section controls and narrow screens.
- Add browser regression tests to CI and JSON-safe validation errors to the editor API.

- Start new installations without sample address subscriptions; preserve existing user configuration.
- Share release checks with branch/PR CI and build/test both amd64 and aarch64 images.

- License new releases under PolyForm Noncommercial 1.0.0; earlier MIT releases keep their original terms.

- Rewrite English/Ukrainian guides with personal chat, group and channel setup; clarify destination help and cover all chat types, pin permissions and private test routing with regression tests.

- Separate unknown telemetry from confirmed power loss; retire unavailable-as-off inference while retaining legacy configuration compatibility.
- Use monotonic debounce deadlines, a 5-second freshness watchdog and independent telemetry-loss/recovery notices. Preserve confirmed outage history through unknown periods.
- Validate loss-trigger freshness, reject invalid/future voltage samples, reset loss confirmation after restart and debounce per-phase notifications.
- Reject retained MQTT replay for configured loss witnesses via a passive HA websocket subscription; require live payloads matching the HA sensor.
- Add transition, restart, freshness, MQTT replay and disconnection regression tests; expose telemetry settings in the editor.
- Disable standalone telemetry notices by default while keeping pinned status updates; preserve the line break before the recovery caveat.
- Document one backed-up grid-input voltage sensor as a complete supported setup, with reporting, connectivity and freshness requirements; keep the second loss sensor optional.
- Remove repeated single-group labels from the pinned status; place the next planned interval on its own line and share formatting with template previews.

## [0.3.12] - 2026-10-17

- Respect HA's group-label visibility: display local schedule variants when the operator hides public group numbers, including Kyiv city / KEM.
- Keep opaque IDs for selection, source validation and deduplication; only presentation changes. Existing public/legacy group formatting stays compatible.
- Use the same presentation for schedule/group/upcoming messages, pinned status and template previews. Explain selected-group behavior in the address editor.
- Add hidden-group presentation and provenance regression tests.

## [0.3.11] - 2026-10-17

- Share schedule delivery acknowledgements between group-change and schedule-change notifications; serialize concurrent handlers per address and defer schedule updates while a group notification is pending.
- Persist acknowledgement only after successful delivery; failed sends remain retryable and later schedule changes still notify.
- Remove the unpublished-dates note from group notifications. An incomplete empty calendar produces only the group change, without an all-clear claim.
- Add regression tests for event order, concurrent ticks, failed delivery, restart, disabled group notifications and publication coverage.

## [0.3.10] - 2026-10-17

### Fixed

- Recheck provider and address identity together with groups and snapshot revision before schedule notification delivery.
- Use the integration's explicit coverage contract: unpublished dates cannot trigger an all-clear notification, and group-change messages explain incomplete publication.
- Preserve compatibility with integrations that do not yet expose provider/coverage metadata.

## [0.3.9] - 2026-10-17

### Fixed

- Require the integration's verified schedule flag and stable snapshot identity when fetching schedules and immediately before automatic schedule/group/countdown notifications.
- Revalidate after persistent state writes, so a failed poll or same-group schedule change during a pending send cannot deliver the old text.
- Reject display-only saved calendar events. Recovery refetches current events and does not replay warnings for already started outages.
- Remain compatible with integrations predating the freshness attributes; those still use the existing group/calendar checks.

## [0.3.8] - 2026-10-17

- Avoid repeating a schedule group already identified by the message header; retain labels for ambiguous or multi-group schedules.
- Isolate test routing, state and message ownership; collect Telegram feedback and delivery evidence in SQLite.
- Reconcile complete address groups after unavailable/reconnect, preserve per-group calendars and support an explicit selected group.
- Reject failed calendar responses instead of announcing an empty schedule.
- Detect stale voltage, preserve voltage alert timers and apply outage debounce during periodic reconciliation.
- Revalidate calendar notifications before retrying; deduplicate upcoming alerts across countdown changes and expire them at outage start.
- Keep voltage retry streams independent per sensor and ignore stale/restored recovery samples.
- Pin tested Python dependencies for Docker and CI; run tests with real runtime imports.
- Persist bounded retries for explicit temporary Telegram failures and retain ambiguous results for inspection.
- Save state/config atomically, use the actual Home Assistant config mount, and show saved/active configuration differences.

## [0.3.7] - 2026-04-22

### Fixed

- Suppressed duplicate emergency snapshot notifications when Home Assistant delivers the same outage-start snapshot more than once.

## [0.3.6] - 2026-04-22

### Fixed

- Suppressed voltage quality alerts for missing phases so zero or phase-loss values no longer generate false low-voltage notifications.
- Cleared active voltage quality alerts silently when a phase drops below the presence threshold, avoiding misleading recovery chatter during real phase loss.

## [0.3.5] - 2026-04-16

### Fixed

- Added configurable voltage hysteresis so low/high voltage alerts normalize only after returning to a safer range instead of flapping at the threshold.
- Exposed the voltage hysteresis control in the ingress UI and persisted it in runtime config.

## [0.3.4] - 2026-04-15

### Fixed

- Fixed ingress editor reloads after save so the web UI now reflects the latest persisted runtime config instead of stale startup state.
- Preserved saved voltage quality thresholds and other runtime fields in the web UI after save and refresh.

## [0.3.3] - 2026-04-13

### Fixed

- Preserved unsaved ingress UI drafts during editing and paused background refresh while configuration or templates are being changed.
- Resolved DTEK schedule group detection against both legacy `schedule_group` entities and current `primary_schedule_group` entities so Telegram status messages show the active group correctly.

## [0.3.2] - 2026-04-13

### Added

- Added Home Assistant add-on branding assets with dedicated `logo.png` and `icon.png` files.

### Fixed

- Updated `actions/setup-python` to `v6` to remove deprecated Node 20 runtime warnings in GitHub Actions.
- Preserved the original uploaded artwork under `.assets/logo-source.png` while generating Home Assistant-compatible asset sizes.

## [0.3.1] - 2026-04-13

### Fixed

- Adjusted add-on metadata to satisfy current Home Assistant add-on linter rules.
- Removed deprecated architectures from public repository metadata and build config.
- Fixed YAML formatting issues that broke `yamllint` in GitHub Actions.
- Pinned the default Docker base image argument to satisfy `hadolint`.

## [0.3.0] - 2026-04-13

### Added

- Public repository metadata for Home Assistant custom add-on distribution, including `repository.yaml`, add-on translations, and end-user documentation.
- GitHub Actions workflows for add-on validation, unit tests, Docker smoke build, and tag-based GitHub releases.
- Full ingress UI import/export for single addresses and for the whole runtime configuration draft.
- Detailed English and Ukrainian documentation focused on installation, configuration, operating modes, and real-world usage.

### Changed

- Prepared the project for publishing as a standalone GitHub add-on repository instead of a local-only development add-on.
- Clarified that the add-on is designed to work together with `ha-dtek-monitor`, using DTEK data and local Home Assistant sensors as separate signal sources.

### Fixed

- Separated browser download/export flows from on-device config save flows in the ingress editor so export no longer implies writing to the add-on filesystem.

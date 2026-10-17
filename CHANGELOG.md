# Changelog

All notable changes to this project will be documented in this file.

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

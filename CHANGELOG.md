# Changelog

All notable changes to this project will be documented in this file.

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

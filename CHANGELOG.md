# Changelog

All notable changes to this project will be documented in this file.

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


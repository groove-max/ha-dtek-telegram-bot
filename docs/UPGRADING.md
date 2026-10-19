# Upgrade to DTEK Telegram Bot 0.5.0-rc1

**English** · [Українська](UPGRADING.uk.md) · [Installation](../README.md#installation)

**RC means release candidate:** this version is available for wider testing before
the final 0.5.0 release. The HA app store reads the version in this repository;
GitHub's “Pre-release” label does not hide the update from the app store. If you
want to stay on 0.4.1, do not install this update and disable automatic updates for
this app while testing continues.

## Choose the matching versions

| Your use | DTEK Monitor | Telegram Bot |
| --- | --- | --- |
| Existing building alerts | 1.7.0, 2.0.0b1 or 2.0.0-rc1 | 0.5.0-rc1 |
| Personal cabinet, readings and history | **2.0.0-rc1** | **0.5.0-rc1** |
| Today/tomorrow PNG schedule menu | **2.0.0-rc1** | **0.5.0-rc1** |

Public schedules support the four operators listed in the integration. Personal
cabinets currently support **Odesa household accounts only**. Other regions'
cabinet support is not enabled by installing this release.

## Update an existing installation

1. Back up the app **and HA configuration**, including
   `dtek_telegram_bot.yaml`. The app data contains private bot credentials,
   templates, message IDs and delivery history. Keep the backup private.
2. For personal mode, first [update DTEK Monitor to 2.0.0-rc1](https://github.com/groove-max/ha-dtek-monitor/blob/main/docs/UPGRADING.md),
   restart HA and check that the cabinet loads. Existing building-only setups
   can keep their compatible integration version.
3. In **Settings → Apps → DTEK Telegram Bot**, install **0.5.0-rc1** and wait for
   the app to start. If the update is missing, check for updates in the app store.
4. Reload the web editor. Existing recipients, sensor bindings, custom templates
   and notification choices are retained. Existing power modes are not converted
   to the new source selector automatically.
5. Check **Current status**: HA connected, the correct recipient, fresh sensors
   where configured, and the existing status message updating in its chat.
6. For personal mode, add an **Особистий кабінет** profile and assign its private
   owner. Submission and reminders are separate, initially disabled switches.
   Apply, then send `/start` for fresh menu buttons.

No personal profile is created automatically. New welcome replies are enabled by
default, only for people who write privately to the bot; change the text or switch
them off in the editor. No new automatic reading submission is introduced.

## What changes in everyday use

- **Apply** validates, saves and restarts. **Save** alone does not change the
  running notification rules. Background refresh preserves your draft.
- Choose recipients from the detected list after `/start`; numeric IDs still work.
  Detection does not grant account access until the HA administrator selects you.
- The personal menu keeps only its latest screen. Status, notifications and
  reminders stay. A Status button remains available unless a periodically updated,
  pinned status is enabled.
- History comes from DTEK, including readings submitted outside HA. Inactive
  records are hidden by default; **Show all DTEK records** and paging reveal them.
- Sensor suggestions use the calibration in My DTEK. Review each cumulative
  reading and confirm it; do not send fictional values as a test.

Use the [personal guide](PERSONAL.md) and [power sensor guide](POWER.md) for new
features. An optional template test sends a real Telegram message: choose a test
destination first. A personal profile may only send private data to its assigned
user, including in test mode.

## Return to your previous setup

Stop the updated app and restore the pre-upgrade app **and configuration** backup,
including the earlier app version. Check the restored version before starting it.
Version 0.4.1 cannot use personal profiles or the new `sources` power mode; do not
point it at a 0.5 configuration and expect it to work unchanged.

A restore does not remove messages already sent to Telegram or undo readings
accepted by DTEK. Restoring an older delivery journal can make it forget later
notifications: review the restored destination and state before enabling a shared
chat. Never run two incoming-update collectors for the same bot token.

For problems, report both project versions, HA version, mode and reproduction
steps in [Issues](https://github.com/groove-max/ha-dtek-telegram-bot/issues). Remove
tokens, addresses and account numbers from screenshots or logs.

# Install and update DTEK Telegram Bot

**English** · [Українська](UPGRADING.uk.md) · [Installation](../README.md#installation)

Install the bot through the **Home Assistant app store**; HACS is used for the DTEK Monitor integration. [Latest bot release](https://github.com/groove-max/ha-dtek-telegram-bot/releases/latest) · [Latest integration release](https://github.com/groove-max/ha-dtek-monitor/releases/latest).

## RC8 update: sensor values and profile templates

Update **Bot to 0.5.0-rc8**, then reload its web editor. This is a regular release, without the Pre-release flag. The app update restarts the bot; no full HA restart or DTEK Monitor update is needed just for this feature.

Use **Tools → Templates** to customize one profile or a shared template, insert HA sensor values/attributes and preview the result. Existing templates stay shared and unchanged; no battery line is added automatically. All personal recipients of a profile share its template. [Battery example and setup](../DOCS.md#battery-template-example).

Template saves apply without restarting. Configuration changes such as enabling notifications still need **Apply**. Back up the entire `/data/templates/` directory, including the new `profiles/` subdirectory. Older bot releases ignore profile overrides and sensor helpers are unavailable there; restore your matching app-data backup when rolling back.

## RC7 update

Install **Monitor 2.0.0-rc7** and **Bot 0.5.0-rc7** for regional DTEK announcements. Update the integration first, restart HA, then update the app and reload its editor. Existing accounts, recipients, sensors, templates and pinned messages stay in place.

Open each profile → **3. Сповіщення → 📢 Оголошення ДТЕК**. You can show notices in status, schedule-change messages and images, or turn them off. Forecast pauses have a separate switch. These options default to enabled, but a pause requires a fresh, unambiguous emergency declaration matching the address and date. Ambiguous or unavailable notices do not confirm that the schedule applies. Actual power and voltage monitoring stay independent. See the [building guide](BUILDING.md#dtek-announcements-next-to-the-schedule) or [personal guide](PERSONAL.md#dtek-announcements-next-to-the-schedule).

This release also fixes literal HTML links in notifications and errors recording Telegram replies with link previews. Native menus, silent schedule images and optional image refresh from RC6 remain available.

<a id="main-code"></a>
## Try code from `main` before a release

Use this only when an issue asks you to test an unreleased change. **Announcements are already included in RC7 and do not require `main`.** If the change needs matching integration code, install it first and restart HA. Development builds can retain the previous version number: rebuild the app image to install source changes. Restarting alone keeps the old image.

For an existing app installed from this GitHub repository:

1. Create an HA backup including the app and its data. Keep your token, recipients, templates and sensor settings.
2. In an HA terminal with the `ha` command, run `ha addons list` and find the **slug** of your installed DTEK Telegram Bot. Replace `YOUR_BOT_SLUG` below with that exact value.
3. Refresh the repository and rebuild the existing app:

   ```bash
   ha store reload
   ha addons rebuild YOUR_BOT_SLUG
   ha addons start YOUR_BOT_SLUG
   ```

4. Wait for the build, check the app log for its HA connection and reload the editor. Open your profile → **3. Сповіщення → 📢 Оголошення ДТЕК**. Existing profiles start with these options enabled.
5. Keep the existing app installed. A second instance using the same token can compete for incoming Telegram commands.

For a **local app**, first replace its source folder under `/addons` with the
complete code from the [main archive](https://github.com/groove-max/ha-dtek-telegram-bot/archive/refs/heads/main.zip),
keeping the same folder and slug, then rebuild. Keep the app's persistent data
and HA configuration. To undo a development build, restore the app backup made
before it. After the next release, normal app updates apply again.

See the official [store reload](https://contributors.home-assistant.io/apps/supervisor-api/store/)
and [app rebuild](https://contributors.home-assistant.io/apps/supervisor-api/apps/)
operations for the distinction.

## First installation

Use **Home Assistant OS**, HA **2026.3.0+** and `amd64` or `aarch64`. [Add the app repository](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgroove-max%2Fha-dtek-telegram-bot), install DTEK Telegram Bot, enter your BotFather token and open its web UI. The [step-by-step installation](../README.md#installation) covers creating the bot and discovering your chat.

Choose [building mode](BUILDING.md) for address alerts or [personal mode](PERSONAL.md) for your cabinet. A group/channel and physical sensors are optional. A private chat is enough to get started.

## Choose the matching versions

The table lists minimum integration versions and the current recommended bot. Use the latest compatible releases for a new installation. The recommended releases are available in the regular release channel.

| Your use | DTEK Monitor | Telegram Bot |
| --- | --- | --- |
| Existing building alerts | 1.7.0, 2.0.0b1 or 2.0.0-rc2 | 0.5.0-rc8 |
| Personal cabinet, readings and history | **2.0.0-rc3** | **0.5.0-rc8** |
| Today/tomorrow PNG schedule menu | **2.0.0-rc3** | **0.5.0-rc8** |
| Exact-minute schedule PNGs | **2.0.0-rc5** | **0.5.0-rc8** |
| Meter photos and private DTEK documents (experimental) | **2.0.0-rc4** | **0.5.0-rc8** |
| Regional announcements and forecast guards | **2.0.0-rc7** | **0.5.0-rc8** |

Public schedules and personal household cabinets cover the four operators listed in Monitor 2.0.0-rc2. **Kyiv oblast and Dnipropetrovsk oblast cabinets and apartment-group lookup are experimental** and await real-account validation. Odesa and Kyiv city cabinet reads have been checked live; actual Day/Night submission has automated tests only.

## Update an existing installation

1. Back up the app **and HA configuration**, including
   `dtek_telegram_bot.yaml`. The app data contains private bot credentials,
   templates, message IDs and delivery history. Keep the backup private.
2. For personal mode, first [update DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor/blob/main/docs/UPGRADING.md),
   restart HA and check that the cabinet loads. Existing building-only setups
   can keep their compatible integration version.
3. In **Settings → Apps → DTEK Telegram Bot**, install the current version and wait for
   the app to start. If the update is missing, check for updates in the app store.
4. Reload the web editor. Existing recipients, sensor bindings, custom templates
   and notification choices are retained. Existing power modes are not converted
   to the new source selector automatically.
5. Check **Current status**: HA connected, the correct recipient, fresh sensors
   where configured, and the existing status message updating in its chat.
6. For personal mode, add an **Особистий кабінет** profile and assign its private
   recipients. Submission and reminders are separate, initially disabled switches.
   Apply, then send `/start` for fresh menu buttons.

No personal profile is created automatically. New welcome replies are enabled by
default, only for people who write privately to the bot; change the text or switch
them off in the editor. No new automatic reading submission is introduced.

## What changes in everyday use

- **Apply** validates, saves and restarts. **Save** alone does not change the
  running notification rules. Success is shown only after a new process loads the saved settings. A failed restart remains visible; retry Apply or restart the app from HA. Background refresh preserves your draft.
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
users, including in test mode.

## Return to your previous setup

RC4 and older versions do not support several recipients in one personal profile. To roll back, restore the matching configuration backup too; do not expect an older app to keep the additional recipients.



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

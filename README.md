# DTEK Telegram Bot for Home Assistant

**English** · [Українська](README.uk.md)

[![Add the app repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgroove-max%2Fha-dtek-telegram-bot)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm_Noncommercial-blue.svg)](LICENSE)

Send **DTEK outage schedules and electricity alerts to Telegram** from Home Assistant. Keep yourself in a private chat, a building group or a channel informed with schedule changes, advance warnings and one updated pinned status message.

This app uses [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor) to obtain the address's DTEK data. Optional local voltage sensors can confirm actual power loss and restoration. Supported operators currently cover **Odesa oblast, Kyiv oblast, Dnipropetrovsk oblast and Kyiv city** through that integration.

[Install](#installation) · [First setup](#setup) · [Choose power detection](#power-detection) · [Troubleshoot](#troubleshooting) · [Full guide](DOCS.md)

<a id="features"></a>
## What it does

- Announces changes to outage schedules and address groups.
- Warns before a planned outage; you choose how many minutes ahead.
- Relays DTEK's outage reasons and estimated restoration times.
- Detects power loss/restoration from suitable local sensors, with delays to filter short dips.
- Supports one phase or multiple voltage sensors, including partial outages.
- Optionally alerts on low/high voltage and recovery.
- Updates a pinned status instead of filling the chat with status posts.
- Provides a web editor for addresses, notifications, templates, previews and diagnostics.

Example pinned message — illustrative address, readings and schedule:

```text
🟢 Odesa • Example building
🔵 Група GPV3.1
✅ Електропостачання: норма
🔌 🟢 L1: 230.0 В
📅 Найближче планове:
17.10 — 19:00–22:00
🕒 Оновлено: 17.10.2026 18:00
```

The built-in web editor and Telegram messages currently use Ukrainian. English documentation does not change the message language; edit the [message templates](DOCS.md#templates) to use your own wording.

<a id="requirements"></a>
## Which project do I install?

| Project | Purpose | Installation |
| --- | --- | --- |
| [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor) | Fetches DTEK data and creates HA sensors/calendars | HACS custom integration or manual copy |
| **DTEK Telegram Bot** — this repository | Sends Telegram messages using those entities and optional local sensors | Home Assistant app repository, not HACS |

Use **Home Assistant OS**, Home Assistant **2026.3.0+**, and an `amd64` or `aarch64` device. You also need a configured DTEK Monitor address, a Telegram bot token and a target chat ID. Install matching current releases of both projects.

Home Assistant now calls add-ons **apps**; older installations show **Add-ons** instead. HA Container does not include the app manager. See [Home Assistant's app requirements](https://www.home-assistant.io/apps).

<a id="installation"></a>
## Install

1. Install [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor#installation), add your address and verify its entities.
2. Create a dedicated bot with [@BotFather](https://t.me/BotFather) using `/newbot`.
3. Choose a destination: **just you** — open the bot and press **Start**; **group** — add the bot and allow messages; **channel** — add it as an administrator with posting/editing rights. Pinning in groups/channels requires the corresponding permission.
4. Find the chat ID using the [Telegram setup guide](DOCS.md#telegram). A group/channel ID is usually a negative number; keep the minus sign.
5. Open **Settings → Apps → Install app → ⋮ → Repositories**. On older versions: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
6. Add `https://github.com/groove-max/ha-dtek-telegram-bot` and install **DTEK Telegram Bot**. The button above opens the repository dialog for you.
7. In the app's **Configuration** tab, enter `telegram_bot_token` and `telegram_chat_id`. Start the app and choose **Open web UI**.

You do not normally need to create a Home Assistant access token: the app uses Supervisor access automatically.

<a id="setup"></a>
## Configure your first address

For personal use, a conversation with the bot is enough: no group or channel is needed. Before enabling a shared feed, test in your private chat or a small test group.

1. In the web editor's **Налаштування** (Configure) tab, add an address from discovery. A new installation starts with an empty address list. Discovery selects the HA entity prefix; do not guess it from a street name.
2. Set a recognizable display name. Leave the address's chat override empty to use the app's main chat.
3. Leave **selected group** empty to follow all groups for the address. Only choose a specific group if you know it applies to your home.
4. Enable the notifications you need. For a first setup without local sensors, start with DTEK schedule/group updates and a pinned status.
5. If using local power detection, choose a mode from the table below and select the voltage entities.
6. Validate, then click **Зберегти й перезапустити** (Save and restart). Saving alone does not apply settings to the running features.
7. Check diagnostics. Preview a template, then use **Send test** to confirm the destination and bot permissions. This sends a real, labeled test message.

**Success looks like:** HA is connected, your address and group are recognized, the test appears in the intended chat, and the status message can be edited. Local power may correctly show `unknown` when no fresh measurement is available.

For enforced test routing, set the app option `test_chat_id` to your private chat, test group or channel before starting. It overrides every destination, including address overrides and template tests. See [isolated testing](DOCS.md#testing).

<a id="power-detection"></a>
## Choose how to detect power

| Available equipment | Mode | What it can establish |
| --- | --- | --- |
| No local sensor | `dtek_only` | The outage state reported by DTEK, which may lag behind reality. |
| One backed-up grid-input voltage sensor | `voltage_only` | Power loss from fresh low/zero voltage and recovery from fresh positive voltage. |
| One sensor that switches off with the grid | `voltage_only` | Fresh voltage while available; disappearance means `unknown`, not a proven outage. |
| Backed-up grid-loss sensor + voltage meter such as ATORCH | `loss_plus_voltage` | Independent confirmation when the voltage meter becomes unavailable or freezes. |

**One sensor is enough** if it keeps measuring and reporting the **grid input**, including zero, during an outage. Its gateway/network and HA also need to remain online. An inverter's backed-up output voltage does not tell you whether the external grid is present.

With two sensors on the same feed, an explicit fresh “grid absent” reading from the inverter plus an unavailable/frozen ATORCH confirms an outage after the configured delays. Either event may arrive first. If only ATORCH disappears while the inverter still reports grid present, the result is unknown local power rather than a false outage.

Sensor reporting time adds to notification latency. A sensor reporting once per minute cannot reliably provide an alert seconds after the physical event. Choose freshness limits and delays using actual measurements. [Examples, timing and transition rules →](DOCS.md#power)

Standalone telemetry-loss/recovery notices are **off by default** (`power.telemetry_notifications: false`). The pinned status still reflects missing data. Confirmed outage and recovery messages remain available.

<a id="schedules"></a>
## Schedules, groups and status

- A schedule is a plan, not a measurement. DTEK emergency restrictions can override it.
- Multiple address groups are kept separate. Selecting one does not prove that it is your apartment's group.
- Operators that hide public group numbers are shown as **«Варіант 1», «Варіант 2»**. These are local labels, not official queue numbers.
- Failed or inconsistent schedule checks pause schedule-related notifications. Saved display-only calendars cannot trigger alerts.
- An unpublished date is not announced as “no outages.”
- The default status mode edits one message. `send_new` is available if you intentionally want new status posts.

<a id="troubleshooting"></a>
## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Cannot find the app | Add this repository to the HA app store, not HACS. Check the installation method and CPU architecture. |
| Address is missing from discovery | Configure [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor) first; check its entities and HA connection. |
| No Telegram messages | Verify token, full chat ID and bot permissions; try a labeled template test and inspect the app log. |
| Settings appear saved but behavior is unchanged | Use **Save and restart** and check the pending-restart indicator. |
| Power is `unknown` | Inspect the sensor's availability, reporting time and configured maximum age. A stale number does not prove power. |
| Status posts keep appearing | Check `delivery_mode`; use `pinned_edit` and ensure the bot can edit its own message. |
| Group is listed twice in a custom message | Reset that template to the current default, or update the override. Overrides survive app upgrades. |
| Schedule notices stopped | Check DTEK Monitor's connection/freshness diagnostics. Local power monitoring can continue independently. |

<a id="support"></a>
## Guides and support

- [Full user guide](DOCS.md): Telegram setup, configuration examples, sensor requirements, templates, backups and test routing.
- [Report an issue](https://github.com/groove-max/ha-dtek-telegram-bot/issues): include versions, mode and sanitized diagnostics. English and Ukrainian are welcome.
- [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor): report missing addresses or incorrect upstream entities there.
- [Changelog](CHANGELOG.md) · [Releases](https://github.com/groove-max/ha-dtek-telegram-bot/releases).
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md).

This is an independent community project, not an official DTEK or Telegram product. Licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Noncommercial use, modification and redistribution are permitted under its terms; commercial use is not granted. This is source-available software, not an OSI open-source license. Earlier MIT releases retain their original terms. Dependencies keep their own licenses. See [NOTICE](NOTICE).

# DTEK Telegram Bot

[![Open your Home Assistant instance and add this repository to the Add-on Store.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgroove-max%2Fha-dtek-telegram-bot)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Українська версія README](README.uk.md)

Home Assistant add-on for Telegram notifications about electricity availability, DTEK outage updates, planned schedules, voltage quality, and live pinned status messages.

This add-on is built to work together with [ha-dtek-monitor](https://github.com/groove-max/ha-dtek-monitor). The integration provides DTEK data for a selected address. The add-on then combines those DTEK entities with local Home Assistant sensors, such as voltage meters, frequency sensors, or phase sensors, to produce faster and more reliable notifications.

It is especially useful for apartment-building chats, local neighborhood groups, and any setup where many people need one shared Telegram status feed for a specific address.

## Important installation note

This project is a **Home Assistant add-on**, not a HACS integration.

- Install **this add-on** from the Home Assistant **Add-on Store** by adding this GitHub repository as a **custom add-on repository**.
- Install **ha-dtek-monitor** through **HACS** or manually as a custom integration.

HACS does not install Home Assistant add-ons.

## Why this add-on exists

DTEK data alone is useful, but it is often not enough for operational notifications:

- DTEK may report an outage later than it happened physically.
- DTEK may show that power is back while the house is still dark.
- Planned schedules may exist even though the current state is actually normal.
- Physical sensors in Home Assistant may know more about the real situation than the DTEK site does.

This add-on solves that by combining both signal sources:

- `ha-dtek-monitor` for official DTEK status, outage windows, and schedule-group data
- Local Home Assistant sensors for real power presence validation
- Telegram delivery logic optimized for one shared bot in one shared chat

## Main capabilities

- Telegram notifications for factual DTEK outage start, end, reason changes, and ETA changes
- Notifications about schedule changes and upcoming planned outages
- Power detection based on DTEK only, voltage only, or fast loss trigger plus voltage confirmation
- Single-phase and multi-phase topologies with `on / partial / off` house-state logic
- Separate voltage-quality alerts for low, high, and normal voltage recovery
- Optional phase-level notifications for partial outages
- Live pinned status message with periodic refresh
- Built-in ingress UI for configuration, diagnostics, template editing, preview, and import/export
- Import/export of one address or the whole add-on configuration for migration between Home Assistant instances

## Requirements

- Home Assistant OS or Home Assistant Supervised
- Home Assistant `2026.3.0` or newer
- A working [ha-dtek-monitor](https://github.com/groove-max/ha-dtek-monitor) installation for the same address
- A Telegram bot token from BotFather
- A Telegram chat ID for the target group or channel

Recommended:

- Use a **dedicated Telegram bot token** for this add-on
- Use one address per building or entrance if the local electrical topology differs

## What the add-on reads from Home Assistant

The add-on is designed around the entities exposed by `ha-dtek-monitor`, including:

- `binary_sensor.<address_slug>_power`
- `sensor.<address_slug>_outage_status`
- `sensor.<address_slug>_outage_description`
- `sensor.<address_slug>_outage_start`
- `sensor.<address_slug>_outage_end`
- `sensor.<address_slug>_schedule_group`
- `sensor.<address_slug>_schedule_changed`
- `calendar.<address_slug>_outage_schedule`

On top of that, you can attach your own local sensors:

- one voltage sensor for single-phase setups
- two or three voltage sensors for multi-phase setups
- a fast-loss trigger sensor, for example AC frequency from an inverter or meter

## Installation

### 1. Install DTEK Monitor first

If you have not done it yet:

1. Install `ha-dtek-monitor`.
2. Add the address you want to monitor.
3. Confirm that DTEK entities appear in Home Assistant.

### 2. Add this repository to the Add-on Store

1. Open `Settings -> Add-ons -> Add-on Store`.
2. Open the menu in the top-right corner.
3. Choose `Repositories`.
4. Add:

   `https://github.com/groove-max/ha-dtek-telegram-bot`

5. Close the dialog.

### 3. Install the add-on

1. Open `DTEK Telegram Bot`.
2. Click `Install`.
3. Wait for the build to finish.

### 4. Fill in add-on options

At minimum, set:

- `telegram_bot_token`
- `telegram_chat_id`

Then start the add-on.

### 5. Open the add-on UI

After the add-on starts, open its ingress UI from the sidebar or from the add-on page.

## First-time setup

The ingress UI is the main place to configure the add-on.

Typical first-run flow:

1. Open the `Configure` tab.
2. Add an address from discovery.
3. Pick the `entity_prefix` that belongs to your `ha-dtek-monitor` address.
4. Set the display name that should appear in Telegram.
5. Choose the power detection mode.
6. Add voltage sensors if you want local validation.
7. Enable the notification features you need.
8. Run `Validate`.
9. Save the configuration.
10. Restart the add-on if needed.

## How power detection works

The add-on can treat electricity presence as more than a simple `on/off` flag.

### `dtek_only`

Use only DTEK’s power entity.

Best when:

- you do not have local voltage sensors
- you want the simplest possible setup

Tradeoff:

- DTEK can lag behind the real situation

### `voltage_only`

Use only the configured voltage topology.

Best when:

- you trust your local sensors more than DTEK
- you want the house state to reflect the real electrical situation

### `loss_plus_voltage`

Use one fast-loss sensor as an early trigger, but confirm outage and restore through the configured voltage sensors.

Best when:

- you have a sensor that reacts immediately to loss of grid input
- your voltage sensor may stop updating when power disappears
- you want fast detection without trusting stale voltage samples

## Single-phase and multi-phase logic

The `Voltage and phases` section is the shared source of truth for voltage-aware logic.

### Single-phase

If you configure one voltage entity:

- the house is either `on` or `off`
- phase notifications do not matter
- the status message shows one phase, usually `L1`

### Multi-phase

If you configure two or three voltage entities:

- `on` means all configured phases are present
- `partial` means at least one phase is missing but not all of them
- `off` means all configured phases are missing

This is useful for buildings with three-phase feed or split local distribution.

## Unavailable sensor values

Voltage sensors can behave differently when power disappears:

- some sensors return `0`
- some sensors return `unavailable`
- some keep the last known value for a while

The add-on lets you control this with the `Unavailable = missing phase` option.

- If enabled, `unavailable` can contribute to `partial` or `off`
- If disabled, `unavailable` is treated as unknown and does not automatically mean power loss

For `loss_plus_voltage`, the add-on also protects against stale voltage values by waiting for a fresh sample during the confirmation window.

## Notification features

### DTEK outage updates

These messages come from `ha-dtek-monitor` data:

- outage started
- outage ended
- outage reason changed
- outage ETA changed

If DTEK changes multiple outage parameters at once, the add-on combines them into one update instead of sending separate messages.

### Schedule changes

When DTEK changes the planned schedule, the add-on can send the new schedule or tell you that planned outages are no longer expected.

### Schedule group changes

If the address changes schedule group, the add-on can notify the new group and show the current planned windows for it.

### Upcoming planned outage warning

The add-on can warn before the next planned outage window.

You can choose whether it should trigger:

- always
- only while power is available
- only while power is missing

### Voltage alerts

Separate voltage alerts are optional.

They are useful when:

- the power is technically available
- but voltage quality is poor and equipment may behave badly

The voltage section also supports a hysteresis margin for recovery notifications.

- Low/high alerts still trigger at the configured threshold.
- The `voltage_normal` recovery message is sent only after the value moves back past the threshold by the configured hysteresis margin.
- This helps avoid repeated low/high/normal flapping when voltage oscillates close to the boundary.

### Power-loss and restore messages

These are the most important messages for most users.

They can be based on:

- DTEK only
- voltage only
- loss trigger plus voltage confirmation

In multi-phase setups, the add-on can also send separate phase-loss and phase-restore messages.

### Status message

The status message is designed for shared chats.

It can:

- edit one pinned message continuously
- or send new status messages instead

The status shows:

- current power state
- current phase summary
- latest outage reason and ETA if available
- next planned outage if available
- timestamp of the last refresh

## Templates

All Telegram messages are based on templates.

The UI allows you to:

- preview any template
- edit and save an override
- reset a template back to the built-in default
- send a test message

This is useful if you want a different style for:

- apartment-building chats
- local neighborhood chats
- your own wording for outage reasons or status messages

## Import and export

The add-on supports two levels of portability:

### Address export/import

Use this when you want to copy only one monitored address from one Home Assistant instance to another.

### Full configuration export/import

Use this when you want to move the whole add-on draft configuration to another instance.

The exported JSON does not need to be stored on the add-on device. It can be downloaded directly from the browser and then imported into another Home Assistant instance.

Note:

- the target Home Assistant instance must already have matching entities
- the add-on will not create DTEK Monitor entities for you

## Recommended deployment patterns

### Apartment building chat

Recommended:

- one address
- one shared group chat
- `loss_plus_voltage`
- pinned status message
- DTEK emergency updates enabled
- upcoming outage warnings enabled

### Private house with one reliable voltage meter

Recommended:

- one address
- one single-phase voltage entity
- `voltage_only` or `loss_plus_voltage`
- separate low/high voltage alerts if equipment is sensitive

### Three-phase building

Recommended:

- two or three voltage entities with labels `L1`, `L2`, `L3`
- `loss_plus_voltage`
- phase notifications enabled
- status message enabled to show partial outages clearly

## Troubleshooting

### The add-on does not send Telegram messages

Check:

- bot token is valid
- chat ID is correct
- the bot is a member of the target group/channel
- the add-on log does not show Telegram rate-limit or permission errors

### DTEK Monitor entities are missing

This add-on depends on `ha-dtek-monitor`.

If those entities do not exist, the add-on cannot build DTEK-aware notifications for that address.

### Power state looks wrong

Review:

- selected power mode
- configured voltage entities
- `present_above`
- `Unavailable = missing phase`
- `loss_entity` and `loss_state`

Then compare the live values in the `Diagnostics` tab.

### The status message is duplicated

This usually means the previous status message could not be edited in Telegram.

Use a dedicated bot token for this add-on when possible, and avoid sharing the same token with other systems that frequently edit or post messages.

### DTEK says there is no outage, but the house is still dark

This is an expected real-world scenario.

In that case the add-on can still show:

- `power_state = off`
- local outage duration
- `reason: unknown`
- note that DTEK does not report an active outage

## Limitations

- The add-on cannot improve DTEK source data; it can only combine it with local Home Assistant signals.
- If no local power sensor exists, DTEK lag is unavoidable.
- If local sensors are unstable, bad topology choices can create false detections.
- Telegram delivery still depends on Telegram API availability and rate limits.

## Support

- Repository: <https://github.com/groove-max/ha-dtek-telegram-bot>
- DTEK Monitor dependency: <https://github.com/groove-max/ha-dtek-monitor>

## Contributing

Contributions are welcome, but this repository is optimized for practical Home Assistant operation rather than experimental feature sprawl.

Before opening a PR:

- read [CONTRIBUTING.md](CONTRIBUTING.md)
- keep changes narrow and testable
- update docs if user-visible behavior changes
- include tests for power-detection or message-logic changes when possible

## License

[MIT](LICENSE)

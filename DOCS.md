# DTEK Telegram Bot

Home Assistant add-on that sends Telegram notifications for one or more DTEK-monitored addresses and combines official DTEK data with local Home Assistant power sensors.

This add-on is designed to work together with [ha-dtek-monitor](https://github.com/groove-max/ha-dtek-monitor). It is especially useful for shared apartment-building chats where people need:

- current power state
- outage reason and ETA from DTEK
- local confirmation that electricity is really present or absent
- one continuously updated pinned status message

## Before you install

This project is a **Home Assistant add-on**.

- Install it from the **Add-on Store** as a **custom add-on repository**
- Do **not** try to install it through HACS
- Install **ha-dtek-monitor** separately as a custom integration

## Requirements

- Home Assistant OS or Home Assistant Supervised
- Home Assistant `2026.3.0` or newer
- A configured `ha-dtek-monitor` address
- Telegram bot token from BotFather
- Telegram chat ID for the target group or channel

## Installation

1. Install and configure `ha-dtek-monitor` for your address.
2. Open `Settings -> Add-ons -> Add-on Store`.
3. Open the menu in the top-right corner and choose `Repositories`.
4. Add:

   `https://github.com/groove-max/ha-dtek-telegram-bot`

5. Install `DTEK Telegram Bot`.
6. Set add-on options:
   - `telegram_bot_token`
   - `telegram_chat_id`
7. Start the add-on.
8. Open the add-on UI.

## Basic setup flow

1. Go to the `Configure` tab.
2. Add an address from discovery.
3. Select the `entity_prefix` that belongs to your `ha-dtek-monitor` address.
4. Choose a power detection mode.
5. Add voltage sensors if you want local power validation.
6. Enable the notification features you need.
7. Click `Validate`.
8. Save the config.

## Power detection modes

### `dtek_only`

Uses only the DTEK power entity.

### `voltage_only`

Uses only local voltage sensors.

### `loss_plus_voltage`

Uses a fast-loss trigger plus voltage confirmation. This is the recommended mode when you have a sensor that reacts immediately to grid loss, but your voltage sensor may keep stale values for a few seconds.

## Single-phase and multi-phase support

The add-on supports:

- one voltage sensor for single-phase setups
- two or three voltage sensors for multi-phase setups

In multi-phase mode the house state can be:

- `on`
- `partial`
- `off`

This allows separate notifications for:

- full outage
- full restore
- missing phase
- restored phase

## Notification features

- DTEK outage start, end, reason change, and ETA updates
- Schedule changes
- Schedule-group changes
- Upcoming planned outage warning
- Voltage quality alerts
- Power loss and power restore
- Phase-level notifications
- Live pinned status message

Each feature can be enabled or disabled independently and can also be sent in silent mode.

## Status message

The status message is intended for group chats.

It can either:

- edit one pinned message
- or send new status messages instead

The status can show:

- current power state
- current voltage / phase state
- outage reason and ETA from DTEK
- next planned outage
- live update timestamp

## Templates

All messages are template-based.

From the UI you can:

- preview messages
- edit templates
- save overrides
- reset to defaults
- send test messages

## Import and export

The UI supports:

- export/import of one address
- export/import of the full add-on configuration draft

This is useful when moving the setup between multiple Home Assistant instances.

## Troubleshooting

If the add-on does not behave as expected:

1. Open the `Diagnostics` tab.
2. Compare live HA entity values with the current power state preview.
3. Check the add-on logs for Telegram API errors.
4. Make sure the same Telegram bot token is not being overused by multiple systems.

## Notes

- DTEK data and real power state may differ temporarily.
- This add-on is built specifically to bridge that gap using local Home Assistant sensors.
- The more reliable your local sensors are, the better the final notification quality will be.

For full documentation:

- English: [README.md](README.md)
- Ukrainian: [README.uk.md](README.uk.md)


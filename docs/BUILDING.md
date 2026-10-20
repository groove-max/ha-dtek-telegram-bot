# DTEK notifications for your building

**English** · [Українська](BUILDING.uk.md) · [Install the app](../README.md#installation)

Use **Address schedules** when you want alerts for a house or building without signing in to a DTEK cabinet. Send them to a Telegram group, a channel or just yourself. One profile follows one address and sends to one recipient.

For your apartment's cabinet group, reading history and meter submissions, choose [personal mode](PERSONAL.md). You can use both modes in the same app and with the same Telegram bot.

## Before you start

- Install [DTEK Monitor](https://groove-max.github.io/ha-dtek-monitor/en/installation/), add the building address and check that its entities load in HA. A cabinet login is optional.
- [Install this app](../README.md#installation) and enter the token of the bot you created with [@BotFather](https://t.me/BotFather).
- Choose a destination. For a private chat, open your bot and press **Start**. For a group, add the bot and allow it to send messages. For a channel, make it an administrator with posting/editing rights. Pinning in shared chats requires the corresponding permission.

## Connect the building and chat

1. Open the app's web UI → **Налаштування → Додати профіль**.
2. Choose **Графіки за адресою** and select your building. Give it a short display name that your neighbours will recognize.
3. Leave the group selection empty to follow **all groups for this building**. Select one only when you intentionally want notifications for that group.
4. On the **Telegram** step, choose the bot and recipient. Send `/start` privately or `/start@your_bot_username` in a group, then press **Оновити**. A channel appears after a new post or membership update reaches the bot. [Recipient discovery and manual IDs](../DOCS.md#telegram).
5. Choose notifications and, optionally, [power sensors](POWER.md). Sensors are not required for schedule alerts.
6. Review the chat and settings, then press **Застосувати**. This validates, saves and restarts the app.

The recipient list uses new Telegram updates; it cannot list every chat the bot ever joined. **Receive commands** must be enabled on the HA collecting those updates. Only one collector may use a given bot token. [Using one bot on several servers](PERSONAL.md#one-token-on-several-ha-servers).

## Choose useful notifications

| Notification | What neighbours receive |
| --- | --- |
| Schedule changes | The updated verified schedule for the building's groups. |
| Group changes | A confirmed change in the groups assigned to the address. |
| Advance warning | A reminder before an upcoming verified outage; you choose the lead time. |
| DTEK outage status | The reason and estimated restoration reported by the operator. |
| Actual power loss and recovery | Sensor-based transitions, when suitable sensors are configured. |
| Voltage quality | Optional low/high voltage and recovery alerts, configured separately. |

Enable only the notifications useful to this chat. A schedule describes a plan; it cannot prove that power is physically present. Missing or unpublished schedules pause schedule alerts instead of announcing that there are no outages. Sensor monitoring can continue independently.

## Keep one current status

Enable **Status message**, use **edit the existing message** (`pinned_edit`) and enable pinning if wanted. The bot updates its own status message with groups, power, voltage and the next verified outage. Automatic notifications remain separate messages.

Without pinning, it can still update one ordinary message. Check the bot's edit/pin permissions if duplicate messages or pinning errors appear. A lost sensor is shown as unknown; separate telemetry-loss messages are off by default.

Messages and the editor use Ukrainian by default. Edit their wording in [Templates](../DOCS.md#templates), including the welcome message for new private users. Detailed cabinet data and reading submission are available only in an authorized private profile, never in the building feed.

## Several buildings, chats or bots

- Add another profile for another address.
- Use **Clone** to send the same building to another recipient with independent settings and delivery history.
- Select another bot on the profile's Telegram step if you want a separate name/token. Several profiles can reuse one token.

You do not need a second app installation. Profiles are configured by the HA administrator; writing to the bot does not automatically subscribe someone to a building. [Profiles, destinations and tokens](../DOCS.md#destinations).

## Check delivery before opening a shared feed

Review the recipient first. Use an optional [test destination](../DOCS.md#testing) and a labeled template test in your private chat or test group. A test sends a real message. Do not use a group of residents for experiments.

Check **Current status** in the editor: HA connected, correct address/groups and correct destination. A missing daily schedule or absent physical sensor can legitimately show unavailable/unknown data.

[Troubleshooting](../README.md#troubleshooting) · [All notification settings](../DOCS.md#notifications)

# Personal DTEK notifications and readings in Telegram

**English** · [Українська](PERSONAL.uk.md) · [Main guide](../DOCS.md)

> Requires **bot 0.5.0-rc2 or newer** and **DTEK Monitor 2.0.0-rc2 or newer**. Both are regular GitHub releases. Update the integration first, then the bot. Building mode continues to work without a cabinet.

Use one bot for a building channel and your own private cabinet. Each profile selects its own source and recipient; you do not need another bot token. Account setup, detailed history, sensor bindings and scheduled-check settings remain in **My DTEK** in Home Assistant. Version 0.5.0-rc2 also lets you confirm a calibration correction during reading entry.

## Choose a profile

| Mode | Source of outage groups | Destination |
| --- | --- | --- |
| Building (`building`, default) | Public building entities; optionally filter a confirmed group | Channel, group or private chat |
| Personal (`personal`) | Current groups reported by the cabinet for the selected account | Only the explicitly authorized user's private chat |

Personal notifications reuse the building mode's schedule-change, group-change and advance-warning logic, including publication checks and duplicate protection. Multiple cabinet groups are retained. A manual group chosen in My DTEK does not replace the cabinet's assignment in this mode. If the cabinet group is missing, expired, disabled or inconsistent with the building, schedule alerts pause instead of falling back to another group. Fresh physical power sensors continue working independently. DTEK's reported outage status still describes the linked building, not a measurement inside your apartment.

A verified assignment change can be announced before its schedule is published. That message will not claim there are no outages.

## Set up a personal profile

1. In [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor), sign in to the cabinet and select the account. Check its building link in **My DTEK → Settings → Building and outage group**. Cabinet group lookup must be enabled and return current data for group notifications. Lookup is enabled for all four operators. Kyiv oblast and Dnipropetrovsk oblast cabinets and apartment-group lookup are experimental and have not been checked with real linked accounts. Kyiv city and Kyiv oblast are separate operators. Hidden labels on the public website do not block private notifications when the cabinet supplies a current group that belongs to the building. If that assignment is unavailable or conflicting, schedule notifications wait for valid data; the bot never substitutes another group.
2. Open your Telegram bot and press **Start**. Before access is configured, its introduction shows your numeric **Telegram ID**. This does not give access to any account. If introductions are disabled, use the [chat ID instructions](../DOCS.md#telegram).
3. In the add-on web editor, add a profile and choose **Особистий кабінет**. Select the account by its masked number and address, choose the Telegram bot, then select yourself from the detected recipients or enter your numeric Telegram user ID. After `/start`, press **Refresh** in the editor to update the list. Verify the name, username and ID before saving. The destination is automatically this user's private chat; a username, channel or group cannot be substituted.
4. Enable the notifications you need. Enable **reading reminders** and **allow reading submission** separately. Both are off by default. Link physical power sensors only if they measure this account's home; they are optional.
5. Validate, then **Apply (Застосувати)**. Open `/start` in Telegram. With several authorized accounts, choose an account first; each has its own status message. Choose a short, distinct display name for each account.

Only the HA administrator can assign an account to a Telegram user. DTEK login credentials stay in the integration. Knowing a bot name, address, account number or button token does not grant access. Removing a profile or permission takes effect when the add-on restarts; save alone does not reload the active configuration.

## Use the menu

The reading-entry and history buttons appear when **Allow reading submission** is enabled for this profile. That switch authorizes access to the reading workflow; it never sends values without your final confirmation. Leave it off for notifications and schedules only.

- **Pinned status**: one automatically updated message with your address, cabinet group, power state, fresh voltage, next verified outage and update time. It uses the same templates and custom overrides as building profiles. Enable the status message, editing mode and pinning in the profile. The **⚡️ Стан (Status)** button is hidden only when the profile has an enabled, pinned message that is edited in place and refreshed periodically (interval greater than zero). The button is available if you disable status messages or pinning, choose a new message for each update, or set the refresh interval to `0`. It requests the current status using the same templates, even when automatic status messages are disabled. This choice is independent for each profile. After applying settings, open **🏠 Меню** or `/start` to see the updated buttons. Unknown power and unavailable schedules remain clearly labeled.
- **📅 Графік на сьогодні / 🗓 Графік на завтра (Today's / tomorrow's schedule)**: a PNG document with all groups from the linked operator's schedule. Your cabinet group is highlighted; building profiles highlight all home groups. Publication/verification times and forecast/unknown cells remain visible. An unavailable day is not displayed as an all-clear day. The refresh uses the integration's shared cooldown.
- **Submit readings**: enter cumulative whole kWh for each supported tariff zone, or use an available calculation from the energy sensor configured in My DTEK. The bot shows previous/new readings and their difference. This difference is **not necessarily a calendar month's consumption**. Nothing is sent until you press **Confirm and submit to DTEK**.
- **📋 Покази в ДТЕК (Readings in DTEK)**: see the latest published cumulative readings and their dates, even if you submitted them outside HA. The bot checks the current year, then the previous year if it finds no active-energy records. Repeated requests share a cache of at most one minute; the screen shows when DTEK was checked. Inactive records are hidden by default; blocked and unknown statuses remain visible as warnings. Use **🗂 Показати всі записи ДТЕК (Show all DTEK records)** to include inactive records for the displayed year, and the newer/older buttons to browse six records per page. Refresh preserves this choice. Identical values on different dates or tariff zones remain separate. The filter does not change DTEK data or submission verification. A failed request is shown as unavailable, not as empty history. If this HA has a submission record, its result is shown separately and **Check last submission** compares it against DTEK history. Matching values do not prove a final monthly settlement.

When a source is configured, the first submission screen shows the proposed cumulative reading, its observation time and whether it uses the saved calibration or a direct meter reading. Use the proposal or enter your own value; the existing HA confirmation and calibration rules apply. Unavailable, stale, reset or changed sensors are explained instead of silently dropping the proposal. The source may be any supported cumulative energy sensor, not just ATORCH.

With the Monitor 2.0.0-rc2, manual reading review offers **📏 Зберегти поправку калібрування (Save calibration correction)** for an estimate binding. Check each tariff's official reading, sensor sample/time and correction, then confirm **Знято одночасно — зберегти (Taken together — save)** only if you read the physical meter at those times. Samples must be no more than three minutes old, including availability mode. Day/Night requires separate cumulative sensor values. Older physical readings must not be paired with current samples. Saving updates HA's reference pairs and does **not** submit anything to DTEK; return to reading review to send separately. Canceling leaves references unchanged. Editing a number alone does not save a correction.

The reading flow supports one consumption meter, with one zone or Day/Night. Starting over, editing, `/cancel`, expired confirmation or a restart invalidates unfinished Telegram confirmations. After rejection, a partial result or an unknown result, inspect **My DTEK** before retrying. The bot never automatically repeats a submission whose outcome is uncertain.

Replies include **🏠 Меню (Menu)**. Only the latest menu screen is retained: after successfully sending a new screen, the bot removes its previous screen, including a schedule image. The pinned status, automatic notifications, reading reminders and your own messages remain. The bot remembers its last screen across restarts. Returning to the menu cancels an unfinished reading confirmation. Navigation buttons are reusable; submission confirmation is single-use. After a restart, open `/start` for fresh buttons.

Telegram only allows deletion within [48 hours](https://core.telegram.org/bots/api#deletemessage). For older screens, the bot removes their buttons instead. Temporary cleanup failures are retried on later navigation. Messages sent before screen tracking was installed cannot be identified safely and remain in the chat.

## Detected Telegram recipients

The editor offers detected recipients next to the manual ID fields. Each bot has its own list. Personal profiles show only private users; building profiles and bot destination fields also show groups and channels. Detection alone never grants access: the administrator must select a recipient, save and restart.

1. Start the bot with **Receive commands** enabled. The default destination can initially be empty. For an additional bot, save its token and restart first.
2. Send `/start` privately. In a group, send `/start@your_bot_username`. For a channel, add the bot as an administrator or publish a new post after the collector starts.
3. Press **Refresh** in the editor, select the intended bot and recipient, then save and restart. Manual numeric ID entry remains available.

Telegram does not provide a complete list of a bot's chats or arbitrary old history. Only chats visible through new updates appear. Blocked users and chats the bot has left are marked unavailable when Telegram reports the change. If one token is shared across HA servers, the list is available on the server receiving commands; it is not synchronized to the others.

The list stores ID, chat type, name, username, availability and last-seen time, up to 500 entries per bot delivery journal. It does not store message content. Refreshing it never overwrites an ID you are editing. Check the displayed username and numeric ID: a display name alone does not establish identity.

## Automatic notifications

Personal profiles use the same notification settings and templates as building profiles: schedule changes, confirmed group changes, advance warnings, DTEK-reported outages, measured power loss/recovery and voltage quality. These run in the background; opening the menu is not required. Configure each switch and its silent-delivery option in the profile editor, then save and restart.

Voltage quality is off by default: enable it explicitly, select voltage sensors for this home, and set low/high limits, delay and hysteresis. Selecting a voltage sensor for power detection alone does not enable voltage-quality alerts. Your cumulative kWh sensor is separate from these voltage sensors. Group and schedule notifications require a current cabinet assignment; missing schedules pause schedule alerts, while physical sensor monitoring continues independently.

There is no unattended submission in either project. Scheduled checks prepare a suggestion; you still confirm every send. Reminder and submission permissions are independent.

## Reminders

Enable reminders per personal profile. The bot checks during the integration's reading window: the last two days of a month and the first three days of the next, after 19:00 Kyiv time. It sends one reminder per period, with **Already submitted** and **Remind tomorrow** buttons. An accepted submission recorded by this HA within the window suppresses it; a reading sent elsewhere may require dismissing the reminder manually.

Dismissal only closes the reminder. Snoozing does not submit readings and stops when the window ends. HA cabinet reminders and Telegram reminders are separate switches. After an uncertain Telegram delivery, retries wait a day to avoid repeated posts.

## Introduction for other users

Edit the welcome text in **Templates → Привітання · welcome**, then save. Changes apply immediately and survive restarts and updates; **Reset** restores the standard introduction with links to both projects. You can replace the entire text, including its links. The only runtime variable is `{{ telegram_user_id }}` (the sender’s ID); no account or address data is passed to this public template. Telegram HTML formatting is supported. This template is shared by all bot instances on this add-on.

`welcome_enabled` is on by default and can be changed in the editor. A person without an assigned private profile who messages the bot receives a short Ukrainian explanation, links to both GitHub projects and their own Telegram ID. The response contains no configured addresses or account information. It is limited to once per user per 24 hours, with an additional limit of 20 introductions per minute per bot; the limit survives restarts.

There are no automatic introductions in groups/channels or unsolicited messages. A channel subscriber can receive this introduction when writing privately because channel membership does not authorize private account access. Pressing Start is not a subscription or registration flow.

The add-on uses one incoming-update collector per bot for both commands and test feedback. Do not run a separate `getUpdates` collector or webhook for the same token. If a webhook is already configured, the add-on leaves it intact; resolve the conflict before using commands.

## Testing and YAML

Keep building notification tests in a test group. For a **personal** profile, the bot's enforced test destination must be the same authorized private user, or testing mode must be off: configuration rejects routing cabinet details into a shared test channel. Template tests are real messages; review private content before choosing a destination. Prefer a dedicated test bot for account workflows. No readings are submitted by previewing a template or a schedule.

Normally use the editor. Equivalent minimal runtime YAML, with fictional identifiers:

```yaml
welcome_enabled: true
addresses:
  - profile_id: my_account
    mode: personal
    account_entity: sensor.example_account_updated
    telegram_bot_id: default
    telegram_user_id: 123456789
    display_name: My apartment
    allow_readings: false
    reading_reminders: true
```

Select the actual account entity from discovery instead of guessing its ID. Leave `selected_group` empty. `profile_id` is a stable identity for state and delivery history; the editor/backend creates one when needed. Clone a building profile to send the same building to another destination with independent history; keep the clone's distinct ID. Existing profiles without IDs keep their previous state.


## One token on several HA servers

Both servers can send through the same bot. Keep `receive_commands: true` on the server with the personal cabinet. Set `receive_commands: false` on the others and restart the add-on. This disables incoming commands, introductions, personal reading reminders and feedback collection there; ordinary notifications continue. The switch is available in the editor. Never run two incoming collectors for the same token.

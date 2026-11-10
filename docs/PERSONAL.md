# Personal DTEK notifications and readings in Telegram

**English** · [Українська](PERSONAL.uk.md) · [Main guide](../DOCS.md)

> Recommended release pair: **bot 0.5.0-rc9** and **DTEK Monitor 2.0.0-rc8**. See [minimum versions by feature](UPGRADING.md#choose-the-matching-versions) for older installations. Both are regular GitHub releases. Update the integration first, then the bot. Building mode continues to work without a cabinet.

Use one bot for a building channel and your own private cabinet. Each profile selects its own source and recipients; you do not need another bot token. Account setup, detailed history, sensor bindings and scheduled-check settings remain in **My DTEK** in Home Assistant. Version 0.5.0-rc2 also lets you confirm a calibration correction during reading entry.

## Choose a profile

| Mode | Source of outage groups | Destination |
| --- | --- | --- |
| Building (`building`, default) | Public building entities; optionally filter a confirmed group | Channel, group or private chat |
| Personal (`personal`) | Current groups reported by the cabinet for the selected account | Private chats of explicitly authorized users |

Personal notifications reuse the building mode's schedule-change, group-change and advance-warning logic, including publication checks and duplicate protection. With Monitor 2.0.0-rc3 or newer, the bot uses cabinet groups confirmed by fresh building data. If DTEK also reports unconfirmed groups, My DTEK explains the mismatch; those extra groups do not stop alerts for the confirmed ones. A manual group chosen in My DTEK does not replace the cabinet's assignment in this mode. If the cabinet group is missing, expired, disabled or has no confirmed overlap with the building, schedule alerts pause instead of falling back to another group. Fresh physical power sensors continue working independently. DTEK's reported outage status still describes the linked building, not a measurement inside your apartment.

A verified assignment change can be announced before its schedule is published. That message will not claim there are no outages.

## One apartment, several people

In the apartment profile, open **Telegram → Користувачі Telegram (Telegram users)**. Each family member can choose **🔗 Request access** (`/connect`) in the Telegram menu. Press **Refresh recipients**, then **Add to this profile** beside their request. You can also add people from the discovery dropdown or enter numeric IDs separated by commas. Selecting a person adds them without replacing previous recipients. Press **Apply** and wait for the confirmed restart.

Each person gets their own private menu, status message, enabled notifications, reminders and new documents. The account, sensors and permission to submit readings are shared settings. Reading drafts, confirmation buttons and reminder dismissals belong to each person separately. Submitted readings belong to the same DTEK account; check its current readings before submitting again after someone else.

To revoke access, remove the ID and apply the change. Discovery alone never grants access. You do not need a Telegram group or duplicate apartment profiles. The legacy single `telegram_user_id` remains compatible; use `telegram_user_ids` for new YAML configurations (up to 50 people).

## Set up a personal profile

1. In [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor), sign in to the cabinet and select the account. Check its building link in **My DTEK → Settings → Building and outage group**. Cabinet group lookup must be enabled and return current data for group notifications. Lookup is enabled for all four operators. Kyiv oblast and Dnipropetrovsk oblast cabinets and apartment-group lookup are experimental and have not been checked with real linked accounts. Kyiv city and Kyiv oblast are separate operators. Hidden labels on the public website do not block private notifications when the cabinet supplies a current group that belongs to the building. If that assignment is unavailable or conflicting, schedule notifications wait for valid data; the bot never substitutes another group.
2. Open your Telegram bot and press **Start**, then choose **🔗 Request access** (`/connect`) from Telegram’s **Menu** button beside the message field. This sends a request to the owner; it does not grant account access. `/connect` works even if the promotional greeting is disabled.
3. In the add-on web editor, add a profile and choose **Особистий кабінет**. Select the account by its masked number and address, choose the Telegram bot, then add the authorized people from **Connection requests**, detected recipients or numeric Telegram user IDs separated by commas. Press **Refresh recipients** in the editor to update the list. Adding a requester changes only the editor draft; **Apply** activates access. Verify the name, username and ID before saving. Each destination is automatically that user's private chat; a username, channel or group cannot be substituted.
4. Enable the notifications you need. Enable **reading reminders** and **allow reading submission** separately. Both are off by default. Link physical power sensors only if they measure this account's home; they are optional.
5. Validate, then **Apply (Застосувати)**. Open `/start` in Telegram. With several authorized accounts, choose an account first; each has its own status message. Choose a short, distinct display name for each account.

Only the HA administrator can assign an account to a Telegram user. DTEK login credentials stay in the integration. Knowing a bot name, address, account number or button token does not grant access. Removing a profile or permission takes effect when the add-on restarts; save alone does not reload the active configuration.

## Use the menu

Telegram’s **Menu** button beside the message field opens commands. You do not have to type `/start` each time. Existing buttons inside messages remain available, especially for selecting a tariff, attaching photos and confirming submissions.

| Command | Opens |
| --- | --- |
| `/start` | Main menu; does not create an access request |
| `/today`, `/tomorrow` | Schedule for the selected day |
| `/status` | Current status, listed when an automatically refreshed pinned status is not configured |
| `/readings`, `/history` | Reading entry and official DTEK history, when permitted |
| `/documents` | Documents for a personal account |
| `/accounts` | Profile selection, when you have several profiles |
| `/cancel` | Discard your current reading draft; cannot undo readings already sent |
| `/help` | Commands and help |

With several eligible profiles, a command asks which one to use and then opens the requested action. The menu contains only commands supported by your profiles. Descriptions are English or Ukrainian; Telegram’s Russian language setting uses Ukrainian descriptions. Existing message texts remain Ukrainian.

Opening the menu, schedule or documents keeps your reading draft, including entered tariff values and photos. Use **Continue reading entry** from `/start` or **Back to reading draft** beneath a screen. Old confirmation buttons become inactive when you leave the reading review; resuming requests a fresh review before submission. Switching to another account asks before discarding the draft. Drafts last up to five minutes and do not survive an app restart; `/cancel` explicitly discards one. No navigation command submits readings.

The reading-entry and history buttons appear when **Allow reading submission** is enabled for this profile. That switch authorizes access to the reading workflow; it never sends values without your final confirmation. Leave it off for notifications and schedules only.

- **Pinned status**: one automatically updated message with your address, cabinet group, power state, fresh voltage, next verified outage and update time. It uses the same templates and custom overrides as building profiles. Enable the status message, editing mode and pinning in the profile. The **⚡️ Стан (Status)** button is hidden only when the profile has an enabled, pinned message that is edited in place and refreshed periodically (interval greater than zero). The button is available if you disable status messages or pinning, choose a new message for each update, or set the refresh interval to `0`. It requests the current status using the same templates, even when automatic status messages are disabled. This choice is independent for each profile. After applying settings, open **🏠 Меню** or `/start` to see the updated buttons. Unknown power and unavailable schedules remain clearly labeled.
- **📅 Графік на сьогодні / 🗓 Графік на завтра (Today / tomorrow)**: opens your confirmed personal group or building groups. **👤 My group / 🏠 My building / 📊 All groups** change the scope and keep your group highlighted. In **📊 All groups**, **🙈 Hide address and highlights** removes the address and personal markings; **🏠 Show address and highlights** restores them. This choice survives refresh, page changes and export. There is no separate duplicate General schedule button. This view is available within your authorized profile; it does not grant access to other accounts. Large grids have 12 groups per page; arrow buttons replace the previous screen. The image is wide, with an uninterrupted **00:00–24:00** table. Exact outage ranges appear in a separate two-column list below the table, identified by group. Short intervals such as **12:15–12:30** retain their actual width and a readable text label. Possible outages, forecasts and missing data are labelled separately.

  Today's image has a **Зараз (Now)** line and a faded past section. Tomorrow's image has neither. **The line is fixed when the image is generated**; use **🔄 Оновити (Refresh)** for a fresh image and current-time line. Refresh keeps the selected date, groups and page, replaces the previous picture, and respects the integration's shared refresh cooldown. The generated time and source verification time are shown separately. Repeated hours on daylight-saving changes include UTC offsets.

  **📎 Усі сторінки одним файлом (All pages in one file)** appears only in multi-page **All groups** views. It sends every group in one PNG file with the same exact intervals, labels and time marker. When the table fits on one page, the duplicate file button is hidden. Unknown assignments remain explicit. With an older integration, the available precision is half an hour; update both projects for exact minute boundaries.
- **Submit readings**: enter cumulative whole kWh for each supported tariff zone, or use an available calculation from the energy sensor configured in My DTEK. The bot shows previous/new readings and their difference. This difference is **not necessarily a calendar month's consumption**. Nothing is sent until you press **Confirm and submit to DTEK**.
- **📋 Покази в ДТЕК (Readings in DTEK)**: see the latest published cumulative readings and their dates, even if you submitted them outside HA. The bot checks the current year, then the previous year if it finds no active-energy records. Repeated requests share a cache of at most one minute; the screen shows when DTEK was checked. Inactive records are hidden by default; blocked and unknown statuses remain visible as warnings. Use **🗂 Показати всі записи ДТЕК (Show all DTEK records)** to include inactive records for the displayed year, and the newer/older buttons to browse six records per page. Refresh preserves this choice. Identical values on different dates or tariff zones remain separate. The filter does not change DTEK data or submission verification. A failed request is shown as unavailable, not as empty history. If this HA has a submission record, its result is shown separately and **Check last submission** compares it against DTEK history. Matching values do not prove a final monthly settlement.

When a source is configured, the first submission screen shows the proposed cumulative reading, its observation time and whether it uses the saved calibration or a direct meter reading. Use the proposal or enter your own value; the existing HA confirmation and calibration rules apply. Unavailable, stale, reset or changed sensors are explained instead of silently dropping the proposal. The source may be any supported cumulative energy sensor, not just ATORCH.

With the Monitor 2.0.0-rc2, manual reading review offers **📏 Зберегти поправку калібрування (Save calibration correction)** for an estimate binding. Check each tariff's official reading, sensor sample/time and correction, then confirm **Знято одночасно — зберегти (Taken together — save)** only if you read the physical meter at those times. Samples must be no more than three minutes old, including availability mode. Day/Night requires separate cumulative sensor values. Older physical readings must not be paired with current samples. Saving updates HA's reference pairs and does **not** submit anything to DTEK; return to reading review to send separately. Canceling leaves references unchanged. Editing a number alone does not save a correction.

The reading flow supports one consumption meter, with one zone or Day/Night. Starting over, editing, `/cancel`, expired confirmation or a restart invalidates unfinished Telegram confirmations. After rejection, a partial result or an unknown result, inspect **My DTEK** before retrying. The bot never automatically repeats a submission whose outcome is uncertain.

Replies include **🏠 Меню (Menu)**. Only the latest menu screen is retained: after successfully sending a new screen, the bot removes its previous screen, including a schedule image. The pinned status, automatic notifications, reading reminders and your own messages remain. The bot remembers its last screen across restarts. Returning to the menu cancels an unfinished reading confirmation. Navigation buttons are reusable; submission confirmation is single-use. After a restart, open `/start` for fresh buttons.

Telegram only allows deletion within [48 hours](https://core.telegram.org/bots/api#deletemessage). For older screens, the bot removes their buttons instead. Temporary cleanup failures are retried on later navigation. Messages sent before screen tracking was installed cannot be identified safely and remain in the chat.

## Connection requests and invitations

Before authorization, the native menu offers `/start`, `/connect` and `/help`. `/start` displays the configured greeting; `/connect` or an invitation link creates a request. In the profile’s **Telegram** step, the owner verifies the applicant’s name, username and numeric ID, chooses **Add to this profile**, reviews account permissions and presses **Apply**. Acceptance is recognized when the new configuration is running; the user then receives confirmation. Saving without restarting leaves access unchanged.

Requests are separate from detected recipients. Repeating `/connect` shows the existing pending status. **Decline** closes the request; the user can request again after one day. Requests expire after 30 days; storage is limited to 500 records per bot delivery journal. A request includes Telegram ID, name, username, timestamps and status. An invitation also identifies the target profile; it does not reveal account details to the applicant. A shared building destination is never replaced with an applicant’s private ID: create a **Personal cabinet** profile for private account access. Manual IDs remain supported.

If the same token is used on multiple HAs, enable **Receive commands** on exactly one. Only that server receives requests and configures Telegram’s native menu. The other servers can continue sending notifications. Requests are not synchronized between servers. After applying a permission change, reopen Telegram’s Menu; removed users lose their account-specific command list and all account access.

### Invite family members to a personal profile

1. Apply the personal profile first. For the first recipient, use the native picker below, `/connect`, or a manual ID.
2. Open the profile’s **Telegram** step and choose **🔗 Створити запрошення (Create invitation)**.
3. Copy the link and send it to the people you want to invite. They open it and press **Start** in Telegram. The link selects the profile for their request; no account number or address appears in the link or the unauthorised reply.
4. Review each request in that profile, choose **Add to this profile**, then **Apply**. All recipients share this profile’s account and permissions, including reading submission if enabled.

An invitation lasts seven days and can be used by several people. **Revoke invitation** blocks new requests through that link; it does not revoke already approved access or delete pending requests. Creating a new invitation for the same profile invalidates the previous link. Changing the account or bot also invalidates the old invitation. To remove a person’s access, remove their ID and apply the settings.

The full link is shown only after creation in the current editor tab. If you reload and need to copy it again, create a new invitation. Invite links are stored as hashes; generated links do not belong in a public issue or screenshot.

### Choose people, a group or a channel inside Telegram

1. On the profile’s **Telegram** step, choose **Select people in Telegram** for a personal profile. For building notifications, choose **Private chat**, **Group** or **Channel**.
2. Open the generated **Open selection in Telegram** link yourself. It lasts 15 minutes and binds to the first person who opens it. Use a separate invitation link when inviting someone else.
3. Press the selection button below the message in Telegram. You can select up to ten people at once, or use **My private chat**. For a group or channel, Telegram filters suitable chats and may ask you to add the bot and grant the requested rights.
4. Return to the editor. The selection appears beneath the link; polling never writes it into your form automatically. Check the name and ID, then **Add to this profile** and **Apply**. Personal recipients are appended; replacing a building destination asks for confirmation.

A selected person must open the bot and press **Start** before it can contact them. Until then, the editor explains what is missing and disables adding that selection. This does not itself give the person account access. For a channel the bot needs posting rights, plus editing rights when pinning is enabled. For a group with a pinned status, it needs pinning rights. Without pinning, add the bot as a member first; the picker verifies that messages are allowed. The person selecting a group/channel must be its administrator. Permission checks never send test posts to the selected chat.

**Cancel selection** closes the temporary Telegram keyboard and preserves any reading draft. Expired, revoked or already completed selectors cannot replace a result. Manual IDs and the discovery dropdown remain available if your Telegram client does not support native selection. Update the client if its selection button is missing.

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

Schedule and group-change notifications include an **All groups** image by default, as a reply to the text. Only changed dates are attached; large tables use albums. Disable **“Зображення «Усі групи»”** in the profile’s notification settings if you prefer text only. Automatic images remain when navigating the menu; unavailable images do not prevent the text notification.

Enable **Оновлювати останній графік за сьогодні** to keep the latest automatic graph’s time line current. The bot edits the same image every 5 minutes by default (configurable from 1 to 60); no new notification is posted. Yesterday’s images and manual menu screens stay unchanged. This option is off by default; enable it separately for each profile. Generation timestamps use **DD.MM.YYYY HH:MM**. [Details and limitations](../DOCS.md#notifications).

## Reminders

Enable reminders per personal profile. The bot checks during the integration's reading window: the last two days of a month and the first three days of the next, after 19:00 Kyiv time. It sends one reminder per period, with **Already submitted** and **Remind tomorrow** buttons. An accepted submission recorded by this HA within the window suppresses it; a reading sent elsewhere may require dismissing the reminder manually.

Dismissal only closes the reminder. Snoozing does not submit readings and stops when the window ends. HA cabinet reminders and Telegram reminders are separate switches. After an uncertain Telegram delivery, retries wait a day to avoid repeated posts.

## Introduction for other users

Edit the welcome text in **Templates → Привітання · welcome**, then save. Changes apply immediately and survive restarts and updates; **Reset** restores the standard introduction with links to both projects. You can replace the entire text, including its links. The only runtime variable is `{{ telegram_user_id }}` (the sender’s ID); no account or address data is passed to this public template. Telegram HTML formatting is supported. This template is shared by all bot instances on this add-on.

`welcome_enabled` is on by default and can be changed in the editor. A person without an assigned private profile who messages the bot receives a short Ukrainian explanation, links to both GitHub projects and their own Telegram ID. The response contains no configured addresses or account information. It is limited to once per user per 24 hours, with an additional limit of 20 introductions per minute per bot; the limit survives restarts.

There are no automatic introductions in groups/channels or unsolicited messages. A channel subscriber can receive this introduction when writing privately because channel membership does not authorize private account access. Pressing Start is not a subscription or registration flow.

The add-on uses one incoming-update collector per bot for both commands and test feedback. Do not run a separate `getUpdates` collector or webhook for the same token. If a webhook is already configured, the add-on leaves it intact; resolve the conflict before using commands.

## Testing and YAML

Keep building notification tests in a test group. For a **personal** profile, the bot's enforced test destination must be one of the authorized private users, or testing mode must be off. In test mode, only that person receives account messages; the other recipients are paused: configuration rejects routing cabinet details into a shared test channel. Template tests are real messages; review private content before choosing a destination. Prefer a dedicated test bot for account workflows. No readings are submitted by previewing a template or a schedule.

Normally use the editor. Equivalent minimal runtime YAML, with fictional identifiers:

```yaml
welcome_enabled: true
addresses:
  - profile_id: my_account
    mode: personal
    account_entity: sensor.example_account_updated
    telegram_bot_id: default
    telegram_user_ids: [123456789, 987654321]
    display_name: My apartment
    allow_readings: false
    reading_reminders: true
```

Select the actual account entity from discovery instead of guessing its ID. Leave `selected_group` empty. `profile_id` is a stable identity for state and delivery history; the editor/backend creates one when needed. Clone a building profile to send the same building to another destination with independent history; keep the clone's distinct ID. Existing profiles without IDs keep their previous state.

## Private menu for building members

Members of a configured group or channel can privately ask the same bot for **⚡️ Status**, **📅 Today’s schedule** and **🗓 Tomorrow’s schedule**. You do not need to enter their individual user IDs.

1. Make the bot an **administrator** in the building group or channel. Telegram requires this for reliable membership checks of other people.
2. In the editor, open the building profile → **Telegram**. Check its destination chat and enable **Особисте меню для учасників групи / каналу (Private menu for group/channel members)**. It is off by default and cannot be used with test-mode redirection.
3. Press **Apply**. Each member opens a private chat with the bot and presses **Start**. Telegram’s command menu and message buttons then provide navigation.

The bot checks membership on every interaction. Telegram does not provide a complete member list. Leaving or being removed from the chat revokes access, including through old buttons. If Telegram is unavailable or the bot loses administrator rights, the check does not grant access; the member can try again later.

For several configured chats, the bot asks which **address or account** to use before performing the requested action. Separately authorized personal accounts appear in the same selector with a distinct label. Building access provides status and schedules only: it does not expose readings, documents or a personal cabinet. **Status** remains available even when the group has a pinned status. Browsing a building keeps a reading draft for your own account.

This option **does not subscribe members to private notifications**: automatic messages still go to the configured shared chat. Private notifications and cabinet access require a separately authorized personal profile.

In YAML, set `member_access: true` on the building profile whose `telegram_chat_id` is a group/channel. All buildings and accounts serving private commands must be configured on the single HA receiving updates for this token. Profiles on another HA are not imported automatically; see the limitation below.

## One token on several HA servers

Both servers can send through the same bot. Keep `receive_commands: true` on the single server containing all profiles used for private commands (buildings and personal accounts). Set `receive_commands: false` on the others and restart the add-on. This disables incoming commands, introductions, personal reading reminders and feedback collection there; ordinary notifications continue. The switch is available in the editor. Never run two incoming collectors for the same token.

## If DTEK data is unavailable

Install **Monitor 2.0.0-rc7 or newer** and **Bot 0.5.0-rc7 or newer** for recovery from known meter-service authorization changes and clearer error messages. The integration checks supported formats automatically on reads; it never retries a meter submission automatically.

If a service is incompatible, the bot links to the latest integration release and the issue tracker. Open **Settings → System → Repairs** in HA for details. Update the integration first; if it is already current, report the problem without passwords, tokens or full account details. Sign in again only if HA specifically asks for authentication.

A failure of monthly consumption does not erase available reading history. Other account sections and verified schedule alerts continue when their own data is available. Missing data is shown as unavailable, not zero consumption or an empty history. If a submission result is uncertain, check its result before trying to send the readings again.

## Meter photos and DTEK documents

Available since **DTEK Monitor 2.0.0-rc4** and **Telegram Bot 0.5.0-rc4**; the current recommended pair is Monitor 2.0.0-rc8 + Bot 0.5.0-rc9. Update the integration first. Document retrieval remains experimental; the availability of a controller’s act through the cabinet is not yet verified.

After entering readings (or choosing a sensor estimate), press **📷 Додати фото** on the review screen. Select a tariff, then send one photo or a JPEG, PNG or WebP file. Check the preview and repeat for the second tariff if applicable. Albums are not accepted. If you use photos, every tariff needs its own photo; otherwise remove all photos to submit numerical readings only. The numbers must match the official meter in the photos, even when a sensor provided the estimate.

Originals may be up to 20 MB and 30 megapixels. The app removes EXIF metadata and converts each photo to JPEG, at most 2560 pixels on its longest side and 1 MB. Check that readings, tariff labels and the meter serial number remain legible. Return to photo selection to replace or remove a photo, then review and explicitly confirm the submission. Replacing photos invalidates previous confirmation buttons. Drafts expire after five minutes; cancellation, switching profiles or restarting the app discards them. Image bytes are not saved in the app's journal; Telegram retains the photos sent in chat under its own rules.

**📄 Документи ДТЕК** lists documents published in the selected account's official cabinet. Choose one to download its PDF. In HA, the same list is under **My DTEK → Readings → DTEK documents**. Only listed document IDs for that account can be downloaded. Downloading does not sign or accept an act.

Enable **Нові документи й акти ДТЕК** in a personal profile to check every 30 minutes and receive new PDFs in your private chat. Existing documents form the initial baseline and are not sent as new. Automatic notifications remain in chat when you navigate the menu. HA has a separate switch under **My DTEK → Settings → Meter notifications**. Files over 2 MB or unsupported formats produce a notification directing you to the official cabinet. Unknown Telegram delivery results are not automatically repeated; the document remains available from the menu.

A real single-zone Odesa reading with a photo was accepted and confirmed in DTEK history. Live multi-zone photo submission remains unverified. The list and file endpoints were identified in DTEK's official legacy cabinet. The list was checked against a real Odesa account, but was empty; a real act download and the appearance of an act after photo submission still need verification. An empty list is different from a service failure. An accepted photo submission does not itself prove that the controller's inspection is complete or an act has been issued. A document published only through DTEK's own chat bot may not appear in the cabinet list.

## DTEK announcements next to the schedule

With an updated integration, the status and schedule-change messages include a
short DTEK announcement note and a link to its original text. A PNG has a compact
announcement marker; its caption contains the source. Rechecking the same text
does not send another notification. The settings below let you choose where notices appear.

Read the territory and dates in the announcement: a message about part of the
region may not concern your building. The table remains available. Only a fresh,
unambiguous emergency declaration matching your territory/date pauses
upcoming-outage warnings and restoration estimates derived from the timetable.
An ambiguous, absent or unavailable announcement does not pause these forecasts.
A failed check is labelled separately; it never means “power on” or “power off”.
Schedule/group changes and measured power/voltage alerts keep working.

Update **both the integration and the add-on**. Older integrations retain their
previous behavior. In HA, open **Schedule → DTEK announcement** for the original
text and last successful check time (not publication time).
[How announcements are interpreted](https://github.com/groove-max/ha-dtek-monitor/blob/main/docs/SCHEDULES.md#regional-announcements-and-emergency-outages).

### Configure announcements for each profile

Open the app's web interface, edit a profile, and select **3. Сповіщення →
📢 Оголошення ДТЕК**. Settings apply only to that profile:

- **Використовувати оголошення ДТЕК** — master switch. Turning it off hides
  announcement notes in this bot profile and stops them pausing its forecasts.
- **Показувати в статусі** — include a note and source in the pinned status or
  the response to a status request.
- **Додавати до повідомлень про зміну графіка й групи** — include a short note
  in those existing messages. There is no separate announcement broadcast.
- **Позначати на зображеннях графіка** — show the PNG marker and caption source
  for both menu requests and automatic schedule photos.
- **Призупиняти прогнози за екстреного оголошення** — pause timetable-derived
  forecasts only for fresh, unambiguous matching emergency declarations.

All switches are on by default. Choose **Apply** to save and restart the app.
Turning the master switch off preserves the other choices for later. These
settings do not change announcement displays or sensors in the HA integration.
For YAML configuration, use `provider_notice.enabled`, `show_in_status`,
`show_in_schedule_messages`, `show_on_images`, and `pause_forecasts` under each
address's `provider_notice` block.

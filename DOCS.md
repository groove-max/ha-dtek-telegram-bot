# DTEK Telegram Bot user guide

**English** · [Українська](DOCS.uk.md) · [Installation and overview](README.md)

[DTEK Monitor](#dtek-monitor) · [Telegram](#telegram) · [Addresses and chats](#destinations) · [Configuration](#configuration) · [Power detection](#power) · [Notifications](#notifications) · [Templates](#templates) · [Testing](#testing) · [Updates](#updates)

<a id="dtek-monitor"></a>
## Connect DTEK Monitor

Bot **0.3.13** supports the public building entities from DTEK Monitor **1.7.0** and **2.0.0b1**. These projects have independent version numbers; the integration's beta does not require a beta bot. Both require HA **2026.3.0+**. Regional support comes from the integration: Odesa oblast, Kyiv oblast, Dnipropetrovsk oblast and Kyiv city.

| Your setup | What to select in the bot |
| --- | --- |
| An address configured directly in DTEK Monitor | Discover and select that building in the bot's web editor. |
| An Odesa personal account in Monitor 2.0 | First check the building link in **My DTEK → Settings**. A unique address match links automatically; resolve an ambiguous or missing link there. Then discover the public building in the bot. |
| Several personal accounts in the same building | Use the shared building entry once. The bot does not need a separate address for each account. |
| Accounts or addresses in different buildings | Add each discovered building as a separate bot address. |

The bot does not need your DTEK phone, password, account number or apartment number. It does not use the personal calendar, send readings, or deliver the integration's account reminders. Those features remain in [My DTEK](https://github.com/groove-max/ha-dtek-monitor/blob/v2.0.0b1/docs/PERSONAL_ACCOUNT.md), even when the bot's destination is a private conversation.

For a shared building chat, leave `selected_group` empty to include all building groups. For personal alerts, choose a confirmed group in the bot if needed. **The apartment group selected in My DTEK does not change the bot's selection**, and changing the bot's selection does not filter the building entities. If the building has one group, leaving the field empty follows it automatically.

Discovery uses the building's power/status/group entities and their common ID prefix. A cabinet account entity is not an address candidate. Keep these entity IDs consistent; renaming individual IDs can break discovery or notifications. A friendly display name can be changed in the bot without renaming HA entities.

<a id="monitor-upgrade"></a>
### Upgrade the integration to 2.0.0b1

This is an optional public beta. Working 1.7.0 installations can continue using the bot. However, 1.7.0 has a [known first-setup issue](https://github.com/groove-max/ha-dtek-monitor/issues/5): when no schedule is published, setup can retry indefinitely without creating the address entities. **2.0.0b1 fixes this case** and loads the address independently of schedule availability.

1. Create a full HA backup that includes integration files, `.storage`, app data, `dtek_telegram_bot.yaml` and template overrides.
2. In HACS, select **2.0.0b1** for DTEK Monitor and restart HA. If beta versions are hidden, follow the [beta installation guide](https://github.com/groove-max/ha-dtek-monitor/blob/v2.0.0b1/docs/BETA.md).
3. Keep the existing integration entry and bot addresses. Existing entity IDs, destinations, sensor bindings and group selections should remain unchanged.
4. Check that the integration entry is loaded, the bot reconnects to HA, and local sensors report fresh values. A missing published schedule alone is not an upgrade failure.
5. If you change bot settings, use **Save and restart**. For an optional delivery test, first choose a [test destination](#testing).

Version 2 migrates the integration's configuration format. **Returning to 1.7.0 requires restoring the pre-upgrade backup**, including configuration and old integration files; reinstalling old code alone is insufficient. See the integration's [rollback instructions](https://github.com/groove-max/ha-dtek-monitor/blob/v2.0.0b1/docs/BETA.md#return-to-the-previous-version).

<a id="schedule-availability"></a>
### Understand a missing schedule

In Monitor 2.0, check the building's **DTEK connection** and **Schedule availability** entities separately. A connection can be healthy while no daily schedule is published. Schedule availability describes today's data in Kyiv time for the relevant groups:

| State | Meaning |
| --- | --- |
| `current` | A complete daily schedule is verified and contains planned outage intervals. |
| `empty` | Complete daily data confirms no planned outages for that date and those groups. Emergency outages are still possible. |
| `partial` | Only some groups or parts of the day have verified daily coverage. |
| `forecast` | Only forecast/possible-window coverage is available; it does not confirm an empty daily schedule. |
| `missing` | There is not enough published coverage for the date/groups. |
| `unpublished` | The operator has disabled publication. |
| `unsupported` | The address is marked as served by an unsupported external operator. |
| `error` | A request failed or the response could not be validated. |

The bot also checks the requested notification interval and groups, so today's status does not guarantee coverage for tomorrow. It pauses unverified schedule/group/upcoming-outage messages and resumes checking automatically. A log entry such as `Schedule has not been verified; notifications paused` can therefore reflect missing publication, not a broken Telegram token. Do not select the **Last known schedule (view only)** calendar as a live notification source.

Power monitoring from fresh local sensors can continue while DTEK schedules are unavailable. The `dtek_only` mode still depends on DTEK address data and does not measure actual electricity.

<a id="telegram"></a>
## Set up Telegram

Create a bot with [@BotFather](https://t.me/BotFather), add it to your destination and keep its token private. Use a separate bot for this app to avoid competing update collectors.

| Destination | Setup |
| --- | --- |
| Private conversation | Open your bot and press **Start** before it can message you. |
| Group/supergroup | Add the bot and allow messages. Grant pinning rights if using a pinned status. |
| Channel | Add the bot as an administrator with posting/editing rights for its messages. |

### Simplest option: messages just for you

Create the bot, press **Start**, find the personal chat ID below and put it in `telegram_chat_id`. A personal ID is positive, for example `"123456789"`; keep negative IDs for groups/channels. Do not substitute your personal `@username` for the numeric ID.

Configure your address in HA and enable the notifications you want. `feedback_enabled` is not needed for normal outgoing messages. Pressing Start lets the bot contact you, but does not automatically register an address/subscription: this is an HA-configured notification bot, not a Telegram-command setup wizard.

If you do not need pinning, turn off `status_message.pin` or the entire `status_message.enabled` feature. `delivery_mode: pinned_edit` with `pin: false` still updates one ordinary message. In a group, this also avoids needing pin permission. [Telegram pinning permissions](https://core.telegram.org/bots/api#pinchatmessage).

Each address has one destination; automatic subscriptions for arbitrary users and selecting individual forum topics are not currently implemented. If a group upgrades to a supergroup and its ID changes, update the configured ID and restart the app.

<a id="destinations"></a>
### Multiple addresses, chats and bots

One installation can monitor several buildings with one or several Telegram bots. Existing addresses keep the **Default bot**, using the token and chat in the HA app Configuration tab. Every address selects one bot profile; an empty address chat uses that profile’s default chat. Each selected bot needs permission to send to its destination.

For example, this runtime configuration follows all groups for two buildings. The first uses the main chat; the second uses its own group. Replace the fictional entity prefixes through discovery and the example chat ID with your own:

```yaml
addresses:
  - entity_prefix: vulytsia_prykladna_12a
    display_name: Example building A
    selected_group: ""
  - entity_prefix: dtek_krem_0123456789abcdef
    display_name: Example building B
    selected_group: ""
    telegram_chat_id: "-1001234567890"
```

To use another bot:

1. Create it with @BotFather and add it to the intended group/channel, or press **Start** in a personal conversation.
2. Open the app’s **Telegram bots** tab. Enter a label, token and default chat ID. You can instead set the chat separately for each address.
3. Choose **Verify token and save bot**. The app calls Telegram `getMe` and displays the bot’s `@username`; this check does not send a message or verify chat permissions.
4. In **Settings**, choose that bot for an address. Use **Save and restart** to apply both profile and address changes.
5. For an optional delivery check, enter a **Test chat** in the profile and use **Templates → Send test**. The button sends only to that bot’s test chat. A missing or inaccessible test destination never falls back to a working group.

One profile can serve several addresses. Adding the same bot twice is rejected to avoid competing update collectors. A new token for the **same** bot can replace the stored token; a different bot needs a new profile. Leave the token blank while editing to keep it. Profiles in use by saved or running addresses cannot be deleted: first change those addresses, save and restart.

The runtime YAML stores only `telegram_bot_id` (`default` or the ID shown in the exported address), not additional bot tokens. Imports retain this reference: add the corresponding bot on the destination HA or choose another profile before saving. Missing profiles pause their addresses; they never silently switch to the default bot.

Clients, delivery journals and incoming-update collectors are separate for each bot. A Telegram error or rate limit for one bot does not stop the other bot workers. Status message IDs and notification acknowledgements are scoped to bot, chat, test/production mode and address. Switching bot or chat creates a new status; the old message stays in the old chat and can be removed manually. Rotating a token for the same bot preserves its status. The first upgrade imports the existing default bot’s state and journal automatically; keep a private backup before updating.

**Current limits:** one bot and destination per address. Fan-out to several recipients, forum topics and user-managed subscriptions are not implemented. Do not duplicate an `entity_prefix` to simulate subscriptions; validation rejects duplicates. Separate development installations still need separate runtime YAML files to avoid sharing `/homeassistant/dtek_telegram_bot.yaml`.

### Find the chat ID

If another trusted HA configuration already uses this chat, copy its numeric ID. Otherwise, obtain it directly from Telegram using your own bot:

1. Stop any app polling updates for this bot. Do this **before** enabling this app's feedback collector.
2. Send a new message: `/start` in a private conversation, `/start@YourBotUsername` in a group, or a normal administrator post in a channel.
3. Save the script below as `telegram_chat_id.py` on a computer with Python 3 and run `python3 telegram_chat_id.py`. It prompts for the token without echoing it and prints chat IDs/names. It does not send messages or acknowledge updates.
4. Copy the intended chat's ID, including its minus sign, into `telegram_chat_id`. Close the script before starting the app.

```python
import getpass
import json
import urllib.error
import urllib.request

bot_token = getpass.getpass("Bot token: ")
request = urllib.request.Request(
    "https://api.telegram.org/bot" + bot_token + "/getUpdates",
    data=b"timeout=0",
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
except urllib.error.URLError:
    raise SystemExit("Request failed. Check token, network and other collectors.") from None
seen = set()
for update in payload.get("result", []):
    message = update.get("message") or update.get("channel_post") or {}
    chat = message.get("chat", {})
    if "id" in chat and chat["id"] not in seen:
        seen.add(chat["id"])
        print(chat["id"], chat.get("title") or chat.get("username") or chat["type"])
if not seen:
    print("No chat found. Send a new message to the bot's destination and retry.")
```

An existing webhook conflicts with `getUpdates`; do not remove another service's webhook just to test this app. Use a dedicated bot. Telegram's [update API](https://core.telegram.org/bots/api#getupdates) and [bot privacy rules](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get) explain which new messages the bot receives. A bot is not a general chat-history reader.

<a id="configuration"></a>
## Where settings live

Settings are divided by purpose:

| Location | Settings |
| --- | --- |
| HA app **Configuration** tab | `telegram_bot_token`, `telegram_chat_id`, optional `test_chat_id`, `feedback_enabled` (default `false`). Restart after changing them. |
| App web editor | Addresses, bot selection, power/voltage rules, notifications and templates. Use **Save and restart** for runtime changes. |
| **Telegram bots** tab | Additional bot profiles, default/test chats and optional feedback. **Verify token and save bot** stores the profile; restart applies it to notifications. |

The editor stores runtime settings in `dtek_telegram_bot.yaml` in HA's configuration directory. Inside this app, its path is `/homeassistant/dtek_telegram_bot.yaml`; the same file may appear as `/config/dtek_telegram_bot.yaml` in an HA file editor. `/data/config.yaml` is a fallback; an existing fallback is copied when the preferred file is first available. Do not maintain conflicting copies.

`ha_token` is an optional runtime fallback for HA authentication. Leave it empty in a normal Supervisor installation. The editor’s JSON export omits the fallback HA token and all Telegram tokens, but includes addresses and chat IDs. Raw runtime YAML can contain the HA token. Inspect files before sharing.

### Editing safely

Background refresh updates live data without replacing fields you are editing. **Validate** checks the current draft and shows a snapshot; it does not save it. **Save** writes settings; **Save and restart** also applies them to the running app. If you keep typing during a save, those later edits remain unsaved.

Bot tokens stay only in the current form until submission and are never returned by the API or stored in browser draft storage. A background refresh preserves this form; closing/reloading the page discards unsaved profile fields. Profile saves also reject concurrent edits; use **Reload profile** to load the saved values before retrying.

Configuration and individual template drafts survive page reloads in the same browser tab when session storage is available. Closing the tab can remove them; export important configuration drafts before closing it. Template drafts remain separate when you switch templates or addresses. Preview uses the current text without saving it.

If another tab or a file edit changes the saved version, saving reports a conflict instead of overwriting it. Export your configuration draft (or copy the template text), choose **Load saved**, then reconcile your changes. Loading saved data explicitly discards the current draft after confirmation.

### Address fields

| Field | Meaning |
| --- | --- |
| `entity_prefix` | Common portion of the address's entity IDs. Use discovery; it is not necessarily the street name. |
| `display_name` | Friendly text in messages; empty lets the app resolve it from HA. |
| `telegram_bot_id` | Bot profile: `default` keeps the existing Supervisor token; use the editor to select an additional bot. |
| `telegram_chat_id` | Optional destination; empty uses the selected bot’s default chat. That bot’s test mode overrides both. |
| `selected_group` | Empty follows all address groups. Select only a known applicable group. |

Example: for `sensor.dtek_krem_0123456789abcdef_outage_status`, the prefix is `dtek_krem_0123456789abcdef`. If a saved selected group disappears from the address, schedule processing waits for a valid selection rather than guessing.

For operators with hidden group IDs, normally leave `selected_group` empty and use the displayed variants. Regional HTTP access and operator selection belong to [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor), not this app.

<a id="power"></a>
## Configure actual power detection

Use the [mode selection table](README.md#power-detection) first. The following examples use placeholder entity IDs; replace them through discovery and your HA device pages.

### One sensor: `voltage_only`

The sensor must measure grid-input **voltage in volts**, stay powered during an outage and report actual values, including zero. Its network/gateway and HA need backup power too. Configure periodic reporting even when voltage is unchanged. Do not put a frequency sensor in `voltage.entities`.

This is a complete runtime YAML example for one address. `voltage.enabled: false` disables separate voltage-quality alerts, not the measurements used for power detection:

```yaml
addresses:
  - entity_prefix: dtek_krem_0123456789abcdef
    display_name: Example building
    power:
      enabled: true
      mode: voltage_only
      loss_delay: 10
      restore_delay: 30
      telemetry_notifications: false
    voltage:
      enabled: false
      entities:
        - entity: sensor.inverter_ac_input_voltage
          label: L1
      present_above: 50
      max_age_seconds: 150
```

`loss_entity` and `loss_mqtt_topic` stay empty. `confirm_timeout` does not apply in this mode. If the sole sensor powers off with the mains, it remains usable for measuring presence, but its disappearance cannot establish an outage.

### Two sensors: `loss_plus_voltage`

Use a backed-up grid-loss sensor on the **same feed** plus a voltage meter such as ATORCH. The independent source must explicitly report grid loss: for example, an inverter's AC-input frequency below 1 Hz. Its unavailability is not a loss signal.

Replace the `power` and `voltage` sections within an address with this example:

```yaml
power:
  enabled: true
  mode: loss_plus_voltage
  loss_entity: sensor.inverter_ac_grid_frequency
  loss_state: "<1"
  loss_delay: 10
  confirm_timeout: 15
  restore_delay: 30
  telemetry_notifications: false
voltage:
  enabled: false
  entities:
    - entity: sensor.atorch_voltage
      label: L1
  present_above: 50
  max_age_seconds: 150
```

| Evidence | Result |
| --- | --- |
| Fresh inverter grid-loss signal + unavailable/frozen older ATORCH reading | Confirmed outage after confirmation and loss delays. Either event can arrive first. |
| Grid-loss signal + a new positive voltage after that signal | Contradiction: the trigger cannot override the fresh voltage. |
| ATORCH unavailable, inverter still reports grid present | Unknown local power; no false outage. |
| Both sources stale/unavailable | Unknown, not confirmed loss. |
| Inverter returns, ATORCH remains unavailable | Wait for fresh voltage before announcing restoration. |
| Fresh voltage returns and remains valid through the restore delay | Announce restoration if there was a previously confirmed outage. |

Repeated unavailable reports do not restart the timers or turn a confirmed outage into a temporary data warning while the independent loss evidence remains fresh.

### Timing and freshness

Values in the examples are starting points for a source reporting roughly once a minute, **not universal device recommendations or defaults**.

| Setting | Default | Purpose |
| --- | --- | --- |
| `power.loss_delay` | 30 s | Stable absence required before a power-loss message. |
| `power.confirm_timeout` | 15 s | Two-signal confirmation window before disregarding older voltage. |
| `power.restore_delay` | 10 s | Stable valid presence before recovery. |
| `voltage.max_age_seconds` | 600 s | Maximum usable sample age; tune to measured reporting cadence. |
| `voltage.present_above` | 50 V | Voltage at/above this software threshold counts as present. |
| `power.telemetry_notifications` | `false` | Separate data-loss/recovery notices. Status still updates when off. |
| `power.telemetry_loss_delay` | 20 s | Delay before an optional data-loss notice. |

Fresh low/zero voltage can establish absence. Missing, malformed, negative, non-finite, restored or stale readings cannot do so on their own. A 5-second watchdog checks silent expiry. Unchanged but newly reported values remain usable; repeated samples do not restart debounce. Unknown or contradictory evidence interrupts a pending transition.

With the two-signal example, allow about 15 + 10 seconds after receiving the independent loss signal, plus processing/watchdog time. Add the device's reporting interval to estimate latency from a physical outage. Do not confuse the freshness limit with the outage-confirmation delay.

HA's `last_reported` (or `last_updated` fallback) records HA reporting, not a guaranteed new measurement at the device. A cloud integration repeatedly republishing cached values can still be misleading. The old `unavailable_as_missing` flag is accepted for compatibility but no longer turns missing data into an outage.

### MQTT independent source

For an MQTT loss sensor, optionally set `power.loss_mqtt_topic` to its exact state topic. The app passively subscribes through HA, ignores retained replay and requires a fresh live payload matching the entity. Wildcards are not accepted. The HA connection must allow `mqtt/subscribe` (administrator access). No broker username/password is needed in this app.

Use the device's active topic, not an obsolete retained topic. If HA needs a numeric template, `{{ value | float(none) }}` handles numeric text/bytes without turning invalid payloads into zero. This is configured on the HA MQTT sensor, not in a Telegram template. See [HA MQTT sensor behavior](https://www.home-assistant.io/integrations/sensor.mqtt/).

### Multiple phases

Add voltage entities with labels such as `L1`, `L2`, `L3`. `on` means all configured phases are present; `off` requires evidence that all are absent. `partial` means some power is present with other phases absent or unknown; the status distinguishes missing and unknown phases. Uncertain evidence never becomes a phase-loss alert. Use a loss witness only if it describes the same monitored feed/topology.

A previously confirmed outage survives a later data gap, so a valid recovery can still notify. Durations are marked unknown when missing observations prevent a reliable calculation.

<a id="notifications"></a>
## Choose notifications for the chat

Each feature has an `enabled` setting. `silent` suppresses Telegram's notification sound, **not the message**.

| Section | Function / main controls |
| --- | --- |
| `schedule_change` | Updated published outage schedule; empty schedules are only announced when publication coverage is sufficient. |
| `group_change` | Changed address groups and their schedule; avoids a duplicate schedule message for the same change. |
| `emergency` | DTEK outage start/end, reason and ETA changes. These are operator reports. |
| `upcoming_outage` | `minutes` ahead (default 10); `power_filter`: `always`, `only_when_available` (default), `only_when_missing`. |
| `power` | Power loss/restoration according to the selected mode; optional `phase_notifications`. |
| `voltage` | Optional low/high/normal alerts: defaults `low: 195`, `high: 245`, `hysteresis: 5`, `delay: 15`. |
| `status_message` | `pinned_edit` (default) or `send_new`; `pin`, `silent`, `update_interval` (300 s), `min_update_interval` (10 s). |

Voltage recovery uses hysteresis: after low/high voltage, the value must move back inside the normal range by the configured margin. For shared chats, start with a pinned status and only the alerts residents need. If using `dtek_only`, enabling both DTEK outage messages and power messages can describe the same operator event twice; choose the desired categories.

Schedules and local readings remain separate. During a DTEK failure, actual power detection can continue; saved schedules cannot drive automatic schedule/group/upcoming warnings.

<a id="templates"></a>
## Change message wording

Open **Templates**, choose a template, preview it, edit and save an override. Reset removes the override and restores the built-in text. Sending a test publishes a real message only to the selected bot’s test chat. Configure it first; the button never uses the production chat as a fallback.

Templates use Jinja. Available context is shown in the editor and depends on the message. For group presentation, `group`, `groups`, `selected_group`, `old_group`, `new_group` already respect hidden labels; `groups_public` indicates whether public group numbers exist.

Keep separate lines for the heading, interval and explanatory notes. Jinja's whitespace trimming removes a newline after a block tag such as `{% endif %}`; preview the **rendered** text for all branches. An explicit `{{ '\n' }}` can preserve a required break.

Overrides are stored in `/data/templates/*.j2` and survive updates. Consequently, updating the app will not automatically fix an old custom template. `export_default_templates: true` copies defaults only when the files do not exist; leave it off unless you need those files.

<a id="testing"></a>
## Test without posting to the main chat

1. Choose a test destination: press **Start** in a private conversation, or add the bot to a test group/channel with the required permissions.
2. For the **Default bot**, set the Supervisor option `test_chat_id` and restart. This redirects all of that bot’s notifications to the test chat, preserving the existing behavior.
3. For an additional bot, set **Test chat** in its profile. **Send test** uses it immediately after the profile is saved. To redirect all notifications, also enable **Test mode**, then save and restart. Other bot profiles keep their own destinations.
4. In test mode, all sends, edits, pins and unpins use the test chat; only message IDs owned by its journal can be modified. Test mode requires a nonempty test chat.
5. Optional **Collect feedback** (`feedback_enabled`) records new posts visible to the bot in its test chat. It requires test mode, one collector per token and no webhook. Failure to start feedback is reported under **Telegram bots** without stopping other bots.
6. Stop any other instance using the same token. Journals are isolated under `/data/bots/<bot-id>/<mode>/telegram.sqlite`; address state is stored in separate route namespaces in `/data/state.json`.

Incoming feedback follows Telegram permissions/privacy. In a channel without a linked discussion, use administrator posts rather than expecting user replies. The collector does not import arbitrary old chat history.

The app keeps a local SQLite journal of outgoing text, API outcomes and relevant address/group context; feedback adds incoming messages. It retains up to 10,000 event records. Treat it as private data. Maintainers can inspect `telegram.sqlite` with read-only SQLite over SSH; this is delivery evidence, not proof that people read a message.

Explicit temporary send failures may be retried within a bounded window after rechecking relevance. Ambiguous network outcomes are recorded as uncertain rather than blindly resent. Telegram does not provide an exactly-once delivery guarantee here.

<a id="updates"></a>
## Backups, updates and moving to another HA

- Back up the app data and HA configuration, including `dtek_telegram_bot.yaml` and template overrides.
- Read the [changelog](CHANGELOG.md), update the app, then verify the active configuration and destination.
- To move addresses, export/import them or the configuration draft in the editor. This does not create HA entities or transfer any bot credentials. Configure the default token/chat separately, recreate additional profiles, check each address’s bot selection and remap entity IDs.
- Before leaving test mode, stop the test instance, clear `test_chat_id` for the default bot or disable **Test mode** for an additional profile, check its production destination and restart. Production resumes its own saved state; do not copy test message IDs or queued notifications into it.

The default Telegram token stays in Supervisor-managed options. Additional tokens are stored unencrypted in the app’s private `/data/telegram_bots.json` file with owner-only permissions (0600); they are not included in the editor’s JSON exports or diagnostics. App backups contain this private file. A fallback HA token may also be present in runtime YAML. Include these only in private backups. If a token is exposed, replace it through the service that issued it.

For unresolved problems, use the [troubleshooting table](README.md#troubleshooting), then open an issue with versions, mode, reporting cadence and sanitized relevant logs. Development details are in [Contributing](CONTRIBUTING.md).

# DTEK Telegram Bot user guide

**English** · [Українська](DOCS.uk.md) · [Installation and overview](README.md)

[Telegram](#telegram) · [Configuration](#configuration) · [Power detection](#power) · [Notifications](#notifications) · [Templates](#templates) · [Testing](#testing) · [Updates](#updates)

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

There are two configuration layers:

| Location | Settings |
| --- | --- |
| HA app **Configuration** tab | `telegram_bot_token`, `telegram_chat_id`, optional `test_chat_id`, `feedback_enabled` (default `false`). Restart after changing them. |
| App web editor | Addresses, power/voltage rules, notifications and template controls. Use **Save and restart** for address/runtime changes. |

The editor stores runtime settings in `dtek_telegram_bot.yaml` in HA's configuration directory. Inside this app, its path is `/homeassistant/dtek_telegram_bot.yaml`; the same file may appear as `/config/dtek_telegram_bot.yaml` in an HA file editor. `/data/config.yaml` is a fallback; an existing fallback is copied when the preferred file is first available. Do not maintain conflicting copies.

`ha_token` is an optional runtime fallback for HA authentication. Leave it empty in a normal Supervisor installation. Full configuration exports can contain this token and private address information; inspect them before sharing.

### Editing safely

Background refresh updates live data without replacing fields you are editing. **Validate** checks the current draft and shows a snapshot; it does not save it. **Save** writes settings; **Save and restart** also applies them to the running app. If you keep typing during a save, those later edits remain unsaved.

Configuration and individual template drafts survive page reloads in the same browser tab when session storage is available. Closing the tab can remove them; export important configuration drafts before closing it. Template drafts remain separate when you switch templates or addresses. Preview uses the current text without saving it.

If another tab or a file edit changes the saved version, saving reports a conflict instead of overwriting it. Export your configuration draft (or copy the template text), choose **Load saved**, then reconcile your changes. Loading saved data explicitly discards the current draft after confirmation.

### Address fields

| Field | Meaning |
| --- | --- |
| `entity_prefix` | Common portion of the address's entity IDs. Use discovery; it is not necessarily the street name. |
| `display_name` | Friendly text in messages; empty lets the app resolve it from HA. |
| `telegram_chat_id` | Optional per-address destination; empty uses the main chat. Test routing overrides both. |
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

Open **Templates**, choose a template, preview it, edit and save an override. Reset removes the override and restores the built-in text. Sending a test publishes a real message to the configured destination.

Templates use Jinja. Available context is shown in the editor and depends on the message. For group presentation, `group`, `groups`, `selected_group`, `old_group`, `new_group` already respect hidden labels; `groups_public` indicates whether public group numbers exist.

Keep separate lines for the heading, interval and explanatory notes. Jinja's whitespace trimming removes a newline after a block tag such as `{% endif %}`; preview the **rendered** text for all branches. An explicit `{{ '\n' }}` can preserve a required break.

Overrides are stored in `/data/templates/*.j2` and survive updates. Consequently, updating the app will not automatically fix an old custom template. `export_default_templates: true` copies defaults only when the files do not exist; leave it off unless you need those files.

<a id="testing"></a>
## Test without posting to the main chat

1. Choose a test destination: press **Start** in a private conversation, or add the bot to a test group/channel with the required permissions.
2. Set the app option `test_chat_id`. Restart before testing.
3. All sends, edits, pins and unpins now use that chat, including address overrides and UI tests. Only message IDs owned by the test journal can be edited/pinned.
4. Optionally enable `feedback_enabled` to collect new posts visible to the bot from that test chat. Use one collector per token; a webhook prevents this collector from starting.
5. Stop any production instance using the same bot while testing. Test state is isolated in `/data/test-<identity>/`.

Incoming feedback follows Telegram permissions/privacy. In a channel without a linked discussion, use administrator posts rather than expecting user replies. The collector does not import arbitrary old chat history.

The app keeps a local SQLite journal of outgoing text, API outcomes and relevant address/group context; feedback adds incoming messages. It retains up to 10,000 event records. Treat it as private data. Maintainers can inspect `telegram.sqlite` with read-only SQLite over SSH; this is delivery evidence, not proof that people read a message.

Explicit temporary send failures may be retried within a bounded window after rechecking relevance. Ambiguous network outcomes are recorded as uncertain rather than blindly resent. Telegram does not provide an exactly-once delivery guarantee here.

<a id="updates"></a>
## Backups, updates and moving to another HA

- Back up the app data and HA configuration, including `dtek_telegram_bot.yaml` and template overrides.
- Read the [changelog](CHANGELOG.md), update the app, then verify the active configuration and destination.
- To move addresses, export/import them or the configuration draft in the editor. This does not create the corresponding HA entities or move Supervisor options; configure the token/chat separately and remap entity IDs.
- Before leaving test mode, stop the test instance, clear `test_chat_id`, check the intended main destination and restart. Production resumes its own saved state; do not copy test message IDs or queued notifications into it.

The app stores Telegram credentials in Supervisor-managed options and may store a fallback HA token in runtime YAML. Include these only in private backups. If a token is exposed, replace it through the service that issued it.

For unresolved problems, use the [troubleshooting table](README.md#troubleshooting), then open an issue with versions, mode, reporting cadence and sanitized relevant logs. Development details are in [Contributing](CONTRIBUTING.md).

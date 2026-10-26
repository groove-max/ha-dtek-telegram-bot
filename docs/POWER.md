# Choose your power sensors

**English** · [Українська](POWER.uk.md) · [User guide](../DOCS.md#power)

Open a profile → **Визначення живлення** → **Датчики Home Assistant**. Select a grid-input sensor; the editor recognizes binary status, volts and hertz. It supports any device that supplies these HA entities, not a specific brand.

1. **Where does the power state come from?** Select your existing binary status/automation, voltage or frequency. The primary device, gateway and HA must remain available during an outage. An inverter's grid input is suitable; its backed-up output does not measure the city supply.
2. **Optional confirmation.** Choose a second, independent observation of the same line. Specify whether it keeps reporting during an outage or powers off with the grid. Two entities from one device/cloud do not automatically provide independent confirmation.
3. **Optional voltage display and alerts.** Configure this separately in **Напруга: показувати та попереджати**. For example, a binary sensor can determine power while another device supplies voltage for the status. Enabling quality alerts does not change which sources determine power.
4. Expand **Тип датчика та свіжість даних** only if you need polarity, reporting age, a custom threshold or a live MQTT topic. Press **Застосувати** to validate, save and restart.

The two presence sources describe one line. They never create a “partial power” state. For different phases, keep the compatible `voltage_only` or `loss_plus_voltage` scheme with voltage entities labeled L1/L2/L3.

**Both sources must observe the same power supply.** For example, a meter before a protective relay can still measure voltage while an inverter after that relay has no grid input. Those readings are compatible electrically, but cannot confirm one shared power state. Choose observations on the same phase and the same side of local protection. One reliable source is enough; for more complex logic, select a ready-made HA binary status. No brand-specific relay configuration is required in the bot. A sensor in one apartment does not establish the state of every phase in the building.

## A ready-made binary status

Select whether `on` or `off` means grid present. Only those two HA states count; `unknown`, `unavailable`, a missing entity or a restored placeholder means unknown power.

A status that changes only when the electricity changes may legitimately remain `on` for days. The default freshness policy trusts the HA state while it is available; it does not expire based on `last_changed`. Its source integration/automation must expose loss of communication as unavailable/unknown, rather than leave a frozen boolean. If it publishes periodic reports, choose heartbeat freshness and set an age limit instead. The bot cannot infer a missing connection from an unchanged valid boolean.

Example inside an address/profile:

```yaml
power:
  enabled: true
  mode: sources
  sources:
    - entity: binary_sensor.city_grid_present
      kind: binary
      present_state: "on"
      freshness: state
  loss_delay: 10
  restore_delay: 20
voltage:
  enabled: true
  entities:
    - entity: sensor.grid_voltage
  max_age_seconds: 180
  low: 195
  high: 245
```

The voltage section is optional. Its missing/low readings do not override the binary source's decision.

## One or two measurement sources

Use numeric voltage in **V** or frequency in **Hz**. Values must be finite and nonnegative. Default presence thresholds are 50 V and 1 Hz (presence at or above the threshold). These are separate from voltage quality limits: 170 V may mean “power present” and still generate a low-voltage warning.

Measurements need regular HA reports, including when the value is unchanged. The default maximum report age is 180 seconds per source. `last_reported` is used when available, with `last_updated` as a fallback; neither proves the device took a new physical measurement if a cloud integration replays a cached value. Choose a limit longer than normal reporting intervals. Old, future-dated, malformed or restored data is unknown. Periodic sources wait for reports from the current HA connection after reconnecting.

Two backed-up voltage sensors, two frequency sensors or a mixed pair use the same agreement rules. The second source is optional; remove it if an authoritative binary status alone should decide power.

Example: grid-input frequency plus a meter that loses power with the grid:

```yaml
power:
  enabled: true
  mode: sources
  sources:
    - entity: sensor.inverter_grid_frequency
      kind: frequency
      availability: always_on
      max_age_seconds: 180
    - entity: sensor.input_meter_voltage
      kind: voltage
      availability: mains_powered
      max_age_seconds: 180
  confirm_timeout: 15
  loss_delay: 10
  restore_delay: 30
  telemetry_notifications: false
```

Add the meter to `voltage.entities` separately if you also want to display its voltage or receive quality alerts.

| Observation | Result |
| --- | --- |
| All configured sources report present | Presence candidate; apply recovery delay. After a confirmed outage, periodic sources must report presence again at or after the end of that delay. |
| All configured sources explicitly report absent | Loss candidate; apply loss delay. |
| Fresh sources disagree | Unknown; cancel the pending physical transition. |
| Primary unavailable or stale | Unknown, even if the secondary disappears. |
| Secondary disappears while primary reports present | Unknown; no outage announcement. |
| Primary explicitly reports absent; previously observed mains-powered secondary stops reporting at that loss | Require explicit unavailability or an expired secondary report, plus `confirm_timeout`; then apply `loss_delay`. A still-fresh positive reading cannot confirm loss. |
| Secondary disappeared before the primary loss or was never observed present | Insufficient corroboration; unknown. |
| Secondary reports fresh presence after primary loss | Conflict; its later aging does not turn that contradiction into absence. |
| Primary recovers but secondary is still missing | Wait for agreement and `restore_delay` before announcing recovery. |

Repeated identical reports do not restart delays. Unknown evidence interrupts a pending transition; it does not invent an outage/recovery pair. Grace windows are not restored after an app restart or HA reconnect. Telemetry-loss/recovery posts are off by default; the status still shows unknown data.

With a 15-second grace and 10-second loss delay, explicit secondary unavailability can confirm loss in about 25 seconds **after the primary signal is received**, plus processing/polling time. If it keeps an old positive value, the bot also waits for that report to expire. Two explicit absence readings need only `loss_delay`.

Set `max_age_seconds` longer than the sensor’s normal reporting interval. A meter that normally reports once a minute has not disappeared after 15 seconds. For quick confirmation, use explicit absence/unavailability reports or one authoritative backed-up source; increasing confidence from silence necessarily takes longer.

After a confirmed outage, a single positive measurement is not enough to announce recovery. In `sources`, `voltage_only` and `loss_plus_voltage` modes, every periodic source used to confirm recovery must report presence at or after the recovery delay. Normal reporting intervals therefore add to the notification time. For example, with a 30-second delay and minute-based reports, recovery may take roughly a minute or more to confirm. An event-driven binary source does not need to repeat its unchanged state. Conflicting or missing independent readings leave power unknown instead of announcing recovery from voltage alone.

The bot checks current evidence again immediately before sending a power notification, including a retry after a definite Telegram rejection. If the state changed while the notification was being prepared, the old notification is cancelled and the new state goes through its normal confirmation delay. Brief dips that end before confirmation do not produce an outage/recovery pair. This only uses data already received by HA: a bot cannot detect recovery that the sensor has not reported yet, or make a once-a-minute sensor report faster.

For MQTT sources, the optional per-source `mqtt_topic` must be an exact topic carrying the same raw value as the HA entity. The existing HA MQTT observer ignores retained/replayed messages. A matching live report must also be within `max_age_seconds`, including for a binary source. JSON payloads require an HA sensor/topic that exposes the corresponding value; the bot does not apply arbitrary MQTT templates.

## Upgrading an existing setup

Existing sensor bindings, MQTT topics, delays and voltage limits are preserved. There is no silent conversion of old configurations. To switch, choose **Датчики Home Assistant**, select the primary/optional secondary and their availability, check voltage display separately, and use the live preview under additional actions before applying. Preview is a point-in-time check; it does not simulate the running grace/debounce timers.

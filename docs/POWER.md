# Choose your power sensors

**English** · [Українська](POWER.uk.md) · [User guide](../DOCS.md#power)

Open a profile → **Визначення живлення** → **Датчики Home Assistant**. Select a grid-input sensor; the editor recognizes binary status, volts and hertz. It supports any device that supplies these HA entities, not a specific brand.

1. **Where does the power state come from?** Select your existing binary status/automation, voltage or frequency. The primary device, gateway and HA must remain available during an outage. An inverter's grid input is suitable; its backed-up output does not measure the city supply.
2. **Optional confirmation.** Choose a second, independent observation of the same line. Specify whether it keeps reporting during an outage or powers off with the grid. Two entities from one device/cloud do not automatically provide independent confirmation.
3. **Optional voltage display and alerts.** Configure this separately in **Напруга: показувати та попереджати**. For example, a binary sensor can determine power while another device supplies voltage for the status. Enabling quality alerts does not change which sources determine power.
4. Expand **Тип датчика та свіжість даних** only if you need polarity, reporting age, a custom threshold or a live MQTT topic. Press **Застосувати** to validate, save and restart.

The two presence sources describe one line. They never create a “partial power” state. For different phases, keep the compatible `voltage_only` or `loss_plus_voltage` scheme with voltage entities labeled L1/L2/L3.

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
| All configured sources report present | Presence candidate; apply recovery delay. |
| All configured sources explicitly report absent | Loss candidate; apply loss delay. |
| Fresh sources disagree | Unknown; cancel the pending physical transition. |
| Primary unavailable or stale | Unknown, even if the secondary disappears. |
| Secondary disappears while primary reports present | Unknown; no outage announcement. |
| Primary explicitly reports absent; previously observed mains-powered secondary stops reporting at that loss | After `confirm_timeout`, its silence can corroborate absence; then apply `loss_delay`. |
| Secondary disappeared before the primary loss or was never observed present | Insufficient corroboration; unknown. |
| Secondary reports fresh presence after primary loss | Conflict; its later aging does not turn that contradiction into absence. |
| Primary recovers but secondary is still missing | Wait for agreement and `restore_delay` before announcing recovery. |

Repeated identical reports do not restart delays. Unknown evidence interrupts a pending transition; it does not invent an outage/recovery pair. Grace windows are not restored after an app restart or HA reconnect. Telemetry-loss/recovery posts are off by default; the status still shows unknown data.

With a 15-second grace and 10-second loss delay, an inferred loss needs about 25 seconds **after the primary signal is received**, plus processing/polling time. Device reporting latency adds to this. A freshness limit is not a mandatory wait before every outage.

Choose `confirm_timeout` longer than the secondary sensor’s normal reporting interval when using silence as confirmation. A meter that normally reports once a minute cannot reliably corroborate loss by silence after only 15 seconds. If fast trustworthy confirmation is unavailable, use an authoritative backed-up source alone or require two explicit readings.

For MQTT sources, the optional per-source `mqtt_topic` must be an exact topic carrying the same raw value as the HA entity. The existing HA MQTT observer ignores retained/replayed messages. A matching live report must also be within `max_age_seconds`, including for a binary source. JSON payloads require an HA sensor/topic that exposes the corresponding value; the bot does not apply arbitrary MQTT templates.

## Upgrading an existing setup

Existing sensor bindings, MQTT topics, delays and voltage limits are preserved. There is no silent conversion of old configurations. To switch, choose **Датчики Home Assistant**, select the primary/optional secondary and their availability, check voltage display separately, and use the live preview under additional actions before applying. Preview is a point-in-time check; it does not simulate the running grace/debounce timers.

"""Virtual-time replay of evidence changes, independently of timer scheduling."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import test_power_presence_feature as fixtures


class PowerTransitionTests(unittest.IsolatedAsyncioTestCase):
    prefix = fixtures.PowerPresenceFeatureTest.prefix
    voltage_entity = fixtures.PowerPresenceFeatureTest.voltage_entity
    loss_entity = fixtures.PowerPresenceFeatureTest.loss_entity
    _build_feature = fixtures.PowerPresenceFeatureTest._build_feature

    async def asyncSetUp(self):
        self.feature, self.monitor, self.ha, self.tg, self.state = self._build_feature()
        self.time = 0
        self.base = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.monitor._loss_fresh_after = self.base
        self.feature._monotonic = lambda: self.time
        self.feature._arm = lambda deadlines: None
        self.feature.config.power.mode = "voltage_only"
        self.feature.config.power.loss_delay = 10
        self.feature.config.power.restore_delay = 30
        self.feature.config.power.telemetry_loss_delay = 20
        self.feature.config.power.telemetry_notifications = True
        self.feature.config.voltage.max_age_seconds = 150
        self.ha.is_connected = True
        self.state.set(self.prefix, "power_state", "on")
        self.state.set(self.prefix, "power_last_confirmed_state", "on")
        self.state.set(self.prefix, "power_last_change", self.base.isoformat())
        self.patches = [
            patch(module + ".now_kyiv", self.now)
            for module in ["power_monitor", "features.power_presence"]
        ]
        for p in self.patches:
            p.start()
        self.put("230")
        await self.feature.on_tick()

    async def asyncTearDown(self):
        await self.feature.on_stop()
        for p in self.patches:
            p.stop()

    def now(self):
        return self.base + timedelta(seconds=self.time)

    def put(self, value, entity=None, **extra):
        self.ha.states[entity or self.voltage_entity] = {
            "state": value,
            "last_updated": self.now().isoformat(),
            **extra,
        }

    async def step(self, time, value=None, **extra):
        self.time = time
        if value is not None:
            self.put(value, **extra)
        await self.feature.on_tick()

    def messages(self):
        return [m.split("|")[0] for m in self.tg.messages]

    def power(self):
        return self.state.get(self.prefix, "power_state")

    async def test_single_backed_up_voltage_sensor_confirms_loss_and_restore(self):
        # No independent witness or DTEK entity is available in this setup.
        self.ha.states = {self.voltage_entity: self.ha.states[self.voltage_entity]}
        self.feature.config.power.loss_entity = ""
        self.feature.config.voltage.enabled = False
        self.feature.config.power.telemetry_notifications = False
        await self.step(1, "0")
        await self.step(10, "0")
        self.assertEqual(self.messages(), [])
        await self.step(11, "0")
        self.assertEqual(self.power(), "off")
        self.assertEqual(self.messages(), ["power_lost"])
        await self.step(60, "0")
        await self.step(61, "230")
        await self.step(90, "231")
        self.assertEqual(self.messages(), ["power_lost"])
        await self.step(91, "230")
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), ["power_lost", "power_restored"])

    async def test_single_sensor_disappearance_and_return_cannot_prove_outage(self):
        self.ha.states = {self.voltage_entity: self.ha.states[self.voltage_entity]}
        self.feature.config.power.loss_entity = ""
        self.feature.config.power.telemetry_notifications = False
        await self.step(151)
        self.assertEqual(self.power(), "unknown")
        await self.step(300, "unavailable")
        await self.step(600, "230")
        await self.step(630, "230")
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), [])

    async def test_brief_unavailable_does_not_invent_an_outage_or_restore(self):
        await self.step(1, "unavailable")
        await self.step(7, "230")
        await self.step(37)
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), [])

    async def test_long_unavailable_sends_one_uncertainty_and_one_data_recovery(self):
        await self.step(1, "unavailable")
        for t in [5, 10, 20, 21, 40, 80]:
            await self.step(t, "unavailable")
        self.assertEqual(self.power(), "unknown")
        self.assertEqual(self.messages(), ["power_telemetry_lost"])
        await self.step(81, "231")
        await self.step(110)
        self.assertEqual(self.messages(), ["power_telemetry_lost"])
        await self.step(111)
        self.assertEqual(
            self.messages(), ["power_telemetry_lost", "power_telemetry_restored"]
        )
        self.assertEqual(self.power(), "on")

    async def test_repeated_zero_reports_do_not_extend_loss_deadline(self):
        for t in [1, 3, 5, 8, 10]:
            await self.step(t, "0")
            self.assertEqual(self.messages(), [])
        await self.step(11, "0")
        await self.step(60, "0")
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_zero_voltage_with_live_grid_replays_false_alert_without_messages(self):
        # Replay a 56-second zero reading while the inverter keeps reporting 50 Hz.
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.telemetry_notifications = False
        self.time = 1
        self.put("50", self.loss_entity)
        await self.step(1, "0")
        for t in (11, 16, 26, 31, 56):
            self.time = t
            self.put("49.9", self.loss_entity)
            await self.step(t)
            self.assertEqual(self.power(), "unknown")
            self.assertEqual(self.messages(), [])
        await self.step(57, "224.63")
        await self.step(87)
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.state.get(self.prefix, "power_last_confirmed_state"), "on")

    async def test_two_fresh_zero_readings_still_confirm_after_loss_delay(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.telemetry_notifications = False
        self.time = 1
        self.put("0", self.loss_entity)
        await self.step(1, "0")
        await self.step(10)
        self.assertEqual(self.messages(), [])
        await self.step(11)
        self.assertEqual(self.power(), "off")
        self.assertEqual(self.messages(), ["power_lost"])
        self.time = 20
        self.put("50", self.loss_entity)
        await self.step(20, "230")
        await self.step(50)
        self.assertEqual(self.messages(), ["power_lost", "power_restored"])

    async def test_zero_voltage_waits_for_loss_witness_before_starting_debounce(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.telemetry_notifications = False
        self.time = 1
        self.put("50", self.loss_entity)
        await self.step(1, "0")
        await self.step(20)
        self.assertEqual(self.messages(), [])
        self.time = 25
        self.put("0", self.loss_entity)
        await self.step(25)
        await self.step(34)
        self.assertEqual(self.messages(), [])
        await self.step(35)
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_loss_witness_recovery_cancels_pending_zero_voltage_outage(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.telemetry_notifications = False
        self.time = 1
        self.put("0", self.loss_entity)
        await self.step(1, "0")
        self.time = 9
        self.put("50", self.loss_entity)
        await self.step(9)
        await self.step(31)
        self.assertEqual(self.power(), "unknown")
        self.assertEqual(self.messages(), [])

    async def test_stale_loss_witness_cannot_confirm_fresh_zero_voltage(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.telemetry_notifications = False
        self.time = 1
        self.put("0", self.loss_entity)
        # The voltage sample is fresh but the independent witness has expired.
        await self.step(152, "0")
        await self.step(180, "0")
        self.assertEqual(self.power(), "unknown")
        self.assertEqual(self.messages(), [])

    async def test_short_zero_dip_cancels_loss(self):
        await self.step(1, "0")
        await self.step(8, "230")
        await self.step(50)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.power(), "on")

    async def test_unknown_breaks_pending_outage_continuity(self):
        await self.step(1, "0")
        await self.step(9, "unknown")
        await self.step(10, "0")
        await self.step(19)
        self.assertEqual(self.messages(), [])
        await self.step(20)
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_confirmed_off_history_survives_unknown_gap(self):
        await self.step(1, "0")
        await self.step(11)
        await self.step(12, "unknown")
        await self.step(32)
        await self.step(40, "230")
        await self.step(70)
        self.assertEqual(
            self.messages(), ["power_lost", "power_telemetry_lost", "power_restored"]
        )

    async def test_restore_requires_continuous_good_observations(self):
        await self.step(1, "0")
        await self.step(11)
        await self.step(20, "230")
        await self.step(45, "unknown")
        await self.step(46, "230")
        await self.step(50)
        self.assertEqual(self.messages(), ["power_lost"])
        await self.step(76)
        self.assertEqual(self.messages(), ["power_lost", "power_restored"])

    async def test_silent_expiry_warns_without_any_ha_state_event(self):
        await self.step(149)
        self.assertEqual(self.power(), "on")
        await self.step(151)
        self.assertEqual(self.power(), "unknown")
        await self.step(171)
        self.assertEqual(self.messages(), ["power_telemetry_lost"])

    async def test_constant_value_with_new_report_is_fresh(self):
        await self.step(
            140,
            "230",
            last_updated=self.base.isoformat(),
            last_reported=self.now().isoformat(),
        )
        self.time = 280
        self.put(
            "230",
            last_updated=self.base.isoformat(),
            last_reported=self.now().isoformat(),
        )
        await self.feature.on_tick()
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), [])

    async def test_invalid_samples_never_confirm_power_loss_even_with_legacy_flag(self):
        for value in ["unavailable", "unknown", "", "garbage", "NaN", "inf", "-1"]:
            with self.subTest(value=value):
                self.put(value)
                self.assertEqual(await self.monitor.detect_current_power(), "unknown")
        self.put("230", attributes={"restored": True})
        self.assertEqual(await self.monitor.detect_current_power(), "unknown")
        self.put("230", last_updated=(self.now() + timedelta(minutes=10)).isoformat())
        self.assertEqual(await self.monitor.detect_current_power(), "unknown")
        self.ha.states.pop(self.voltage_entity)
        self.assertEqual(await self.monitor.detect_current_power(), "unknown")

    async def test_ha_disconnect_cancels_pending_loss_and_never_reads_rest(self):
        await self.step(1, "0")
        self.ha.is_connected = False

        async def forbidden(entity):
            self.fail("REST must not run while disconnected")

        with patch.object(self.ha, "get_state", forbidden):
            await self.step(11)
            self.assertEqual(self.power(), "unknown")
            self.assertEqual(await self.monitor.get_power_state(), "unknown")
        self.ha.is_connected = True
        await self.step(12, "230")
        await self.step(42)
        self.assertEqual(self.messages(), [])

    async def test_explicit_independent_loss_with_cached_voltage_confirms(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.time = 1
        self.put("0", self.loss_entity)
        await self.step(1)
        await self.step(16)
        self.assertEqual(self.messages(), [])
        await self.step(26)
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_missing_stale_or_restored_loss_signal_cannot_confirm(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        for value, extra in [
            ("unavailable", {}),
            ("unknown", {}),
            ("0", {"attributes": {"restored": True}}),
            ("0", {"last_updated": (self.now() - timedelta(seconds=151)).isoformat()}),
        ]:
            self.put(value, self.loss_entity, **extra)
            self.assertFalse(await self.monitor.loss_condition_met())
        await self.step(1, "unavailable")
        await self.step(21)
        self.assertEqual(self.messages(), ["power_telemetry_lost"])

    async def test_loss_signal_recovery_cancels_confirmation(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.time = 1
        self.put("0", self.loss_entity)
        await self.step(1)
        self.time = 10
        self.put("50", self.loss_entity)
        await self.step(10)
        await self.step(40)
        self.assertEqual(self.messages(), [])

    async def test_restart_discards_old_confirmation_but_preserves_real_outage(self):
        self.state.set(self.prefix, "loss_stale_reference_at", self.base.isoformat())
        self.state.set(self.prefix, "power_state", "off")
        self.state.set(self.prefix, "power_last_confirmed_state", "off")
        await self.feature.on_start()
        self.assertIsNone(self.state.get(self.prefix, "loss_stale_reference_at"))
        self.assertEqual(self.messages(), [])
        await self.step(30)
        self.assertEqual(self.messages(), ["power_restored"])

    async def test_phase_loss_uses_validated_samples_and_debounce(self):
        cfg = self.feature.config.voltage.entities
        cfg.append(SimpleNamespace(entity="sensor.l2", label="L2"))
        self.feature._voltage_entities.append("sensor.l2")
        self.put("230", "sensor.l2")
        self.state.set(self.prefix, "phase_state::sensor.l2", "on")
        await self.step(1, "unavailable", entity="sensor.l2")
        await self.step(30)
        self.assertNotIn("phase_lost", self.messages())
        await self.step(31, "0", entity="sensor.l2")
        await self.step(39)
        self.assertNotIn("phase_lost", self.messages())
        await self.step(41)
        self.assertEqual(self.messages(), ["phase_lost"])

    async def test_shutdown_cancels_pending_work(self):
        await self.step(1, "0")
        await self.feature.on_stop()
        await self.step(20)
        self.assertEqual(self.messages(), [])

    async def test_fresh_loss_signal_confirms_unavailable_voltage_after_window(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.time = 1
        self.put("0", self.loss_entity)
        await self.step(1, "unavailable")
        await self.step(16)
        await self.step(26)
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_atorch_disappears_before_inverter_reports_grid_loss(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.feature.config.power.telemetry_notifications = False
        await self.step(1, "unavailable")
        self.assertEqual(self.power(), "unknown")
        self.time = 8
        self.put("0", self.loss_entity)
        await self.step(8)
        for t in [12, 20, 23, 28, 32]:
            await self.step(t, "unavailable")
            self.assertEqual(self.messages(), [])
        await self.step(33, "unavailable")
        self.assertEqual(self.power(), "off")
        self.assertEqual(self.messages(), ["power_lost"])
        # Continuing unavailability cannot demote a confirmed outage while
        # the independent sensor keeps reporting measured grid loss.
        for t in [60, 120, 180, 240]:
            self.time = t
            self.put("0", self.loss_entity)
            await self.step(t, "unavailable")
            self.assertEqual(self.power(), "off")
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_inverter_reports_grid_loss_before_atorch_disappears(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.feature.config.power.telemetry_notifications = False
        self.time = 1
        self.put("0", self.loss_entity)
        await self.step(1)
        await self.step(8, "unavailable")
        await self.step(16)
        await self.step(25, "unavailable")
        self.assertEqual(self.messages(), [])
        await self.step(26)
        self.assertEqual(self.power(), "off")
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_atorch_disappearance_with_grid_present_is_not_an_outage(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.feature.config.power.telemetry_notifications = False
        for t in [1, 20, 40, 100, 160, 220]:
            self.time = t
            self.put("50", self.loss_entity)
            await self.step(t, "unavailable")
            self.assertEqual(self.power(), "unknown")
        await self.step(221, "230")
        await self.step(251)
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), [])

    async def test_inverter_recovery_alone_cannot_restore_atorch_outage(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.feature.config.power.telemetry_notifications = False
        self.time = 1
        self.put("0", self.loss_entity)
        for t in [1, 16, 26]:
            await self.step(t, "unavailable")
        self.assertEqual(self.messages(), ["power_lost"])
        self.time = 40
        self.put("50", self.loss_entity)
        await self.step(40)
        await self.step(70)
        self.assertEqual(self.power(), "unknown")
        self.assertEqual(self.messages(), ["power_lost"])
        await self.step(71, "230")
        await self.step(100, "230")
        self.assertEqual(self.messages(), ["power_lost"])
        await self.step(101)
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), ["power_lost", "power_restored"])

    async def test_loss_signal_expiry_cancels_pending_confirmation(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.time = 1
        self.put("0", self.loss_entity)
        await self.step(1)
        await self.step(16)
        self.put(
            "0",
            self.loss_entity,
            last_updated=(self.now() - timedelta(seconds=151)).isoformat(),
        )
        await self.step(26)
        self.assertEqual(self.messages(), [])
        self.assertIsNone(self.state.get(self.prefix, "loss_stale_reference_at"))

    async def test_old_positive_reading_cannot_restore_after_independent_loss(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.feature.config.power.confirm_timeout = 15
        self.time = 1
        self.put("0", self.loss_entity)
        for t in [1, 16, 26, 60, 90]:
            await self.step(t)
        self.assertEqual(self.messages(), ["power_lost"])
        self.assertEqual(self.power(), "off")

    async def test_phase_restore_does_not_duplicate_whole_house_restore(self):
        self.feature.config.voltage.entities.append(
            SimpleNamespace(entity="sensor.l2", label="L2")
        )
        self.feature._voltage_entities.append("sensor.l2")
        self.state.set(self.prefix, "phase_state::sensor.l2", "on")
        self.state.set(self.prefix, "phase_state::" + self.voltage_entity, "on")
        self.put("0", "sensor.l2")
        await self.step(1, "0")
        await self.step(11)
        self.put("230", "sensor.l2")
        await self.step(20, "230")
        await self.step(50)
        self.assertEqual(self.messages(), ["power_lost", "power_restored"])

    async def test_failed_send_is_retried_and_does_not_hide_live_power_state(self):
        original = self.tg.send_message

        async def failed(*args, **kwargs):
            return None

        self.tg.send_message = failed
        await self.step(1, "0")
        await self.step(11)
        self.assertEqual(self.power(), "off")
        self.tg.send_message = original
        await self.step(12)
        self.assertEqual(self.messages(), ["power_lost"])
        await self.step(13)
        self.assertEqual(self.messages(), ["power_lost"])

    async def test_deleted_entity_is_unknown(self):
        self.ha.states.pop(self.voltage_entity)
        await self.feature.on_state_change(self.voltage_entity, {"state": "230"}, {})
        self.assertEqual(self.power(), "unknown")
        await self.step(20)
        self.assertEqual(self.messages(), ["power_telemetry_lost"])

    async def test_watchdog_reconciles_without_external_tick(self):
        import asyncio

        self.feature.CHECK_INTERVAL = 0.005
        await self.feature.on_start()
        self.ha.states.pop(self.voltage_entity)
        self.time = 1
        await asyncio.sleep(0.02)
        self.assertEqual(self.power(), "unknown")
        self.time = 21
        await asyncio.sleep(0.02)
        self.assertEqual(self.messages(), ["power_telemetry_lost"])

    async def test_power_notification_identity_is_stable_while_retrying(self):
        keys = []

        async def failed(text, **kwargs):
            keys.append(kwargs.get("delivery_key"))
            return None

        self.tg.send_message = failed
        await self.step(1, "0")
        await self.step(11)
        await self.step(80, "1")
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])

    async def test_duration_is_unknown_after_a_gap(self):
        await self.step(1, "0")
        await self.step(11)
        await self.step(12, "unknown")
        await self.step(20, "230")
        await self.step(50)
        self.assertIn("невідомо", self.tg.messages[-1])

    async def test_presence_threshold_boundary(self):
        for value, expected in [
            ("0", "off"),
            ("49.9", "off"),
            ("50", "on"),
            ("50.1", "on"),
        ]:
            self.put(value)
            self.assertEqual(await self.monitor.detect_current_power(), expected)

    async def test_cached_zero_before_start_or_ha_reconnect_is_not_a_loss_signal(self):
        self.feature.config.power.mode = "loss_plus_voltage"
        self.put("0", self.loss_entity)
        self.assertFalse(await self.monitor.loss_condition_met())
        self.time = 1
        self.put("0", self.loss_entity)
        self.assertTrue(await self.monitor.loss_condition_met())
        self.time = 2
        self.ha.connected_at = self.now()
        self.assertFalse(await self.monitor.loss_condition_met())
        self.time = 3
        self.put("0", self.loss_entity)
        self.assertTrue(await self.monitor.loss_condition_met())

    async def test_telemetry_notices_disabled_but_status_still_updates(self):
        from unittest.mock import AsyncMock

        status = SimpleNamespace(request_update=AsyncMock())
        self.feature.set_status_message(status)
        self.feature.config.power.telemetry_notifications = False
        await self.step(1, "unavailable")
        await self.step(21)
        self.assertEqual(self.power(), "unknown")
        self.assertTrue(status.request_update.called)
        await self.step(30, "230")
        await self.step(60)
        self.assertEqual(self.power(), "on")
        self.assertEqual(self.messages(), [])
        self.assertGreaterEqual(status.request_update.await_count, 2)
        await self.step(61, "0")
        await self.step(71)
        await self.step(80, "230")
        await self.step(110)
        self.assertEqual(self.messages(), ["power_lost", "power_restored"])

    async def test_disabling_notices_also_suppresses_pending_data_recovery(self):
        self.state.set(self.prefix, "power_telemetry_alert", True)
        self.feature.config.power.telemetry_notifications = False
        await self.step(1, "unknown")
        await self.step(2, "230")
        await self.step(32)
        self.assertEqual(self.messages(), [])
        self.assertFalse(self.state.get(self.prefix, "power_telemetry_alert"))

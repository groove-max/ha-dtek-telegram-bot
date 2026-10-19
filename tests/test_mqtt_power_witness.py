"""Retained broker payloads must never masquerade as live power evidence."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import test_power_presence_feature as fixtures
from ha_client import HAClient
from config import PowerConfig
from pydantic import ValidationError


class MQTTWitnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = HAClient(ha_token="test")
        self.client._connected.set()
        self.client._mqtt_subscriptions[7] = "grid/frequency"

    async def receive(self, payload, retain=False, topic="grid/frequency"):
        await self.client._handle_message(
            {
                "id": 7,
                "type": "event",
                "event": {
                    "topic": topic,
                    "payload": payload,
                    "retain": retain,
                },
            }
        )

    async def test_retained_zero_is_ignored(self):
        await self.receive("0", retain=True)
        self.assertIsNone(self.client.mqtt_observation("grid/frequency"))
        await self.receive("0")
        self.assertEqual(self.client.mqtt_observation("grid/frequency")["state"], "0")

    async def test_retained_replay_invalidates_previous_live_message(self):
        await self.receive("0")
        await self.receive("0", retain=True)
        self.assertIsNone(self.client.mqtt_observation("grid/frequency"))

    async def test_disconnected_client_cannot_reuse_observation(self):
        await self.receive("0")
        self.client._connected.clear()
        self.assertIsNone(self.client.mqtt_observation("grid/frequency"))

    async def test_mismatched_topic_is_not_trusted(self):
        await self.receive("0", topic="obsolete/frequency")
        self.assertIsNone(self.client.mqtt_observation("grid/frequency"))

    async def test_subscription_failure_invalidates_witness(self):
        await self.receive("0")
        await self.client._handle_message({"id": 7, "type": "result", "success": False})
        self.assertIsNone(self.client.mqtt_observation("grid/frequency"))

    async def test_monitor_requires_live_matching_finite_fresh_payload(self):
        feature, monitor, ha, _, _ = (
            fixtures.PowerPresenceFeatureTest()._build_feature()
        )
        feature.config.power.loss_mqtt_topic = "grid/frequency"
        ha.mqtt_observation = self.client.mqtt_observation
        monitor._loss_fresh_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        ha.states[feature.config.power.loss_entity] = {
            "state": "0",
            "last_reported": datetime.now(timezone.utc).isoformat(),
        }
        self.assertFalse(await monitor.loss_condition_met())

        await self.receive("0", retain=True)
        self.assertFalse(await monitor.loss_condition_met())
        await self.receive("50")
        self.assertFalse(await monitor.loss_condition_met())
        await self.receive("0")
        self.assertTrue(await monitor.loss_condition_met())
        ha.states[feature.config.power.loss_entity]["state"] = "50"
        self.assertFalse(await monitor.loss_condition_met())
        ha.states[feature.config.power.loss_entity]["state"] = "0"
        self.client._mqtt_observations["grid/frequency"]["last_reported"] = (
            datetime.now(timezone.utc) - timedelta(seconds=601)
        ).isoformat()
        self.assertFalse(await monitor.loss_condition_met())

    async def test_zero_voltage_requires_live_matching_mqtt_loss(self):
        feature, monitor, ha, _, _ = (
            fixtures.PowerPresenceFeatureTest()._build_feature()
        )
        feature.config.power.loss_mqtt_topic = "grid/frequency"
        ha.mqtt_observation = self.client.mqtt_observation
        monitor._loss_fresh_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        for entity in (feature.config.power.loss_entity, feature._voltage_entities[0]):
            ha.states[entity] = {
                "state": "0",
                "last_reported": datetime.now(timezone.utc).isoformat(),
            }
        self.assertEqual(await monitor.detect_current_power(), "unknown")
        await self.receive("0", retain=True)
        self.assertEqual(await monitor.detect_current_power(), "unknown")
        await self.receive("50")
        self.assertEqual(await monitor.detect_current_power(), "unknown")
        await self.receive("0")
        self.assertEqual(await monitor.detect_current_power(), "off")
        ha.states[feature.config.power.loss_entity]["state"] = "50"
        self.assertEqual(await monitor.detect_current_power(), "unknown")

    def test_wildcard_witness_topic_is_rejected(self):
        for topic in ["#", "home/+/frequency", "home/\x00"]:
            with self.assertRaises(ValidationError):
                PowerConfig(loss_mqtt_topic=topic)

    def test_numeric_mqtt_template_accepts_bytes_and_text(self):
        from jinja2 import Environment

        template = Environment().from_string("{{ value | float(none) }}")
        for value in ["50", b"50", "0", b"0", "49.9", b"49.9"]:
            self.assertEqual(float(template.render(value=value)), float(value))
        self.assertEqual(template.render(value="invalid"), "None")

    def test_telemetry_template_separates_information_line(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from template_engine import TemplateEngine

        with TemporaryDirectory() as directory:
            engine = TemplateEngine(Path(directory))
            text = engine.render(
                "power_telemetry_restored",
                display_name="Test",
                house_state="on",
                timestamp="now",
            )
        self.assertIn("Напруга є.\n", text)
        self.assertIn("\nℹ️", text)
        self.assertNotIn("є.ℹ️", text)

import sys
import unittest
from importlib.util import find_spec
from pathlib import Path

HAS_RUNTIME_DEPS = find_spec("yaml") is not None and find_spec("pydantic") is not None

if HAS_RUNTIME_DEPS:
    from pydantic import ValidationError

    APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
    sys.path.insert(0, str(APP_ROOT))

    from config import (
        AddressConfig,
        PowerConfig,
        StatusMessageConfig,
        UpcomingOutageConfig,
        VoltageConfig,
    )


@unittest.skipUnless(HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment")
class ConfigValidationTest(unittest.TestCase):
    def test_voltage_delay_must_be_non_negative(self) -> None:
        with self.assertRaises(ValidationError):
            VoltageConfig(delay=-1)

    def test_upcoming_outage_minutes_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            UpcomingOutageConfig(minutes=0)

    def test_loss_plus_voltage_requires_loss_entity(self) -> None:
        with self.assertRaises(ValidationError):
            PowerConfig(enabled=True, mode="loss_plus_voltage")

    def test_legacy_single_sensor_alias_maps_to_loss_plus_voltage(self) -> None:
        cfg = PowerConfig(enabled=True, mode="single_sensor", loss_entity="sensor.loss")

        self.assertEqual(cfg.mode, "loss_plus_voltage")

    def test_voltage_only_requires_voltage_entities_at_address_level(self) -> None:
        with self.assertRaises(ValidationError):
            AddressConfig(
                entity_prefix="demo",
                power={"enabled": True, "mode": "voltage_only"},
            )

    def test_legacy_dual_sensor_fields_migrate_into_voltage_section(self) -> None:
        cfg = AddressConfig(
            entity_prefix="demo",
            voltage={"enabled": False, "entities": []},
            power={
                "enabled": True,
                "mode": "dual_sensor",
                "loss_entity": "sensor.loss",
                "confirm_entity": "sensor.v_main",
                "phase_entities": [
                    {"entity": "sensor.v_l2", "label": "L2"},
                    {"entity": "sensor.v_l3", "label": "L3"},
                ],
                "phase_threshold": 65,
            },
        )

        self.assertEqual(cfg.power.mode, "loss_plus_voltage")
        self.assertEqual(cfg.voltage.present_above, 65)
        self.assertEqual(
            [item.entity for item in cfg.voltage.entities],
            ["sensor.v_main", "sensor.v_l2", "sensor.v_l3"],
        )
        self.assertEqual(cfg.voltage.entities[0].label, "L1")

    def test_upcoming_outage_power_filter_must_be_supported(self) -> None:
        with self.assertRaises(ValidationError):
            UpcomingOutageConfig(power_filter="sometimes")

    def test_status_message_delivery_mode_must_be_supported(self) -> None:
        with self.assertRaises(ValidationError):
            StatusMessageConfig(delivery_mode="thread")


if __name__ == "__main__":
    unittest.main()

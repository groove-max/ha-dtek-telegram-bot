import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

HAS_RUNTIME_DEPS = find_spec("yaml") is not None and find_spec("pydantic") is not None

if HAS_RUNTIME_DEPS:
    APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
    sys.path.insert(0, str(APP_ROOT))

    from power_monitor import PowerMonitor


class DummyHA:
    def __init__(self, states: dict[str, dict[str, str]]) -> None:
        self._states = states

    async def get_state(self, entity_id: str) -> dict[str, str] | None:
        return self._states.get(entity_id)


class DummyState:
    def get(self, prefix: str, key: str) -> str | None:
        return None


@unittest.skipUnless(HAS_RUNTIME_DEPS, "PyYAML and pydantic are not installed in the local test environment")
class PowerMonitorTest(unittest.IsolatedAsyncioTestCase):
    def _build_config(
        self,
        *,
        mode: str,
        voltage_entities: list[str],
        loss_entity: str = "",
        unavailable_as_missing: bool = True,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            entity_prefix="doroga_liustdorfska_56v",
            power=SimpleNamespace(
                enabled=True,
                mode=mode,
                loss_entity=loss_entity,
                loss_state="<1",
                confirm_timeout=15,
            ),
            voltage=SimpleNamespace(
                enabled=bool(voltage_entities),
                entities=[
                    SimpleNamespace(entity=entity_id, label="")
                    for entity_id in voltage_entities
                ],
                present_above=50,
                unavailable_as_missing=unavailable_as_missing,
            ),
        )

    async def test_voltage_only_single_phase_above_threshold_is_on(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="voltage_only",
                voltage_entities=["sensor.v_main"],
            ),
            ha=DummyHA(
                {
                    "sensor.v_main": {"state": "223.45"},
                    "binary_sensor.doroga_liustdorfska_56v_power": {"state": "off"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "on")
        self.assertEqual(snapshot["house_state"], "on")
        self.assertEqual(snapshot["single_voltage"], 223.45)

    async def test_voltage_only_multi_phase_partial_when_one_phase_missing(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="voltage_only",
                voltage_entities=["sensor.v_l1", "sensor.v_l2", "sensor.v_l3"],
            ),
            ha=DummyHA(
                {
                    "sensor.v_l1": {"state": "228.0"},
                    "sensor.v_l2": {"state": "0"},
                    "sensor.v_l3": {"state": "227.0"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "partial")
        self.assertEqual(snapshot["present_phase_count"], 2)
        self.assertEqual(snapshot["missing_phases"], ["L2"])

    async def test_loss_plus_voltage_off_when_all_configured_phases_disappear(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="loss_plus_voltage",
                voltage_entities=["sensor.v_l1", "sensor.v_l2"],
                loss_entity="sensor.solar2mqtt_ac_in_frequenz",
            ),
            ha=DummyHA(
                {
                    "sensor.solar2mqtt_ac_in_frequenz": {"state": "0"},
                    "sensor.v_l1": {"state": "0"},
                    "sensor.v_l2": {"state": "unavailable"},
                    "binary_sensor.doroga_liustdorfska_56v_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()

        self.assertEqual(detected, "off")

    async def test_loss_plus_voltage_partial_even_if_loss_entity_triggered(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="loss_plus_voltage",
                voltage_entities=["sensor.v_l1", "sensor.v_l2"],
                loss_entity="sensor.solar2mqtt_ac_in_frequenz",
            ),
            ha=DummyHA(
                {
                    "sensor.solar2mqtt_ac_in_frequenz": {"state": "0"},
                    "sensor.v_l1": {"state": "0"},
                    "sensor.v_l2": {"state": "226.0"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "partial")
        self.assertEqual(snapshot["missing_phases"], ["L1"])

    async def test_loss_plus_voltage_ignores_unavailable_without_loss_confirmation(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="loss_plus_voltage",
                voltage_entities=["sensor.v_main"],
                loss_entity="sensor.solar2mqtt_ac_in_frequenz",
                unavailable_as_missing=True,
            ),
            ha=DummyHA(
                {
                    "sensor.solar2mqtt_ac_in_frequenz": {"state": "50"},
                    "sensor.v_main": {"state": "unavailable"},
                    "binary_sensor.doroga_liustdorfska_56v_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()

        self.assertEqual(detected, "on")

    async def test_legacy_primary_label_is_normalized_to_l1(self) -> None:
        config = self._build_config(
            mode="voltage_only",
            voltage_entities=["sensor.v_main"],
        )
        config.voltage.entities[0].label = "Основний"

        monitor = PowerMonitor(
            config=config,
            ha=DummyHA({"sensor.v_main": {"state": "0"}}),
            state=DummyState(),
        )

        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(snapshot["phases"][0]["label"], "L1")
        self.assertEqual(snapshot["missing_phases"], ["L1"])

    async def test_voltage_only_falls_back_to_dtek_when_all_phases_are_unknown_and_ignored(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="voltage_only",
                voltage_entities=["sensor.v_l1", "sensor.v_l2"],
                unavailable_as_missing=False,
            ),
            ha=DummyHA(
                {
                    "sensor.v_l1": {"state": "unavailable"},
                    "sensor.v_l2": {"state": "unknown"},
                    "binary_sensor.doroga_liustdorfska_56v_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "on")
        self.assertIsNone(snapshot["house_state"])
        self.assertEqual(snapshot["unknown_phases"], ["L1", "L2"])

    async def test_voltage_only_partial_when_one_phase_is_present_and_one_is_unknown(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="voltage_only",
                voltage_entities=["sensor.v_l1", "sensor.v_l2"],
                unavailable_as_missing=False,
            ),
            ha=DummyHA(
                {
                    "sensor.v_l1": {"state": "228.0"},
                    "sensor.v_l2": {"state": "unavailable"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "partial")
        self.assertEqual(snapshot["present_phase_count"], 1)
        self.assertEqual(snapshot["unknown_phases"], ["L2"])
        self.assertEqual(snapshot["missing_phases"], [])

    async def test_voltage_only_rest_miss_is_unknown_not_missing(self) -> None:
        monitor = PowerMonitor(
            config=self._build_config(
                mode="voltage_only",
                voltage_entities=["sensor.v_main"],
                unavailable_as_missing=True,
            ),
            ha=DummyHA(
                {
                    "binary_sensor.doroga_liustdorfska_56v_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "on")
        self.assertIsNone(snapshot["house_state"])
        self.assertEqual(snapshot["unknown_phases"], ["L1"])
        self.assertEqual(snapshot["missing_phases"], [])

    async def test_loss_plus_voltage_stale_rest_miss_after_loss_is_off(self) -> None:
        class DummyStateWithLossReference:
            def get(self, prefix: str, key: str) -> str | None:
                if key == "loss_stale_reference_at":
                    return "2026-03-19T00:00:00+02:00"
                return None

        monitor = PowerMonitor(
            config=self._build_config(
                mode="loss_plus_voltage",
                voltage_entities=["sensor.v_main"],
                loss_entity="sensor.solar2mqtt_ac_in_frequenz",
                unavailable_as_missing=True,
            ),
            ha=DummyHA(
                {
                    "sensor.solar2mqtt_ac_in_frequenz": {"state": "0"},
                    "binary_sensor.doroga_liustdorfska_56v_power": {"state": "on"},
                }
            ),
            state=DummyStateWithLossReference(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot(
            stale_reference="2026-03-19T00:00:00+02:00",
            stale_after_seconds=0,
        )

        self.assertEqual(detected, "off")
        self.assertEqual(snapshot["house_state"], "off")
        self.assertEqual(snapshot["missing_phases"], ["L1"])
        self.assertTrue(snapshot["phases"][0]["stale_after_loss"])


if __name__ == "__main__":
    unittest.main()

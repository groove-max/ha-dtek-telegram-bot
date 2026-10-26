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
        state = self._states.get(entity_id)
        if state is None:
            return None
        from datetime import datetime, timezone
        return {"last_updated": datetime.now(timezone.utc).isoformat(), **state}


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
            entity_prefix="vulytsia_prykladna_12a",
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
                    "binary_sensor.vulytsia_prykladna_12a_power": {"state": "off"},
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

    async def test_loss_plus_voltage_unknown_when_one_phase_has_no_measurement(self) -> None:
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
                    "binary_sensor.vulytsia_prykladna_12a_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()

        self.assertEqual(detected, "unknown")

    async def test_numeric_zero_needs_valid_loss_witness_in_paired_mode(self) -> None:
        from datetime import datetime, timedelta, timezone

        for witness in (
            None,
            {"state": "50"},
            {"state": "unavailable"},
            {"state": "unknown"},
            {"state": "nan"},
            {"state": "0", "attributes": {"restored": True}},
            {"state": "0", "last_updated": (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()},
        ):
            with self.subTest(witness=witness):
                states = {"sensor.v_main": {"state": "0"}}
                if witness is not None:
                    states["sensor.grid_frequency"] = witness
                monitor = PowerMonitor(
                    config=self._build_config(
                        mode="loss_plus_voltage",
                        voltage_entities=["sensor.v_main"],
                        loss_entity="sensor.grid_frequency",
                    ),
                    ha=DummyHA(states),
                    state=DummyState(),
                )
                self.assertEqual(await monitor.detect_current_power(), "unknown")

    async def test_loss_witness_is_rechecked_after_voltage_fetch(self) -> None:
        ha = DummyHA({
            "sensor.grid_frequency": {"state": "0"},
            "sensor.v_main": {"state": "0"},
        })
        get_state = ha.get_state

        async def change_witness(entity_id):
            if entity_id == "sensor.v_main":
                ha._states["sensor.grid_frequency"] = {"state": "50"}
            return await get_state(entity_id)

        ha.get_state = change_witness
        monitor = PowerMonitor(
            config=self._build_config(
                mode="loss_plus_voltage",
                voltage_entities=["sensor.v_main"],
                loss_entity="sensor.grid_frequency",
            ),
            ha=ha,
            state=DummyState(),
        )
        self.assertEqual(await monitor.detect_current_power(), "unknown")

    async def test_loss_plus_voltage_partial_conflicts_with_whole_line_loss(self) -> None:
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

        self.assertEqual(detected, "unknown")
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
                    "binary_sensor.vulytsia_prykladna_12a_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()

        self.assertEqual(detected, "unknown")

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

    async def test_voltage_only_unknown_without_fallback_when_all_phases_unknown(self) -> None:
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
                    "binary_sensor.vulytsia_prykladna_12a_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "unknown")
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
                    "binary_sensor.vulytsia_prykladna_12a_power": {"state": "on"},
                }
            ),
            state=DummyState(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot()

        self.assertEqual(detected, "unknown")
        self.assertIsNone(snapshot["house_state"])
        self.assertEqual(snapshot["unknown_phases"], ["L1"])
        self.assertEqual(snapshot["missing_phases"], [])

    async def test_loss_plus_voltage_stale_rest_miss_is_still_unknown(self) -> None:
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
                    "binary_sensor.vulytsia_prykladna_12a_power": {"state": "on"},
                }
            ),
            state=DummyStateWithLossReference(),
        )

        detected = await monitor.detect_current_power()
        snapshot = await monitor.get_voltage_snapshot(
            stale_reference="2026-03-19T00:00:00+02:00",
            stale_after_seconds=0,
        )

        self.assertEqual(detected, "unknown")
        self.assertIsNone(snapshot["house_state"])
        self.assertEqual(snapshot["missing_phases"], [])
        self.assertFalse(snapshot["phases"][0]["stale_after_loss"])


if __name__ == "__main__":
    unittest.main()

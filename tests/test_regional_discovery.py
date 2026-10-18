"""Regional HA identities work through the existing common bot contract."""

import sys
import unittest
from pathlib import Path
from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import AsyncMock

HAS_DEPS = all(find_spec(m) is not None for m in ("yaml", "pydantic", "aiogram"))
if HAS_DEPS:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
    import main


@unittest.skipUnless(HAS_DEPS, "Requires addon runtime dependencies")
class RegionalDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_labels_keep_oem_and_krem_separate(self):
        prefixes = ["vulytsia_prykladna_12a", "dtek_krem_0123456789abcdef"]
        states = []
        for prefix in prefixes:
            for domain, suffix in [
                ("binary_sensor", "power"),
                ("sensor", "outage_status"),
                ("sensor", "primary_schedule_group"),
                ("calendar", "outage_schedule"),
                ("calendar", "possible_outage_schedule"),
            ]:
                states.append(
                    {
                        "entity_id": f"{domain}.{prefix}_{suffix}",
                        "state": "on",
                        "attributes": {},
                    }
                )
        app = object.__new__(main.Orchestrator)
        addresses = [
            SimpleNamespace(entity_prefix=p, display_name="") for p in prefixes
        ]
        app.config = SimpleNamespace(addresses=addresses[:1])
        app.ha = SimpleNamespace(
            is_connected=True,
            get_states=AsyncMock(return_value=states),
            render_template=AsyncMock(
                side_effect=[
                    "вулиця Прикладна, 12А|м. Одеса",
                    "вул. Броварської сотні, 1|м. Бровари",
                ]
            ),
        )
        result = await app.discover_addresses()
        candidates = {c["entity_prefix"]: c for c in result["candidates"]}
        self.assertEqual(set(candidates), set(prefixes))
        for prefix in prefixes:
            self.assertEqual(
                candidates[prefix]["entities"],
                {
                    "power": f"binary_sensor.{prefix}_power",
                    "status": f"sensor.{prefix}_outage_status",
                    "schedule_group": f"sensor.{prefix}_primary_schedule_group",
                    "outage_schedule": f"calendar.{prefix}_outage_schedule",
                },
            )
        self.assertTrue(candidates[prefixes[0]]["configured"])
        self.assertFalse(candidates[prefixes[1]]["configured"])
        app.config.addresses = addresses
        await app._resolve_display_names()
        self.assertEqual(addresses[0].display_name, "Одеса • вулиця Прикладна, 12А")
        self.assertEqual(
            addresses[1].display_name, "Бровари • вул. Броварської сотні, 1"
        )

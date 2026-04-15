import asyncio
import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch


HAS_RUNTIME_DEPS = all(
    find_spec(module) is not None
    for module in ("yaml", "pydantic", "aiogram")
)

if HAS_RUNTIME_DEPS:
    APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
    sys.path.insert(0, str(APP_ROOT))

    import main as app_main
    from config import build_runtime_config_payload, validate_runtime_config_payload


OPTIONS = {
    "telegram_bot_token": "123456:TEST_TOKEN_VALUE",
    "telegram_chat_id": "-1001234567890",
}


@unittest.skipUnless(HAS_RUNTIME_DEPS, "Runtime dependencies are not installed in the local test environment")
class EditorConfigSyncTest(unittest.TestCase):
    def test_get_editor_config_uses_latest_saved_runtime_payload(self) -> None:
        startup_config = validate_runtime_config_payload(
            {
                "ha_token": "old-token",
                "addresses": [
                    {
                        "entity_prefix": "demo_prefix",
                        "display_name": "Old name",
                        "voltage": {
                            "enabled": True,
                            "entities": [{"entity": "sensor.demo_voltage", "label": "L1"}],
                            "low": 195,
                            "high": 245,
                        },
                        "power": {"enabled": False, "mode": "dtek_only"},
                    }
                ],
            },
            options=OPTIONS,
        )
        saved_config = validate_runtime_config_payload(
            {
                "ha_token": "new-token",
                "addresses": [
                    {
                        "entity_prefix": "demo_prefix",
                        "display_name": "New name",
                        "telegram_chat_id": "-100999888777",
                        "voltage": {
                            "enabled": True,
                            "entities": [{"entity": "sensor.demo_voltage", "label": "L1"}],
                            "low": 205,
                            "high": 252,
                            "delay": 25,
                            "present_above": 60,
                            "unavailable_as_missing": False,
                        },
                        "power": {"enabled": False, "mode": "dtek_only"},
                    }
                ],
            },
            options=OPTIONS,
        )

        orchestrator = app_main.Orchestrator(startup_config)

        with patch.object(
            app_main,
            "load_runtime_config_payload",
            return_value=build_runtime_config_payload(saved_config),
        ):
            payload = asyncio.run(orchestrator.get_editor_config())

        saved_address = payload["config"]["addresses"][0]
        self.assertEqual(payload["config"]["ha_token"], "new-token")
        self.assertEqual(saved_address["display_name"], "New name")
        self.assertEqual(saved_address["telegram_chat_id"], "-100999888777")
        self.assertEqual(saved_address["voltage"]["low"], 205)
        self.assertEqual(saved_address["voltage"]["high"], 252)
        self.assertEqual(saved_address["voltage"]["delay"], 25)
        self.assertFalse(saved_address["voltage"]["unavailable_as_missing"])

    def test_get_editor_config_preserves_resolved_display_name_for_blank_saved_value(self) -> None:
        startup_config = validate_runtime_config_payload(
            {
                "ha_token": "old-token",
                "addresses": [
                    {
                        "entity_prefix": "demo_prefix",
                        "display_name": "Resolved from HA",
                        "voltage": {
                            "enabled": True,
                            "entities": [{"entity": "sensor.demo_voltage", "label": "L1"}],
                        },
                        "power": {"enabled": False, "mode": "dtek_only"},
                    }
                ],
            },
            options=OPTIONS,
        )
        saved_config = validate_runtime_config_payload(
            {
                "ha_token": "old-token",
                "addresses": [
                    {
                        "entity_prefix": "demo_prefix",
                        "display_name": "",
                        "voltage": {
                            "enabled": True,
                            "entities": [{"entity": "sensor.demo_voltage", "label": "L1"}],
                        },
                        "power": {"enabled": False, "mode": "dtek_only"},
                    }
                ],
            },
            options=OPTIONS,
        )

        orchestrator = app_main.Orchestrator(startup_config)

        with patch.object(
            app_main,
            "load_runtime_config_payload",
            return_value=build_runtime_config_payload(saved_config),
        ):
            payload = asyncio.run(orchestrator.get_editor_config())

        self.assertEqual(
            payload["config"]["addresses"][0]["display_name"],
            "Resolved from HA",
        )


if __name__ == "__main__":
    unittest.main()
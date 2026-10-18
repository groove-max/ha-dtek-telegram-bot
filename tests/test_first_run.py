"""A fresh installation has no subscriptions; upgrades retain user addresses."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

import yaml

APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs/app"
sys.path.insert(0, str(APP_ROOT))
import config
import main as app_main


class FirstRunTests(unittest.TestCase):
    def test_first_and_second_start_have_no_sample_subscriptions(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            ha_path = base / "homeassistant/dtek_telegram_bot.yaml"
            data_path = base / "data/config.yaml"
            ha_path.parent.mkdir()
            data_path.parent.mkdir()
            with (
                patch.object(config, "CONFIG_PATH_HA", ha_path),
                patch.object(config, "CONFIG_PATH", data_path),
                patch.object(
                    config, "DEFAULT_CONFIG_PATH", APP_ROOT / "default_config.yaml"
                ),
                patch.object(config, "load_options", return_value={}),
            ):
                self.assertEqual(config.load_config().addresses, [])
                self.assertEqual(config.load_config().addresses, [])
                for path in [ha_path, data_path]:
                    self.assertEqual(yaml.safe_load(path.read_text())["addresses"], [])

    def test_upgrade_does_not_replace_existing_user_configuration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dtek_telegram_bot.yaml"
            content = "addresses:\n  - entity_prefix: my_existing_address\n"
            path.write_text(content)
            with (
                patch.object(config, "CONFIG_PATH_HA", path),
                patch.object(config, "CONFIG_PATH", Path(directory) / "fallback.yaml"),
                patch.object(config, "load_options", return_value={}),
            ):
                loaded = config.load_config()
                self.assertEqual(
                    loaded.addresses[0].entity_prefix, "my_existing_address"
                )
                self.assertEqual(path.read_text(), content)


class FirstRunEntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_address_list_keeps_editor_available(self):
        initial = config.FullConfig(
            telegram_bot_token="123456:TEST_TOKEN_VALUE",
            telegram_chat_id="123456789",
        )
        runtime = Mock(run=AsyncMock(), shutdown=AsyncMock())
        ui = Mock(start=AsyncMock(), stop=AsyncMock())
        with (
            patch.object(app_main, "load_config", return_value=initial),
            patch.object(app_main, "Orchestrator", return_value=runtime),
            patch.object(app_main, "UIServer", return_value=ui),
            patch.object(app_main.asyncio, "get_running_loop", return_value=Mock()),
        ):
            await app_main.main()
        ui.start.assert_awaited_once()
        runtime.run.assert_awaited_once()
        ui.stop.assert_awaited_once()
        runtime.shutdown.assert_awaited_once()

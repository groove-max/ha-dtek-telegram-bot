"""HTTP editor validation and optimistic concurrency with real temporary files."""

import asyncio
import copy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
import config
from main import Orchestrator
from template_engine import TemplateEngine
from ui_server import UIServer


class UIConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for name, path in [
            ("CONFIG_PATH_HA", root / "homeassistant/config.yaml"),
            ("CONFIG_PATH", root / "config.yaml"),
        ]:
            patcher = patch.object(config, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.payload = config.build_runtime_config_payload(
            config.FullConfig(addresses=[config.AddressConfig(entity_prefix="demo")])
        )
        config.save_runtime_config_payload(self.payload)
        self.orchestrator = Mock(spec=Orchestrator)
        self.orchestrator.config = config.FullConfig()
        self.orchestrator.tg = Mock(default_chat_id="123456789")
        self.orchestrator.preview_runtime_config = AsyncMock(
            return_value={"addresses": []}
        )
        self.orchestrator.templates = TemplateEngine(root / "templates")
        self.orchestrator.get_templates_snapshot.side_effect = (
            lambda: Orchestrator.get_templates_snapshot(self.orchestrator)
        )
        self.orchestrator.save_template_override.side_effect = (
            lambda name, source: Orchestrator.save_template_override(
                self.orchestrator, name, source
            )
        )
        self.orchestrator.reset_template_override.side_effect = (
            lambda name: Orchestrator.reset_template_override(self.orchestrator, name)
        )
        self.server = UIServer(self.orchestrator)
        self.server._delayed_restart = AsyncMock()
        self.client = TestClient(TestServer(self.server._app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def save(self, payload=None, revision=None, restart=False):
        return await self.client.post(
            "/api/config/save",
            json={
                "config": payload or self.payload,
                "expected_revision": revision,
                "restart": restart,
            },
        )

    async def test_stale_save_is_rejected_without_overwriting_file(self):
        revision = config.runtime_config_revision()
        first = copy.deepcopy(self.payload)
        first["addresses"][0]["display_name"] = "First tab"
        response = await self.save(first, revision)
        self.assertEqual(response.status, 200)
        saved = await response.json()
        self.assertEqual(saved["revision"], config.runtime_config_revision())
        second = copy.deepcopy(self.payload)
        second["addresses"][0]["display_name"] = "Second tab"
        response = await self.save(second, revision)
        self.assertEqual(response.status, 409)
        self.assertEqual(config.load_runtime_config_payload(), first)
        self.orchestrator.preview_runtime_config.assert_awaited_once()

    async def test_save_without_revision_does_not_overwrite(self):
        response = await self.save()
        self.assertEqual(response.status, 409)
        self.assertEqual(config.load_runtime_config_payload(), self.payload)

    async def test_two_requests_cannot_both_write_the_same_revision(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def preview(_):
            if not started.is_set():
                started.set()
                await release.wait()
            return {"addresses": []}

        self.orchestrator.preview_runtime_config.side_effect = preview
        revision = config.runtime_config_revision()
        first = copy.deepcopy(self.payload)
        first["addresses"][0]["display_name"] = "Saved"
        request = asyncio.create_task(self.save(first, revision))
        try:
            await asyncio.wait_for(started.wait(), 2)
            second_payload = copy.deepcopy(self.payload)
            second_payload["addresses"][0]["display_name"] = "Second wins"
            second = await self.save(second_payload, revision)
            self.assertEqual(second.status, 200)
            self.assertEqual(config.load_runtime_config_payload(), second_payload)
        finally:
            release.set()
            self.assertEqual((await request).status, 409)

    async def test_custom_validation_errors_return_json_without_mutation(self):
        invalid = copy.deepcopy(self.payload)
        invalid["addresses"][0]["power"]["loss_delay"] = -1
        for endpoint in ("validate", "save"):
            with self.subTest(endpoint=endpoint):
                response = await self.client.post(
                    f"/api/config/{endpoint}",
                    json={
                        "config": invalid,
                        "expected_revision": config.runtime_config_revision(),
                    },
                )
                self.assertEqual(response.status, 400)
                body = await response.json()
                self.assertFalse(body["ok"])
                self.assertEqual(body["errors"][0]["type"], "value_error")
        self.assertEqual(config.load_runtime_config_payload(), self.payload)

    async def test_validation_does_not_save_or_restart(self):
        changed = copy.deepcopy(self.payload)
        changed["addresses"][0]["display_name"] = "Unsaved"
        response = await self.client.post(
            "/api/config/validate", json={"config": changed}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(config.load_runtime_config_payload(), self.payload)
        self.server._delayed_restart.assert_not_called()

    async def test_restart_is_scheduled_after_preview_finishes(self):
        async def preview(_):
            self.server._delayed_restart.assert_not_called()
            return {"addresses": []}

        self.orchestrator.preview_runtime_config.side_effect = preview
        response = await self.save(
            revision=config.runtime_config_revision(), restart=True
        )
        self.assertEqual(response.status, 200)
        self.server._delayed_restart.assert_awaited_once()

    async def test_template_save_and_reset_reject_stale_source(self):
        name = "power_off"
        original = self.orchestrator.templates.get_template_details(name)["source"]
        response = await self.client.post(
            "/api/templates/save",
            json={
                "template_name": name,
                "source": "First tab",
                "expected_source": original,
            },
        )
        self.assertEqual(response.status, 200)
        for endpoint in ("save", "reset"):
            with self.subTest(endpoint=endpoint):
                response = await self.client.post(
                    f"/api/templates/{endpoint}",
                    json={
                        "template_name": name,
                        "source": "Second tab",
                        "expected_source": original,
                    },
                )
                self.assertEqual(response.status, 409)
                self.assertEqual(
                    self.orchestrator.templates.get_template_details(name)["source"],
                    "First tab",
                )
        response = await self.client.post(
            "/api/templates/reset",
            json={"template_name": name, "expected_source": "First tab"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["template"]["source"], original)

    async def test_editor_revision_uses_persisted_data_before_preview_await(self):
        stored_revision = config.runtime_config_revision()
        editor = config.validate_runtime_config_payload(self.payload)
        editor.addresses[0].display_name = "Resolved HA name"
        self.orchestrator._get_editor_runtime_config.return_value = editor

        async def preview(_):
            changed = copy.deepcopy(self.payload)
            changed["addresses"][0]["display_name"] = "Changed during preview"
            config.save_runtime_config_payload(changed)
            return {"addresses": []}

        self.orchestrator.preview_runtime_config.side_effect = preview
        snapshot = await Orchestrator.get_editor_config(self.orchestrator)
        self.assertEqual(snapshot["revision"], stored_revision)
        self.assertNotEqual(snapshot["revision"], config.runtime_config_revision())
        self.assertEqual(
            snapshot["config"]["addresses"][0]["display_name"], "Resolved HA name"
        )

    async def test_revision_does_not_depend_on_dictionary_order(self):
        reordered = dict(reversed(list(self.payload.items())))
        self.assertEqual(
            config.runtime_config_revision(self.payload),
            config.runtime_config_revision(reordered),
        )

    async def test_failed_preview_does_not_partially_save_configuration(self):
        self.orchestrator.preview_runtime_config.side_effect = RuntimeError(
            "HA unavailable"
        )
        changed = copy.deepcopy(self.payload)
        changed["addresses"][0]["display_name"] = "Must remain unsaved"
        response = await self.save(
            changed, config.runtime_config_revision(), restart=True
        )
        self.assertEqual(response.status, 500)
        self.assertEqual(config.load_runtime_config_payload(), self.payload)
        self.server._delayed_restart.assert_not_called()

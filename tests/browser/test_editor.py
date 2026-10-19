"""Browser regressions using real UI assets and an isolated, delayed HTTP API.

Run separately: python -m unittest discover -s tests/browser -v
No requests reach Home Assistant or Telegram.
"""

import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rootfs/app"))
from config import AddressConfig


class EditorBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.context = await self.browser.new_context(
            viewport={"width": 1440, "height": 1000}
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("dialog", lambda dialog: dialog.accept())
        self.config = {
            "ha_token": "",
            "export_default_templates": False,
            "addresses": [
                AddressConfig(
                    entity_prefix="demo_a", display_name="Будинок A"
                ).model_dump(),
                AddressConfig(
                    entity_prefix="demo_b", display_name="Будинок B"
                ).model_dump(),
            ],
        }
        self.bot_profiles = {
            "revision": "bots-1", "restart_required": False,
            "profiles": [{"id": "default", "name": "Основний бот", "chat_id": "123456789"}],
            "runtime": [],
        }
        self.revision = 1
        self.templates = [
            {"name": name, "source": text, "group": "Живлення", "origin": "default"}
            for name, text in [("power_off", "Немає світла"), ("power_on", "Є світло")]
        ]
        self.calls = []
        self.gates = {}
        self.started = {}
        self.failures = {}
        self.malformed = {}
        self.ticks = 0
        self.candidates = []
        self.accounts = []
        await self.context.route("**/*", self.route)
        await self.page.clock.install()
        await self.page.goto("http://dtek-ui.test/ingress/instance-a/")
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Будинок A"
        )
        await self.page.locator('[data-action="edit-profile"]').first.click()
        # Regression scenarios use the explicit expanded view; dedicated setup
        # tests below exercise the default step-by-step experience.
        await self.page.locator('[data-action="show-all"]').first.click()
        await self.page.locator('.profile-actions > summary').first.click()
        await self.page.locator('.power-compatibility').first.evaluate("el => el.open = true")
        await self.page.locator("#bot-extra").evaluate("el => el.open = true")
        # Existing regression scenarios exercise the optional tools too.
        await self.page.locator('.editor-tools > summary').click()
        await self.page.locator('.nav-tools > summary').click()
        await self.page.locator('.global-settings > summary').click()

    async def asyncTearDown(self):
        for gate in self.gates.values():
            gate.set()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.assertEqual(self.errors, [])

    def config_payload(self):
        return {
            "config": copy.deepcopy(self.config),
            "bots": copy.deepcopy(self.bot_profiles),
            "revision": str(self.revision),
            "options": {"default_chat_id": "123456789"},
            "config_path": "/data/config.yaml",
            "preview": {"addresses": [], "generated_at": "2026-01-01T12:00:00Z"},
            "restart_required": False,
        }

    async def route(self, route):
        path = urlparse(route.request.url).path
        if "/api/" not in path:
            name = path.rsplit("/", 1)[-1] or "index.html"
            asset = ROOT / "rootfs/app/web" / name
            if not asset.is_file():
                await route.fulfill(status=404)
                return
            mime = {
                "js": "application/javascript",
                "css": "text/css",
                "html": "text/html",
            }[name.rsplit(".", 1)[-1]]
            await route.fulfill(body=asset.read_text(), content_type=mime)
            return
        endpoint = path.split("/api/")[1]
        body = route.request.post_data_json if route.request.method == "POST" else None
        self.calls.append((endpoint, body))
        payload = None
        if endpoint == "config":
            payload = self.config_payload()  # Capture before a simulated network delay.
        elif endpoint == "templates":
            payload = {"templates": copy.deepcopy(self.templates)}
        self.started.setdefault(endpoint, asyncio.Event()).set()
        if endpoint in self.gates:
            await self.gates[endpoint].wait()
        if endpoint in self.malformed:
            await route.fulfill(
                status=200,
                body=self.malformed[endpoint],
                content_type="application/json",
            )
            return
        if endpoint in self.failures:
            await route.fulfill(
                status=self.failures[endpoint], json={"message": "Конфлікт збереження"}
            )
            return
        if endpoint == "overview":
            self.ticks += 1
            payload = {
                "runtime": {
                    "running": True,
                    "ha_connected": True,
                    "queued_events": self.ticks,
                },
                "global": {"address_count": len(self.config["addresses"])},
                "addresses": [],
            }
        elif endpoint == "discovery":
            payload = {
                "ha_connected": True,
                "candidates": self.candidates,
                "accounts": self.accounts,
                "catalog": {
                    "binary_power": [{"entity_id": "binary_sensor.city_grid", "friendly_name": "Міська мережа", "state": "on"}],
                    "frequency_sensors": [{"entity_id": "sensor.grid_hz", "friendly_name": "Частота входу", "state": "50", "unit": "Hz"}],
                    "voltage_sensors": [
                        {
                            "entity_id": "sensor.demo_voltage",
                            "friendly_name": "Напруга",
                            "state": "230",
                            "unit": "V",
                        }
                    ]
                },
            }
        elif endpoint == "diagnostics":
            payload = {"connected": True}
        elif endpoint == "bots/save":
            profile = {k: v for k, v in body.items() if k not in ("token", "expected_revision")}
            profile.update(id=body.get("id") or "bot_654321", username="demo_bot")
            self.bot_profiles["profiles"] = [p for p in self.bot_profiles["profiles"] if p["id"] != profile["id"]] + [profile]
            self.bot_profiles["revision"] = "bots-2"
            self.bot_profiles["restart_required"] = True
            payload = {**copy.deepcopy(self.bot_profiles), "profile": profile}
        elif endpoint == "bots/activate":
            payload = {"restart_scheduled": True}
        elif endpoint == "bots/delete":
            self.bot_profiles["profiles"] = [p for p in self.bot_profiles["profiles"] if p["id"] != body["id"]]
            self.bot_profiles["revision"] = "bots-3"
            payload = copy.deepcopy(self.bot_profiles)
        elif endpoint in ("config/save", "config/validate"):
            if endpoint.endswith("/save"):
                self.config = copy.deepcopy(body["config"])
                self.revision += 1
            payload = {
                "ok": True,
                "config": body["config"],
                "revision": str(self.revision),
                "preview": {"addresses": []},
            }
        elif endpoint in ("templates/preview", "templates/send-test"):
            payload = {
                "ok": True,
                "preview": {
                    "address": body["config"]["addresses"][body["address_index"]],
                    "rendered": body["source_override"],
                    "context": {},
                    "sent": {"ok": True, "chat_id": "123456789"},
                },
            }
        elif endpoint in ("templates/save", "templates/reset"):
            meta = next(
                item for item in self.templates if item["name"] == body["template_name"]
            )
            meta.update(
                source=(
                    body["source"] if endpoint.endswith("save") else "Стандартний текст"
                ),
                origin="override",
            )
            payload = {"ok": True, "template": copy.deepcopy(meta)}
        await route.fulfill(json=payload or {})

    def delay(self, endpoint):
        self.gates[endpoint] = asyncio.Event()
        self.started[endpoint] = asyncio.Event()
        return self.gates[endpoint]

    async def wait_started(self, endpoint):
        await asyncio.wait_for(self.started[endpoint].wait(), 5)

    async def refresh(self):
        await self.page.locator("#refresh-button").click()
        await expect(self.page.locator("#refresh-button")).to_be_enabled()

    async def templates_tab(self):
        await self.page.locator('[data-tab="templates"]').click()

    async def test_bot_token_and_address_draft_survive_background_refresh(self):
        await self.page.locator(".addr-display-name").first.fill("Draft address")
        await self.page.locator('[data-tab="bots"]').click()
        await self.page.locator("#bot-name").fill("Building bot")
        await self.page.locator("#bot-token").fill("654321:PRIVATE_TEST_TOKEN")
        await self.page.clock.run_for(15001)
        await self.refresh()
        await expect(self.page.locator("#bot-token")).to_have_value("654321:PRIVATE_TEST_TOKEN")
        await expect(self.page.locator("#bot-name")).to_have_value("Building bot")
        stored = await self.page.evaluate("JSON.stringify({...sessionStorage, ...localStorage})")
        self.assertNotIn("PRIVATE_TEST_TOKEN", stored)
        await self.page.locator('[data-tab="configure"]').click()
        await expect(self.page.locator(".addr-display-name").first).to_have_value("Draft address")

    async def test_add_bot_select_for_address_save_and_export_without_token(self):
        await self.page.locator('[data-tab="bots"]').click()
        await self.page.locator("#bot-name").fill("Building bot")
        await self.page.locator("#bot-token").fill("654321:PRIVATE_TEST_TOKEN")
        await self.page.locator("#bot-chat").fill("-10002")
        await self.page.locator('#bot-form button[type="submit"]').click()
        await expect(self.page.locator("#bot-status")).to_contain_text("Збережено")
        await expect(self.page.locator("#bot-token")).to_have_value("")
        await expect(self.page.locator("#bot-list")).to_contain_text("@demo_bot")
        await self.page.locator('[data-tab="configure"]').click()
        await self.page.locator(".addr-bot-id").first.select_option("bot_654321")
        await self.refresh()
        await expect(self.page.locator(".addr-bot-id").first).to_have_value("bot_654321")
        await self.page.locator("#save-button").click()
        await expect(self.page.locator("#editor-status")).to_contain_text("Налаштування збережено")
        self.assertEqual(self.config["addresses"][0]["telegram_bot_id"], "bot_654321")
        self.assertNotIn("PRIVATE_TEST_TOKEN", json.dumps(self.config))
        async with self.page.expect_download() as info:
            await self.page.locator("#export-config-button").click()
        download = await info.value
        exported = Path(await download.path()).read_text()
        self.assertNotIn("PRIVATE_TEST_TOKEN", exported)
        self.assertIn("bot_654321", exported)

    async def test_failed_profile_save_keeps_input_and_allows_retry(self):
        self.failures["bots/save"] = 409
        await self.page.locator('[data-tab="bots"]').click()
        await self.page.locator("#bot-name").fill("Building bot")
        await self.page.locator("#bot-token").fill("654321:PRIVATE_TEST_TOKEN")
        await self.page.locator('#bot-form button[type="submit"]').click()
        await expect(self.page.locator("#bot-status")).to_contain_text("Конфлікт")
        await self.refresh()
        await expect(self.page.locator("#bot-token")).to_have_value("654321:PRIVATE_TEST_TOKEN")
        await expect(self.page.locator("#bot-name")).to_be_enabled()

    async def test_unknown_imported_profile_stays_selected_without_default_fallback(self):
        self.config["addresses"][0]["telegram_bot_id"] = "bot_missing"
        await self.refresh()
        await expect(self.page.locator(".addr-bot-id").first).to_have_value("bot_missing")
        await expect(self.page.locator(".addr-bot-id").first).to_contain_text("Недоступний профіль")

    async def test_bot_form_fits_mobile_without_overflow(self):
        await self.page.set_viewport_size({"width": 360, "height": 800})
        await self.page.locator('[data-tab="bots"]').click()
        self.assertTrue(await self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))

    async def test_validate_and_refresh_keep_dirty_draft_after_page_reload(self):
        field = self.page.locator(".addr-display-name").first
        await field.fill("Моя чернетка")
        await self.page.locator("#validate-button").click()
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Налаштування перевірено"
        )
        ticks = self.ticks
        await self.page.clock.run_for(15001)
        await expect(self.page.locator("#stats-grid")).to_contain_text(str(ticks + 1))
        await self.refresh()
        await expect(field).to_have_value("Моя чернетка")
        await self.page.reload()
        await expect(field).to_have_value("Моя чернетка")

    async def test_delayed_validation_does_not_replace_new_input(self):
        gate = self.delay("config/validate")
        field = self.page.locator(".addr-display-name").first
        await field.fill("Перша")
        await self.page.locator("#validate-button").click()
        await self.wait_started("config/validate")
        await field.fill("Нова")
        gate.set()
        await expect(self.page.locator("#editor-status")).to_contain_text("Нові зміни")
        await expect(field).to_have_value("Нова")

    async def test_save_ack_preserves_later_edits_and_advances_revision(self):
        gate = self.delay("config/save")
        field = self.page.locator(".addr-display-name").first
        await field.fill("Надіслана")
        await self.page.locator("#save-button").click()
        await self.wait_started("config/save")
        await expect(self.page.locator("#save-button")).to_be_disabled()
        await field.fill("Після запиту")
        gate.set()
        await expect(self.page.locator("#editor-status")).to_contain_text("Нові зміни")
        await expect(field).to_have_value("Після запиту")
        await self.refresh()
        await expect(field).to_have_value("Після запиту")
        await self.page.locator("#save-button").click()
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Налаштування збережено"
        )
        saves = [body for endpoint, body in self.calls if endpoint == "config/save"]
        self.assertEqual([body["expected_revision"] for body in saves], ["1", "2"])
        self.assertEqual(self.config["addresses"][0]["display_name"], "Після запиту")

    async def test_poll_started_before_save_cannot_apply_old_response(self):
        gate = self.delay("config")
        await self.page.locator("#refresh-button").click()
        await self.wait_started("config")
        await self.page.locator(".addr-display-name").first.fill("Збережено")
        await self.page.locator("#save-button").click()
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Налаштування збережено"
        )
        gate.set()
        await expect(self.page.locator("#refresh-button")).to_be_enabled()
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Збережено"
        )

    async def test_failed_save_keeps_input_and_allows_retry(self):
        self.failures["config/save"] = 409
        await self.page.locator(".addr-display-name").first.fill("Не втрачати")
        await self.page.locator("#save-button").click()
        await expect(self.page.locator("#editor-status")).to_contain_text("Конфлікт")
        await self.refresh()
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Не втрачати"
        )
        await expect(self.page.locator("#save-button")).to_be_enabled()

    async def test_template_preview_and_switches_preserve_each_draft(self):
        await self.templates_tab()
        source = self.page.locator("#template-source")
        await source.fill("Мій {{ address }}")
        await self.page.locator('[data-action="template-preview"]').click()
        await expect(self.page.locator("#template-status")).to_contain_text(
            "Прев'ю оновлено"
        )
        await expect(source).to_have_value("Мій {{ address }}")
        await self.page.locator("#template-address-select").select_option("1")
        await expect(source).to_have_value("Мій {{ address }}")
        await self.page.locator("#template-select").select_option("power_on")
        await source.fill("Друга чернетка")
        await self.page.locator("#template-select").select_option("power_off")
        await expect(source).to_have_value("Мій {{ address }}")
        await self.page.reload()
        await expect(source).to_have_value("Мій {{ address }}")
        await self.page.locator("#template-select").select_option("power_on")
        await expect(source).to_have_value("Друга чернетка")

    async def test_delayed_template_preview_cannot_show_old_text(self):
        await self.templates_tab()
        gate = self.delay("templates/preview")
        source = self.page.locator("#template-source")
        await source.fill("Перша")
        await self.page.locator('[data-action="template-preview"]').click()
        await self.wait_started("templates/preview")
        await source.fill("Друга")
        gate.set()
        await expect(self.page.locator("#template-status")).to_contain_text(
            "Чернетка змінилася"
        )
        await expect(source).to_have_value("Друга")
        await expect(self.page.locator(".template-preview").first).to_contain_text(
            "ще не побудовано"
        )

    async def test_delayed_template_save_keeps_new_edits_and_selected_template(self):
        await self.templates_tab()
        gate = self.delay("templates/save")
        source = self.page.locator("#template-source")
        await source.fill("Надіслана")
        await self.page.locator('[data-action="template-save"]').click()
        await self.wait_started("templates/save")
        await source.fill("Нова чернетка")
        await self.page.locator("#template-select").select_option("power_on")
        await source.fill("Інший шаблон")
        gate.set()
        await expect(self.page.locator("#template-status")).to_contain_text(
            "Нові зміни"
        )
        await expect(source).to_have_value("Інший шаблон")
        await self.page.locator("#template-select").select_option("power_off")
        await expect(source).to_have_value("Нова чернетка")
        await self.page.locator('[data-action="template-save"]').click()
        await expect(self.page.locator("#template-status")).to_contain_text(
            "power_off: збережено"
        )
        saves = [b for e, b in self.calls if e == "templates/save"]
        self.assertEqual(saves[-1]["expected_source"], "Надіслана")

    async def test_template_reload_fetches_latest_and_keeps_typing_during_request(self):
        await self.templates_tab()
        self.templates[0]["source"] = "З іншої вкладки"
        await self.page.locator('[data-action="template-reload"]').click()
        await expect(self.page.locator("#template-source")).to_have_value(
            "З іншої вкладки"
        )
        gate = self.delay("templates")
        await self.page.locator('[data-action="template-reload"]').click()
        await self.wait_started("templates")
        await self.page.locator("#template-source").fill("Поки чекали")
        gate.set()
        await expect(self.page.locator("#template-status")).to_contain_text(
            "Ваш ввід збережено"
        )
        await expect(self.page.locator("#template-source")).to_have_value("Поки чекали")

    async def test_config_reload_keeps_typing_and_failure_does_not_clear_form(self):
        gate = self.delay("config")
        await self.page.locator("#reload-config-button").click()
        await self.wait_started("config")
        await self.page.locator(".addr-display-name").first.fill("Поки чекали")
        gate.set()
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Ваш ввід збережено"
        )
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Поки чекали"
        )
        self.failures["config"] = 503
        await self.page.locator("#reload-config-button").click()
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Помилка завантаження"
        )
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Поки чекали"
        )

    async def test_empty_configuration_and_last_address_deletion(self):
        await self.page.locator('[data-action="remove-address"]').first.click()
        await expect(self.page.locator(".address-editor-card")).to_have_count(1)
        await self.page.locator('[data-action="remove-address"]').click()
        await expect(self.page.locator(".address-editor-card")).to_have_count(0)
        await self.page.locator("#save-button").click()
        await expect(self.page.locator("#save-button")).to_be_enabled()
        await self.refresh()
        await expect(self.page.locator(".address-editor-card")).to_have_count(0)
        await self.templates_tab()
        await expect(
            self.page.locator('[data-action="template-preview"]')
        ).to_be_disabled()
        await self.page.locator('[data-tab="configure"]').click()
        await self.page.locator("#add-address-button").click()
        await expect(self.page.locator(".address-editor-card")).to_have_count(1)

    async def test_focused_clean_form_keeps_original_revision_after_external_edit(self):
        field = self.page.locator(".addr-display-name").first
        await field.focus()
        await field.evaluate("(el) => el.setSelectionRange(2, 4)")
        self.config["addresses"][0]["display_name"] = "Ззовні"
        self.revision = 2
        await self.page.clock.run_for(15001)
        await expect(field).to_have_value("Будинок A")
        self.assertEqual(
            await field.evaluate("(el) => [el.selectionStart, el.selectionEnd]"), [2, 4]
        )
        await field.fill("Локальна")
        await self.page.locator("#save-button").click()
        await expect(self.page.locator("#save-button")).to_be_enabled()
        saves = [b for e, b in self.calls if e == "config/save"]
        self.assertEqual(saves[-1]["expected_revision"], "1")

    async def test_drafts_are_scoped_to_ingress_instance(self):
        await self.page.locator(".addr-display-name").first.fill("Лише A")
        await self.page.goto("http://dtek-ui.test/ingress/instance-b/")
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Будинок A"
        )
        await self.page.goto("http://dtek-ui.test/ingress/instance-a/")
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Лише A"
        )

    async def test_no_horizontal_overflow_at_mobile_and_desktop_widths(self):
        await self.page.locator(".addr-display-name").first.fill("ДужеДовгаНазва" * 20)
        for width in (320, 375, 768, 1440):
            await self.page.set_viewport_size({"width": width, "height": 900})
            for tab in (
                "configure",
                "templates",
                "runtime",
                "discovery",
                "diagnostics",
            ):
                await self.page.locator(f'[data-tab="{tab}"]').click()
                overflow = await self.page.evaluate(
                    "document.documentElement.scrollWidth > innerWidth"
                )
                self.assertFalse(overflow, f"{tab} overflows at {width}px")
                artifact_dir = os.environ.get("UI_SCREENSHOT_DIR")
                if (
                    artifact_dir
                    and tab in ("configure", "templates")
                    and width in (375, 1440)
                ):
                    target = Path(artifact_dir)
                    target.mkdir(parents=True, exist_ok=True)
                    await self.page.screenshot(
                        path=str(target / f"{tab}-{width}.png"), full_page=True
                    )

    async def test_entity_picker_and_empty_number_survive_refresh_and_add_address(self):
        card = self.page.locator(".address-editor-card").first
        panel = card.locator(".voltage-panel")
        await panel.locator(".subpanel-head").click()
        await card.locator(".voltage-entity-select").select_option(
            "sensor.demo_voltage"
        )
        await card.locator(".voltage-entity-label").fill("Мітка в процесі")
        await self.refresh()
        await expect(card.locator(".voltage-entity-select")).to_have_value(
            "sensor.demo_voltage"
        )
        await expect(card.locator(".voltage-entity-label")).to_have_value(
            "Мітка в процесі"
        )
        await self.page.locator("#add-address-button").click()
        await expect(card.locator(".voltage-entity-label")).to_have_value(
            "Мітка в процесі"
        )
        await card.locator('[data-action="edit-profile"]').click()
        await card.locator('[data-action="add-entity-item"]').click()
        await expect(card.locator(".selected-entity-item")).to_contain_text(
            "Мітка в процесі"
        )
        number = card.locator(".voltage-present-above")
        await number.fill("")
        await self.refresh()
        await expect(number).to_have_value("")
        await self.page.locator("#validate-button").click()
        await expect(self.page.locator("#validate-button")).to_be_enabled()
        body = [b for e, b in self.calls if e == "config/validate"][-1]
        self.assertEqual(body["config"]["addresses"][0]["voltage"]["present_above"], "")

    async def test_collapsed_panel_stays_closed_when_form_changes(self):
        card = self.page.locator(".address-editor-card").first
        power_panel = card.locator(".subpanel:has(.power-enabled)")
        await card.locator(".power-enabled").check()
        await power_panel.locator(".subpanel-head").click(position={"x": 5, "y": 5})
        await expect(power_panel).to_have_class("subpanel collapsed")
        await card.locator(".addr-display-name").fill("Зміна назви")
        await card.locator(".addr-chat-id").focus()
        await self.refresh()
        await expect(power_panel).to_have_class("subpanel collapsed")

    async def test_template_reset_preserves_new_input_while_waiting(self):
        await self.templates_tab()
        gate = self.delay("templates/reset")
        await self.page.locator("#template-source").fill("Стара чернетка")
        await self.page.locator('[data-action="template-reset"]').click()
        await self.wait_started("templates/reset")
        await self.page.locator("#template-source").fill("Нова чернетка")
        gate.set()
        await expect(self.page.locator("#template-status")).to_contain_text(
            "Нові зміни"
        )
        await expect(self.page.locator("#template-source")).to_have_value(
            "Нова чернетка"
        )

    async def test_refresh_keeps_template_cursor_and_selection(self):
        await self.templates_tab()
        source = self.page.locator("#template-source")
        await source.fill("Моя чернетка з курсором")
        await source.evaluate("(el) => el.setSelectionRange(4, 10)")
        await self.page.clock.run_for(15001)
        self.assertEqual(
            await source.evaluate(
                "(el) => [el.selectionStart, el.selectionEnd, document.activeElement === el]"
            ),
            [4, 10, True],
        )
        await expect(source).to_have_value("Моя чернетка з курсором")

    async def test_slow_file_import_does_not_replace_new_input(self):
        await self.page.evaluate("""() => {
          const original = File.prototype.text;
          File.prototype.text = async function() {
            const value = await original.call(this);
            await new Promise(resolve => { window.releaseFileRead = resolve; });
            return value;
          };
        }""")
        imported = copy.deepcopy(self.config)
        imported["addresses"][0]["display_name"] = "З файлу"
        await self.page.locator("#config-import-input").set_input_files(
            {
                "name": "config.json",
                "mimeType": "application/json",
                "buffer": json.dumps(imported).encode(),
            }
        )
        await self.page.wait_for_function(
            'typeof window.releaseFileRead === "function"'
        )
        await self.page.locator(".addr-display-name").first.fill("Після імпорту")
        await self.page.evaluate("window.releaseFileRead()")
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Повторіть імпорт"
        )
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Після імпорту"
        )

    async def test_template_address_tracks_identity_after_address_deletion(self):
        self.config["addresses"].append(
            AddressConfig(entity_prefix="demo_c", display_name="Будинок C").model_dump()
        )
        await self.refresh()
        await self.templates_tab()
        await self.page.locator("#template-address-select").select_option("1")
        await self.page.locator('[data-tab="configure"]').click()
        await self.page.locator('[data-action="remove-address"]').first.click()
        await self.templates_tab()
        await expect(
            self.page.locator("#template-address-select option:checked")
        ).to_have_text("Будинок B")
        await self.page.locator('[data-tab="configure"]').click()
        await self.page.locator(".addr-display-name").first.fill("Нова назва B")
        await self.templates_tab()
        await expect(
            self.page.locator("#template-address-select option:checked")
        ).to_have_text("Нова назва B")

    async def test_collapsible_sections_support_keyboard(self):
        head = self.page.locator(".voltage-panel .subpanel-head").first
        await expect(head).to_have_attribute("aria-expanded", "false")
        await head.focus()
        await head.press("Enter")
        await expect(head).to_have_attribute("aria-expanded", "true")
        await head.press("Space")
        await expect(head).to_have_attribute("aria-expanded", "false")

    async def test_expanded_sensor_forms_fit_mobile_and_sidebar_widths(self):
        for head in await self.page.locator(".subpanel-head:visible").all():
            await head.click(position={"x": 5, "y": 5})
        card = self.page.locator(".address-editor-card").first
        await card.locator(".power-enabled").check()
        await card.locator(".power-mode").select_option("loss_plus_voltage")
        await card.locator(".voltage-entity-select").select_option(
            "sensor.demo_voltage"
        )
        await card.locator(".voltage-entity-label").fill("ДовгаМіткаБезПробілів" * 12)
        await card.locator('[data-action="add-entity-item"]').click()
        for width in (320, 375, 768, 900, 1440):
            await self.page.set_viewport_size({"width": width, "height": 900})
            await self.page.clock.run_for(100)
            overflow = await self.page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"
            )
            self.assertFalse(overflow, f"Expanded forms overflow at {width}px")
            # Also catch children clipped by overflow:hidden on section containers.
            clipped = await self.page.locator(
                ".subpanel-body input:visible, .subpanel-body select:visible, .subpanel-body button:visible"
            ).evaluate_all(
                """nodes => nodes.filter(node => {
                const box = node.getBoundingClientRect();
                const panel = node.closest('.subpanel').getBoundingClientRect();
                return box.left < panel.left - 1 || box.right > panel.right + 1;
            }).map(node => node.className)"""
            )
            self.assertEqual(clipped, [], f"Clipped controls at {width}px")
            artifact_dir = os.environ.get("UI_SCREENSHOT_DIR")
            if artifact_dir and width in (375, 1440):
                await self.page.screenshot(
                    path=str(Path(artifact_dir) / f"expanded-{width}.png"),
                    full_page=True,
                )

    async def test_malformed_save_response_never_acknowledges_or_discards_draft(self):
        field = self.page.locator(".addr-display-name").first
        await field.fill("Не втрачати")
        for response in ("<html>Session expired</html>", "{}"):
            self.malformed["config/save"] = response
            await self.page.locator("#save-button").click()
            await expect(self.page.locator("#editor-status")).to_have_class(
                "status-message status-bad"
            )
            await expect(self.page.locator("#save-button")).to_be_enabled()
            await expect(field).to_have_value("Не втрачати")
            await self.refresh()
            await expect(field).to_have_value("Не втрачати")
        await self.page.reload()
        await expect(field).to_have_value("Не втрачати")

    async def test_request_timeout_unlocks_controls_and_preserves_draft(self):
        gate = self.delay("config/save")
        field = self.page.locator(".addr-display-name").first
        await field.fill("Без відповіді")
        await self.page.locator("#save-button").click()
        await self.wait_started("config/save")
        await self.page.clock.run_for(30001)
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Час очікування"
        )
        await expect(self.page.locator("#save-button")).to_be_enabled()
        await expect(field).to_have_value("Без відповіді")
        gate.set()
        await self.refresh()
        await expect(field).to_have_value("Без відповіді")

    async def test_personal_fields_survive_refresh_and_switch_without_replacing_draft(self):
        form = self.page.locator('.address-editor-card').first
        await form.locator('.addr-mode').select_option('personal')
        await form.locator('.addr-user-id').fill('123456789')
        await form.locator('.addr-allow-readings').check()
        await expect(form.locator('.addr-entity-prefix')).to_be_hidden()
        await self.refresh()
        await expect(form.locator('.addr-user-id')).to_have_value('123456789')
        await expect(form.locator('.addr-allow-readings')).to_be_checked()
        await form.locator('.addr-mode').select_option('building')
        await expect(form.locator('.addr-entity-prefix')).to_be_visible()
        await expect(form.locator('.addr-entity-prefix')).to_have_value('demo_a')
        await form.locator('.addr-mode').select_option('personal')
        await expect(form.locator('.addr-user-id')).to_have_value('123456789')
        for width in (360, 768, 1366):
            await self.page.set_viewport_size({"width": width, "height": 900})
            self.assertTrue(await self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        self.assertEqual(self.errors, [])

    async def test_welcome_preference_is_saved_and_clone_has_independent_identity(self):
        await self.page.locator('#cfg-welcome').uncheck()
        await self.page.locator('#cfg-receive-commands').uncheck()
        await self.page.locator('#save-button').click()
        await self.wait_started('config/save')
        body = next(body for endpoint, body in reversed(self.calls) if endpoint == 'config/save')
        self.assertIs(body['config']['welcome_enabled'], False)
        self.assertIs(body['config']['receive_commands'], False)
        await self.page.locator('[data-action="clone-address"]').first.click()
        profiles = self.page.locator('.address-editor-card')
        a = await profiles.nth(0).get_attribute('data-profile-id')
        b = await profiles.nth(1).get_attribute('data-profile-id')
        self.assertNotEqual(a,b)
        self.assertTrue(b)
        self.assertEqual(self.errors, [])

    async def test_recipient_selection_is_scoped_and_refresh_never_replaces_typed_id(self):
        self.bot_profiles["profiles"].append({"id": "bot_2", "name": "Second bot"})
        self.bot_profiles["recipients"] = {
            "default": [
                {"id": "123", "type": "private", "name": "User <img src=x>", "username": "person", "available": True},
                {"id": "-1001", "type": "channel", "name": "House", "available": True},
                {"id": "456", "type": "private", "name": "Blocked", "available": False},
            ],
            "bot_2": [{"id": "789", "type": "private", "name": "Second user", "available": True}],
        }
        await self.refresh()
        form = self.page.locator('.address-editor-card').first
        await form.locator('.addr-mode').select_option('personal')
        await form.locator('.addr-user-id').fill('999')
        await self.refresh()
        picker = form.locator('.recipient-picker[data-private-only="true"]')
        await expect(form.locator('.addr-user-id')).to_have_value('999')
        await expect(picker.locator('option[value="-1001"]')).to_have_count(0)
        await expect(picker.locator('option[value="456"]')).to_have_attribute('disabled', '')
        await expect(form.locator('img')).to_have_count(0)
        await picker.select_option('123')
        await expect(form.locator('.addr-user-id')).to_have_value('123')
        await form.locator('.addr-bot-id').select_option('bot_2')
        await expect(picker.locator('option[value="123"]')).to_have_count(0)
        await expect(picker.locator('option[value="789"]')).to_have_count(1)
        await expect(form.locator('.addr-user-id')).to_have_value('123')
        await picker.select_option('789')
        await self.refresh()
        await expect(form.locator('.addr-user-id')).to_have_value('789')
        await form.locator('.addr-mode').select_option('building')
        await form.locator('.addr-bot-id').select_option('default')
        await form.locator('.recipient-picker[data-target=".addr-chat-id"]').select_option('-1001')
        await expect(form.locator('.addr-chat-id')).to_have_value('-1001')
        for width in (360, 768):
            await self.page.set_viewport_size({"width": width, "height": 900})
            self.assertTrue(await self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))

    async def test_bot_recipient_options_do_not_reuse_another_tokens_directory(self):
        self.bot_profiles["profiles"].append({"id": "bot_2", "name": "Second bot", "chat_id": ""})
        self.bot_profiles["recipients"] = {"bot_2": [{"id": "-1002", "type": "channel", "name": "Second channel", "available": True}]}
        await self.refresh()
        await self.page.locator('[data-tab="bots"]').click()
        await self.page.locator('[data-edit-bot="bot_2"]').click()
        picker = self.page.locator('.recipient-picker[data-target="#bot-chat"]')
        await picker.select_option('-1002')
        await expect(self.page.locator('#bot-chat')).to_have_value('-1002')
        await self.page.locator('#bot-token').fill('777:NEW_TEST_TOKEN')
        await expect(picker.locator('option[value="-1002"]')).to_have_count(0)
        await self.refresh()
        await expect(self.page.locator('#bot-chat')).to_have_value('-1002')
        await expect(self.page.locator('#bot-token')).to_have_value('777:NEW_TEST_TOKEN')

    async def test_generic_power_binary_is_saved_without_quality_sensor(self):
        form = self.page.locator('.address-editor-card').first
        await form.locator('.power-enabled').check()
        await form.locator('.power-mode').select_option('sources')
        row = form.locator('.power-source').first
        await row.locator('.source-entity').select_option('binary_sensor.city_grid')
        await row.locator('.source-present-state').select_option('off')
        await self.refresh()
        await expect(row.locator('.source-present-state')).to_have_value('off')
        await self.page.locator('#save-button').click()
        await self.wait_started('config/save')
        data = next(body['config']['addresses'][0] for endpoint, body in reversed(self.calls) if endpoint=='config/save')
        model = AddressConfig.model_validate(data)
        self.assertEqual(model.power.mode, 'sources')
        self.assertEqual(model.power.sources[0].kind, 'binary')
        self.assertEqual(model.power.sources[0].freshness, 'state')
        self.assertEqual(model.power.sources[0].present_state, 'off')
        self.assertEqual(model.voltage.entities, [])

    async def test_generic_pair_infers_units_and_keeps_advanced_draft(self):
        form = self.page.locator('.address-editor-card').first
        await form.locator('.power-enabled').check()
        await form.locator('.power-mode').select_option('sources')
        first, second = form.locator('.power-source').nth(0), form.locator('.power-source').nth(1)
        await first.locator('.source-entity').select_option('sensor.grid_hz')
        await second.locator('.source-entity').select_option('sensor.demo_voltage')
        await second.locator('.source-availability').select_option('mains_powered')
        await first.locator('summary').click()
        await first.locator('.source-max-age').fill('75')
        await first.locator('.source-threshold').fill('2.5')
        await self.refresh()
        await expect(first.locator('.source-max-age')).to_have_value('75')
        await expect(first.locator('.source-threshold')).to_have_value('2.5')
        await expect(second.locator('.source-kind')).to_have_value('voltage')
        await expect(second.locator('.source-threshold')).to_have_value('50')
        await self.page.locator('#validate-button').click()
        await self.wait_started('config/validate')
        data = next(body['config']['addresses'][0] for endpoint, body in reversed(self.calls) if endpoint=='config/validate')
        model=AddressConfig.model_validate(data)
        self.assertEqual([s.kind for s in model.power.sources], ['frequency','voltage'])
        self.assertEqual(model.power.sources[0].max_age_seconds,75)
        self.assertEqual(model.power.sources[1].availability,'mains_powered')
        for width in (320, 375, 768, 1440):
            await self.page.set_viewport_size({'width':width,'height':900})
            self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_new_profile_wizard_preserves_navigation_reload_and_applies_explicitly(self):
        self.candidates=[{'entity_prefix':'new_house','friendly_name':'Новий будинок','entities':{}}]
        await self.refresh()
        await self.page.locator('#add-address-button').click()
        form=self.page.locator('.wizard-profile')
        await expect(form.locator('.addr-entity-prefix')).to_have_value('new_house')
        await form.locator('.addr-display-name').fill('Мій профіль')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('.addr-chat-id').fill('555123')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('.feature-emergency').uncheck()
        await self.page.reload()
        form=self.page.locator('.wizard-profile')
        await expect(form.locator('[data-stage="2"]')).to_be_visible()
        await expect(form.locator('.feature-emergency')).not_to_be_checked()
        await form.locator('[data-action="wizard-back"]').click()
        await expect(form.locator('.addr-chat-id')).to_have_value('555123')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('[data-action="wizard-next"]').click()
        await expect(form.locator('[data-stage="3"]')).to_contain_text('555123')
        self.assertFalse(any(e=='config/save' for e,_ in self.calls))
        await form.locator('[data-action="wizard-apply"]').click()
        await self.wait_started('config/save')
        data=next(b for e,b in reversed(self.calls) if e=='config/save')
        self.assertTrue(data['restart'])
        model=AddressConfig.model_validate(data['config']['addresses'][-1])
        self.assertEqual(model.display_name,'Мій профіль')
        self.assertFalse(model.emergency.enabled)
        self.assertTrue(model.profile_id)

    async def test_personal_wizard_requires_explicit_owner_even_with_discovered_recipient(self):
        self.accounts=[{'entity_id':'sensor.example_account','masked':'••••1234','address':'Приклад адреси'}]
        await self.refresh()
        await self.page.locator('#add-address-button').click()
        form=self.page.locator('.wizard-profile')
        await form.locator('.addr-mode').select_option('personal')
        await form.locator('.addr-account').select_option('sensor.example_account')
        await form.locator('[data-action="wizard-next"]').click()
        await expect(form.locator('.addr-user-id')).to_have_value('')
        await form.locator('[data-action="wizard-step"][data-step="3"]').click()
        await expect(form.locator('[data-stage="1"]')).to_be_visible()
        await form.locator('.addr-user-id').fill('123456')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('[data-action="wizard-next"]').click()
        await expect(form.locator('[data-stage="3"]')).to_contain_text('123456')
        self.assertFalse(any(e=='config/save' for e,_ in self.calls))

    async def test_switching_profiles_keeps_unsaved_fields_and_only_one_editor(self):
        first,second=self.page.locator('.address-editor-card').nth(0),self.page.locator('.address-editor-card').nth(1)
        await first.locator('.addr-display-name').fill('Чернетка першого')
        await second.locator('[data-action="edit-profile"]').click()
        await expect(first.locator('.profile-editor')).to_be_hidden()
        await second.locator('.addr-display-name').fill('Чернетка другого')
        await self.refresh()
        await first.locator('[data-action="edit-profile"]').click()
        await expect(first.locator('.addr-display-name')).to_have_value('Чернетка першого')
        await expect(second.locator('.addr-display-name')).to_have_value('Чернетка другого')
        await expect(self.page.locator('.profile-editor:visible')).to_have_count(1)

    async def test_existing_profile_has_same_steps_and_keeps_edits_across_navigation(self):
        form = self.page.locator('.address-editor-card').first
        await form.locator('[data-action="show-all"]').click()
        await expect(form.locator('[data-stage="0"]')).to_be_visible()
        await expect(form.locator('[data-stage="1"]')).to_be_hidden()
        await form.locator('.addr-display-name').fill('Назва в чернетці')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('.addr-chat-id').fill('567890')
        await form.locator('[data-action="wizard-step"][data-step="3"]').click()
        await expect(form.locator('.profile-review')).to_contain_text('567890')
        await self.refresh()
        await self.page.reload()
        await self.page.locator('[data-action="edit-profile"]').first.click()
        await expect(form.locator('[data-stage="3"]')).to_be_visible()
        await form.locator('[data-action="wizard-step"][data-step="0"]').click()
        await expect(form.locator('.addr-display-name')).to_have_value('Назва в чернетці')
        self.assertFalse(any(e == 'config/save' for e,_ in self.calls))

    async def test_inline_bot_onboarding_keeps_incomplete_profile_and_token_private(self):
        self.candidates=[{'entity_prefix':'demo_a','friendly_name':'Будинок A','entities':{}}]
        await self.refresh()
        await self.page.locator('#add-address-button').click()
        form = self.page.locator('.wizard-profile')
        await form.locator('.addr-entity-prefix').select_option('demo_a')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('[data-action="connect-bot"]').click()
        await self.page.locator('#bot-name').fill('Новий бот')
        await self.page.locator('#bot-token').fill('654321:secret-for-browser-test')
        await self.refresh_via_poll()
        await expect(self.page.locator('#bot-token')).to_have_value('654321:secret-for-browser-test')
        await self.page.locator('#bot-form button[type="submit"]').click()
        await expect(self.page.locator('#bot-dialog')).not_to_be_visible()
        await expect(form.locator('.addr-bot-id')).to_have_value('bot_654321')
        await expect(form.locator('.telegram-progress')).to_contain_text('збережений')
        await form.locator('[data-action="activate-bots"]').click()
        await expect(self.page.locator('#editor-status')).to_contain_text('чернетку не застосовано')
        self.assertFalse(any(e == 'config/save' for e,_ in self.calls))
        self.assertNotIn('secret-for-browser-test', await self.page.evaluate('JSON.stringify(sessionStorage)'))
        self.bot_profiles.update(restart_required=False, receiving=True,
            runtime=[{'id':'bot_654321','ready':True}],
            recipients={'bot_654321':[{'id':'567890','type':'private','name':'Власник тесту','available':True}]})
        await form.locator('[data-action="refresh-recipients"]').click()
        await expect(form.locator('.recipient-picker[data-target=".addr-chat-id"] option')).to_have_count(2)
        await form.locator('.recipient-picker[data-target=".addr-chat-id"]').select_option('567890')
        await expect(form.locator('.profile-summary')).to_contain_text('Власник тесту')
        await form.locator('[data-action="wizard-step"][data-step="3"]').click()
        await expect(form.locator('.profile-review')).to_contain_text('Новий бот')
        await expect(form.locator('.profile-review')).to_contain_text('Власник тесту')

    async def refresh_via_poll(self):
        await self.page.clock.run_for(15001)
        await expect(self.page.locator('#refresh-button')).to_be_enabled()

    async def test_personal_review_names_owner_sensors_and_confirmation(self):
        self.accounts=[{'entity_id':'sensor.test_account','masked':'••••1234','address':'Тестова адреса'}]
        self.bot_profiles['recipients']={'default':[{'id':'678901','name':'Ірина','type':'private','available':True}]}
        await self.refresh()
        await self.page.locator('#add-address-button').click()
        form=self.page.locator('.wizard-profile')
        await form.locator('.addr-mode').select_option('personal')
        await form.locator('.addr-account').select_option('sensor.test_account')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('.recipient-picker[data-target=".addr-user-id"]').select_option('678901')
        await form.locator('[data-action="wizard-next"]').click()
        await form.locator('.addr-allow-readings').check()
        await form.locator('.power-enabled').check()
        await form.locator('.source-entity').first.select_option('binary_sensor.city_grid')
        await form.locator('[data-action="wizard-next"]').click()
        review=form.locator('.profile-review')
        for text in ('••••1234', 'Ірина', '678901', 'Міська мережа', 'окремого підтвердження'):
            await expect(review).to_contain_text(text)
        await expect(review).not_to_contain_text('undefined')
        for width in (320,375,768,1440):
            await self.page.set_viewport_size({'width':width,'height':900})
            self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_summary_refreshes_recipient_while_preserving_focused_draft(self):
        form=self.page.locator('.address-editor-card').first
        await form.locator('.addr-chat-id').fill('456789')
        await form.locator('.addr-display-name').fill('Незбережена назва')
        self.bot_profiles['recipients']={'default':[{'id':'456789','type':'private','name':'Олена','available':True}]}
        await self.refresh_via_poll()
        await expect(form.locator('.profile-summary')).to_contain_text('Олена')
        await expect(form.locator('.addr-display-name')).to_have_value('Незбережена назва')
        await expect(form.locator('.addr-display-name')).to_be_focused()

    async def test_unsaved_bot_token_blocks_apply_instead_of_silently_ignoring_it(self):
        await self.page.locator('[data-tab="bots"]').click()
        await self.page.locator('#bot-name').fill('Не збережено')
        await self.page.locator('#bot-token').fill('123456:do-not-ignore')
        await self.page.locator('[data-tab="configure"]').click()
        await self.page.locator('#save-restart-button').click()
        await expect(self.page.locator('#editor-status')).to_contain_text('Токен ще не збережений')
        self.assertFalse(any(e == 'config/save' for e,_ in self.calls))

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
        await self.context.route("**/*", self.route)
        await self.page.clock.install()
        await self.page.goto("http://dtek-ui.test/ingress/instance-a/")
        await expect(self.page.locator(".addr-display-name").first).to_have_value(
            "Будинок A"
        )

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
                "candidates": [],
                "catalog": {
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

    async def test_validate_and_refresh_keep_dirty_draft_after_page_reload(self):
        field = self.page.locator(".addr-display-name").first
        await field.fill("Моя чернетка")
        await self.page.locator("#validate-button").click()
        await expect(self.page.locator("#editor-status")).to_contain_text(
            "Конфіг валідний"
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
            "Конфіг збережено"
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
            "Конфіг збережено"
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
        for head in await self.page.locator(".subpanel-head").all():
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

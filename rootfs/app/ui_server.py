"""Ingress UI server for add-on overview, diagnostics, and config editing."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web
from pydantic import ValidationError

from config import (
    build_runtime_config_payload,
    save_runtime_config_payload,
    validate_runtime_config_payload,
)

if TYPE_CHECKING:
    from main import Orchestrator

logger = logging.getLogger(__name__)

UI_PORT = 8099
ALLOWED_IPS = {"127.0.0.1", "::1", "172.30.32.1", "172.30.32.2"}
WEB_ROOT = Path(__file__).resolve().parent / "web"


@web.middleware
async def ingress_only_middleware(
    request: web.Request,
    handler: web.Handler,
) -> web.StreamResponse:
    """Allow UI access only from the HA ingress gateway and local loopback."""
    remote = request.remote or ""
    if remote not in ALLOWED_IPS:
        raise web.HTTPForbidden(text="Ingress access only")
    return await handler(request)


class UIServer:
    """Small aiohttp server used for add-on ingress pages and API."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator
        self._app = web.Application(middlewares=[ingress_only_middleware])
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._configure_routes()

    def _configure_routes(self) -> None:
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/app.js", self._asset)
        self._app.router.add_get("/styles.css", self._asset)
        self._app.router.add_get("/healthz", self._healthz)
        self._app.router.add_get("/api/config", self._config)
        self._app.router.add_get("/api/overview", self._overview)
        self._app.router.add_get("/api/discovery", self._discovery)
        self._app.router.add_get("/api/diagnostics", self._diagnostics)
        self._app.router.add_get("/api/templates", self._templates)
        self._app.router.add_post("/api/config/validate", self._validate_config)
        self._app.router.add_post("/api/config/save", self._save_config)
        self._app.router.add_post("/api/templates/preview", self._preview_template)
        self._app.router.add_post("/api/templates/send-test", self._send_template_test)
        self._app.router.add_post("/api/templates/save", self._save_template)
        self._app.router.add_post("/api/templates/reset", self._reset_template)

    async def start(self) -> None:
        """Start the ingress UI server."""
        if self._runner is not None:
            return

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="0.0.0.0", port=UI_PORT)
        await self._site.start()
        logger.info("Ingress UI listening on port %d", UI_PORT)

    async def stop(self) -> None:
        """Stop the ingress UI server."""
        if self._runner is None:
            return

        await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def _index(self, request: web.Request) -> web.FileResponse:
        return self._file_response(WEB_ROOT / "index.html")

    async def _asset(self, request: web.Request) -> web.FileResponse:
        name = request.match_info.route.resource.canonical
        return self._file_response(WEB_ROOT / name.lstrip("/"))

    @staticmethod
    async def _healthz(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _config(self, request: web.Request) -> web.Response:
        return web.json_response(await self._orchestrator.get_editor_config())

    async def _overview(self, request: web.Request) -> web.Response:
        return web.json_response(await self._orchestrator.get_overview())

    async def _discovery(self, request: web.Request) -> web.Response:
        return web.json_response(await self._orchestrator.discover_addresses())

    async def _diagnostics(self, request: web.Request) -> web.Response:
        return web.json_response(await self._orchestrator.get_diagnostics())

    async def _templates(self, request: web.Request) -> web.Response:
        return web.json_response(self._orchestrator.get_templates_snapshot())

    async def _validate_config(self, request: web.Request) -> web.Response:
        payload = await self._read_config_payload(request)
        try:
            validated = validate_runtime_config_payload(
                payload,
                options=self._validation_options(),
            )
        except ValidationError as exc:
            return web.json_response(
                {"ok": False, "errors": exc.errors()},
                status=400,
            )

        return web.json_response(
            {
                "ok": True,
                "config": build_runtime_config_payload(validated),
                "preview": await self._orchestrator.preview_runtime_config(validated),
            }
        )

    async def _save_config(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        payload = body.get("config", {})
        restart = bool(body.get("restart", False))

        try:
            validated = validate_runtime_config_payload(
                payload,
                options=self._validation_options(),
            )
        except ValidationError as exc:
            return web.json_response(
                {"ok": False, "errors": exc.errors()},
                status=400,
            )

        normalized = build_runtime_config_payload(validated)
        target = save_runtime_config_payload(normalized)
        if restart:
            asyncio.create_task(self._delayed_restart())

        return web.json_response(
            {
                "ok": True,
                "config": normalized,
                "preview": await self._orchestrator.preview_runtime_config(validated),
                "path": str(target),
                "restart_scheduled": restart,
            }
        )

    async def _preview_template(self, request: web.Request) -> web.Response:
        try:
            validated, template_name, address_index, source_override = await self._read_template_request(request)
            preview = await self._orchestrator.preview_template(
                validated,
                template_name=template_name,
                address_index=address_index,
                source_override=source_override,
            )
        except ValidationError as exc:
            return web.json_response({"ok": False, "errors": exc.errors()}, status=400)
        except ValueError as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=400)

        return web.json_response({"ok": True, "preview": preview})

    async def _send_template_test(self, request: web.Request) -> web.Response:
        try:
            validated, template_name, address_index, source_override = await self._read_template_request(request)
            result = await self._orchestrator.send_template_test(
                validated,
                template_name=template_name,
                address_index=address_index,
                source_override=source_override,
            )
        except ValidationError as exc:
            return web.json_response({"ok": False, "errors": exc.errors()}, status=400)
        except ValueError as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=400)

        return web.json_response({"ok": True, "preview": result})

    async def _save_template(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        template_name = str(body.get("template_name", "")).strip()
        source = str(body.get("source", ""))
        try:
            details = self._orchestrator.save_template_override(template_name, source)
        except ValueError as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=400)
        return web.json_response({"ok": True, "template": details})

    async def _reset_template(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        template_name = str(body.get("template_name", "")).strip()
        try:
            details = self._orchestrator.reset_template_override(template_name)
        except ValueError as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=400)
        return web.json_response({"ok": True, "template": details})

    async def _read_config_payload(self, request: web.Request) -> dict[str, object]:
        body = await self._read_json_body(request)
        return body.get("config", body)

    async def _read_template_request(
        self,
        request: web.Request,
    ) -> tuple[object, str, int, str | None]:
        body = await self._read_json_body(request)
        payload = body.get("config", {})
        template_name = str(body.get("template_name", "")).strip()
        try:
            address_index = int(body.get("address_index", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("address_index must be an integer") from exc
        source_override = body.get("source_override")
        if not template_name:
            raise ValueError("template_name is required")
        validated = validate_runtime_config_payload(
            payload,
            options=self._validation_options(),
        )
        if source_override is not None:
            source_override = str(source_override)
        return validated, template_name, address_index, source_override

    @staticmethod
    async def _read_json_body(request: web.Request) -> dict[str, object]:
        try:
            body = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(text=f"Invalid JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON body must be an object")
        return body

    @staticmethod
    def _addon_slug() -> str:
        hostname = os.environ.get("HOSTNAME", "local-dtek-telegram-bot")
        return hostname.replace("-", "_")

    @staticmethod
    def _file_response(path: Path) -> web.FileResponse:
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    def _validation_options(self) -> dict[str, str]:
        return {
            "telegram_bot_token": self._orchestrator.config.telegram_bot_token,
            "telegram_chat_id": self._orchestrator.config.telegram_chat_id,
        }

    @staticmethod
    def _supervisor_token() -> str:
        return os.environ.get("SUPERVISOR_TOKEN", "") or os.environ.get("HASSIO_TOKEN", "")

    async def _delayed_restart(self) -> None:
        await asyncio.sleep(0.75)
        token = self._supervisor_token()
        if not token:
            logger.error("Cannot restart add-on from UI: supervisor token is missing")
            return

        slug = self._addon_slug()
        url = f"http://supervisor/addons/{slug}/restart"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status >= 400:
                        logger.error(
                            "Supervisor restart request failed for %s with %d",
                            slug,
                            resp.status,
                        )
                    else:
                        logger.info("Restart requested via UI for %s", slug)
        except Exception:
            logger.exception("Failed to restart add-on via Supervisor API")

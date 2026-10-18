"""Independent Telegram clients, persisted routes and serial workers per bot."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from bot_profiles import BotProfileStore, default_profile
from delivery_journal import DeliveryJournal
from state_store import StateStore
from telegram_service import TelegramService

logger = logging.getLogger(__name__)


class RoutedState:
    """Keep each address's acknowledgements and status IDs in its delivery route."""

    def __init__(self, store: StateStore, routes: dict[str, str]):
        self.store, self.routes = store, routes

    def get(self, prefix, key, default=None):
        return self.store.get(self.routes.get(prefix, prefix), key, default)

    def set(self, prefix, key, value):
        self.store.set(self.routes.get(prefix, prefix), key, value)

    def get_all(self, prefix):
        return self.store.get_all(self.routes.get(prefix, prefix))

    async def save(self):
        await self.store.save()

    async def flush(self):
        await self.store.flush()


class BotRuntime:
    def __init__(self, config, store: BotProfileStore, data_dir=Path("/data")):
        self.config, self.store, self.data_dir = config, store, data_dir
        self.profiles = {"default": default_profile(config), **store.read()}
        self.clients = {}
        self.errors = {}
        self._queues = {}
        self._workers = {}
        self._tick_pending = set()
        self._pending_events = {}
        self._feedback_starts = []
        self._base_state = StateStore(data_dir / "state.json")
        self.state = RoutedState(self._base_state, {})
        used_ids = set()
        for profile in self.profiles.values():
            if not profile.token:
                continue
            if profile.test_mode and not profile.test_chat_id:
                self.errors[profile.id] = (
                    "Тестовий режим призупинено: не задано тестовий чат."
                )
                continue
            if profile.bot_id in used_ids:
                self.errors[profile.id] = "Цього бота вже використовує інший профіль."
                continue
            try:
                self.clients[profile.id] = TelegramService(
                    profile.token,
                    profile.chat_id,
                    test_chat_id=profile.test_chat_id if profile.test_mode else "",
                )
                used_ids.add(profile.bot_id)
            except Exception:
                self.errors[profile.id] = (
                    "Некоректний токен. Перевірте налаштування бота."
                )
        for address in config.addresses:
            profile = self.profiles.get(address.telegram_bot_id)
            if profile:
                chat = (
                    (profile.test_chat_id if profile.test_mode else "")
                    or address.telegram_chat_id
                    or profile.chat_id
                )
                scope = f"{profile.bot_id}:{profile.test_mode}:{chat}:{address.entity_prefix}"
                self.state.routes[address.entity_prefix] = (
                    "route:" + hashlib.sha256(scope.encode()).hexdigest()
                )

    def service(self, address):
        return self.clients.get(address.telegram_bot_id)

    def chat(self, address):
        service = self.service(address)
        return service.resolve_chat_id(address.telegram_chat_id) if service else ""

    async def start(self):
        self._base_state.load()
        # Claim the old single-bot state exactly once. Changing the default token
        # later must never adopt another bot's message IDs or delivery journal.
        legacy_owner = self._base_state.get("_routing", "legacy_owner")
        default = self.profiles["default"]
        migrate = legacy_owner in (None, default.bot_id) and "default" in self.clients
        if migrate:
            for address in self.config.addresses:
                if address.telegram_bot_id != "default":
                    continue
                legacy_dir = self._legacy_dir(default)
                legacy = self._base_state
                if default.test_chat_id:
                    legacy = StateStore(legacy_dir / "state.json")
                    legacy.load()
                values = legacy.get_all(address.entity_prefix)
                stored_chat = values.get("status_message_chat_id")
                if stored_chat and str(stored_chat) != self.chat(address):
                    continue
                if values.get("status_message_id") and not stored_chat:
                    # Older data without a chat owner cannot authorize an edit.
                    values.pop("status_message_id", None)
                route = self.state.routes[address.entity_prefix]
                if not self._base_state.get_all(route):
                    for key, value in values.items():
                        self.state.set(address.entity_prefix, key, value)
        for profile_id, client in self.clients.items():
            profile = self.profiles[profile_id]
            mode = (
                "test-" + hashlib.sha256(profile.test_chat_id.encode()).hexdigest()[:16]
                if profile.test_mode
                else "production"
            )
            path = self.data_dir / "bots" / profile.bot_id / mode / "telegram.sqlite"
            if migrate and profile_id == "default" and not path.exists():
                old = self._legacy_dir(default) / "telegram.sqlite"
                if old.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(sqlite3.connect(old)) as source, closing(
                        sqlite3.connect(path)
                    ) as target:
                        source.backup(target)
            client.journal = DeliveryJournal(path)
            client.allowed_routes = {
                a.entity_prefix: self.chat(a)
                for a in self.config.addresses
                if a.telegram_bot_id == profile_id
            }
            if profile.feedback_enabled:
                self._feedback_starts.append(
                    asyncio.create_task(self._start_feedback(profile, client))
                )
            # At most one pending event per watched entity and one pending tick.
            # A rate-limited bot cannot backpressure the shared HA event reader.
            self._queues[profile_id] = asyncio.Queue()
            self._pending_events[profile_id] = {}
            self._workers[profile_id] = asyncio.create_task(self._worker(profile_id))
        if legacy_owner is None:
            self._base_state.set(
                "_routing", "legacy_owner", default.bot_id if migrate else "disabled"
            )
        await self.state.save()

    def _legacy_dir(self, profile):
        namespace = hashlib.sha256(
            f"{profile.bot_id}:{profile.test_chat_id}".encode()
        ).hexdigest()[:16]
        return self.data_dir / (
            f"test-{namespace}" if profile.test_chat_id else "production"
        )

    async def _start_feedback(self, profile, client):
        try:
            async with asyncio.timeout(15):
                await client.start_feedback(
                    self.data_dir / f"feedback-{profile.bot_id}.lock"
                )
        except Exception as exc:
            self.errors[profile.id] = (
                "Збір зворотного зв’язку недоступний. Перевірте тестовий чат та інші запущені копії бота."
            )
            logger.warning(
                "Feedback unavailable for %s: %s", profile.id, type(exc).__name__
            )

    async def enqueue(self, profile_id, method, features, args=()):
        queue = self._queues.get(profile_id)
        if queue is None:
            return
        if method == "on_tick":
            if profile_id in self._tick_pending:
                return
            self._tick_pending.add(profile_id)
        if method == "on_state_change":
            entity_id, old_state, new_state = args
            pending = self._pending_events[profile_id]
            if entity_id in pending:
                first_old = pending[entity_id][1][1]
                pending[entity_id] = (features, (entity_id, first_old, new_state))
                return
            pending[entity_id] = (features, args)
            queue.put_nowait((method, None, entity_id))
        else:
            queue.put_nowait((method, features, args))

    async def _worker(self, profile_id):
        queue = self._queues[profile_id]
        while True:
            method, features, args = await queue.get()
            if method == "on_state_change":
                features, args = self._pending_events[profile_id].pop(args)
            try:
                for feature in features:
                    try:
                        await getattr(feature, method)(*args)
                    except Exception as exc:
                        logger.error(
                            "%s.%s failed for %s: %s",
                            feature.name,
                            method,
                            profile_id,
                            type(exc).__name__,
                        )
                if method == "on_tick":
                    await self.clients[profile_id].retry_pending(self.state)
            except Exception as exc:
                logger.error("Worker failed for %s: %s", profile_id, type(exc).__name__)
            finally:
                if method == "on_tick":
                    self._tick_pending.discard(profile_id)
                queue.task_done()

    async def stop_workers(self):
        for task in self._workers.values():
            task.cancel()
        await asyncio.gather(*self._workers.values(), return_exceptions=True)

    async def close(self):
        await self.stop_workers()
        for task in self._feedback_starts:
            task.cancel()
        await asyncio.gather(*self._feedback_starts, return_exceptions=True)
        await self.state.flush()
        await asyncio.gather(*(client.close() for client in self.clients.values()))

    def public(self):
        return [
            {
                **profile.public(),
                "ready": profile.id in self.clients,
                "error": self.errors.get(profile.id, ""),
                "delivery_error": getattr(
                    self.clients.get(profile.id), "last_error", ""
                ),
            }
            for profile in self.profiles.values()
        ]

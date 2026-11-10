"""Receive authenticated HA actions; reuse existing profiles and status rendering."""

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from features.status_message import StatusMessageFeature
from state_store import atomic_write

LOGGER = logging.getLogger(__name__)
PREFIX = "dtek_monitor/bot_status/"
ID = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


class ActionJob:
    name = "ha_status_action"

    def __init__(self, run, future):
        self.run_action, self.future = run, future

    async def run(self):
        if self.future.done():
            return
        try:
            result = await self.run_action()
        except Exception:
            LOGGER.exception("HA status action failed")
            result = {"status": "unknown", "recipients": 0}
        if not self.future.done():
            self.future.set_result(result)


class StatusActions:
    def __init__(self, app, data_dir: Path):
        self.app = app
        data_dir.mkdir(parents=True, exist_ok=True)
        identity = data_dir / "status_action_instance"
        if not identity.exists():
            atomic_write(identity, uuid4().hex)
        self.instance = identity.read_text().strip()
        if not ID.fullmatch(self.instance):
            raise ValueError("Invalid status action instance ID")
        self.db = sqlite3.connect(data_dir / "status_actions.sqlite")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, fingerprint TEXT, expires REAL, result TEXT);
            CREATE TABLE IF NOT EXISTS cooldown (profile TEXT PRIMARY KEY, last_send REAL);
        """)
        self._task = None
        self._generation = None

    def catalog(self):
        result = []
        for address in self.app.config.addresses:
            routes = self.routes(address.key)
            if not routes:
                continue
            bot = self.app.bots.profiles.get(address.telegram_bot_id)
            bot_name = getattr(bot, "name", "") or address.telegram_bot_id
            result.append({
                "id": address.key, "name": f"{address.display_name or address.key} · {bot_name}"[:256],
                "can_refresh": address.status_message.enabled and address.status_message.delivery_mode == "pinned_edit",
            })
        return result

    def routes(self, profile):
        # Only current, configured recipients. No chat/user/token in HA commands.
        return [a for a in self.app.deliveries if a.template_scope == profile
                and self.app.bots.service(a) is not None and self.app.bots.chat(a)]

    def start(self):
        self._task = asyncio.create_task(self._connect())

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.db.close()

    async def _connect(self):
        while True:
            try:
                await self.app.ha.wait_connected(timeout=None)
                generation = self.app.ha.connected_at
                if generation != self._generation:
                    result = await self.app.ha._send_command({
                        "type": PREFIX + "subscribe", "version": 1,
                        "instance_id": self.instance, "profiles": self.catalog(),
                    }, subscription_callback=self.handle)
                    if result.get("success"):
                        self._generation = generation
                        LOGGER.info("HA status actions connected (%d profiles)", len(self.catalog()))
                    else:
                        LOGGER.debug("HA status actions not available; update DTEK Monitor to enable them")
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.debug("HA status actions connection unavailable")
                await asyncio.sleep(30)

    async def handle(self, command):
        try:
            if (not isinstance(command, dict) or not ID.fullmatch(str(command.get("request_id", "")))
                    or not ID.fullmatch(str(command.get("profile_id", "")))
                    or command.get("operation") not in ("send", "refresh")
                    or not isinstance(command.get("silent"), bool)
                    or not isinstance(command.get("expires"), (int, float))
                    or not time.time() - 60 < command["expires"] <= time.time() + 60):
                return
            routes = self.routes(command["profile_id"])
            if not routes:
                result = {"status": "failed", "recipients": 0}
            else:
                future = asyncio.get_running_loop().create_future()
                generation = self.app.ha.connected_at
                job = ActionJob(lambda: self.execute(command, generation), future)
                await self.app.bots.enqueue(routes[0].telegram_bot_id, "run", [job])
                try:
                    result = await asyncio.wait_for(future, max(0.1, command["expires"] - time.time()))
                except TimeoutError:
                    result = {"status": "unknown", "recipients": 0}
            await self.app.ha.command(PREFIX + "result", instance_id=self.instance,
                                      request_id=command["request_id"], **result)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.debug("HA status action acknowledgement unavailable")

    async def execute(self, command, generation):
        request_id = command["request_id"]
        fingerprint = hashlib.sha256(json.dumps({k: command[k] for k in (
            "profile_id", "operation", "silent")}, sort_keys=True).encode()).hexdigest()
        prior = self.db.execute("SELECT fingerprint,result FROM requests WHERE id=?", (request_id,)).fetchone()
        if prior:
            return json.loads(prior[1]) if prior[0] == fingerprint else {"status": "failed", "recipients": 0}
        if (not self.app.ha.is_connected or self.app.ha.connected_at != generation
                or command["expires"] <= time.time()):
            return {"status": "expired", "recipients": 0}
        routes = self.routes(command["profile_id"])
        if not routes:
            return {"status": "failed", "recipients": 0}
        if command["operation"] == "send":
            last = self.db.execute("SELECT last_send FROM cooldown WHERE profile=?", (command["profile_id"],)).fetchone()
            if last and time.time() - last[0] < 10:
                return {"status": "rate_limited", "recipients": 0}
        # Commit before Telegram: a crash or lost acknowledgement must not replay a send.
        with self.db:
            self.db.execute("DELETE FROM requests WHERE expires<?", (time.time() - 86400,))
            self.db.execute("INSERT INTO requests VALUES(?,?,?,?)", (
                request_id, fingerprint, command["expires"], json.dumps({"status": "unknown", "recipients": 0})))
            if command["operation"] == "send":
                self.db.execute("INSERT OR REPLACE INTO cooldown VALUES(?,?)", (command["profile_id"], time.time()))
        outcomes = []
        for address in routes:
            if (command["expires"] <= time.time() or not self.app.ha.is_connected
                    or self.app.ha.connected_at != generation):
                outcomes.append("expired")
                continue
            if command["operation"] == "refresh":
                feature = next((f for f in self.app._features_by_address.get(address.key, [])
                                if isinstance(f, StatusMessageFeature)), None)
                outcome = await feature.refresh_from_automation(
                    command["expires"], valid=lambda: self.app.ha.is_connected and self.app.ha.connected_at == generation,
                ) if feature else "disabled"
            else:
                text = await self.app.render_status(address)
                if (command["expires"] <= time.time() or not self.app.ha.is_connected
                        or self.app.ha.connected_at != generation):
                    outcomes.append("expired")
                    continue
                if not text or "[Template error:" in text:
                    outcomes.append("failed")
                    continue
                message_id = await self.app.bots.service(address).send_message(
                    text, chat_id=address.telegram_chat_id or None,
                    disable_notification=command["silent"],
                    delivery_key=f"ha-status:{request_id}:{address.key}",
                    context={"feature": "automation_status", "address": address.key, "expires": command["expires"]},
                )
                outcome = "sent" if message_id else "unknown"
            outcomes.append(outcome)
        expected = "sent" if command["operation"] == "send" else "updated"
        count = outcomes.count(expected)
        status = expected if count == len(outcomes) else "partial" if count else outcomes[0]
        result = {"status": status, "recipients": count}
        with self.db:
            self.db.execute("UPDATE requests SET result=? WHERE id=?", (json.dumps(result), request_id))
        return result

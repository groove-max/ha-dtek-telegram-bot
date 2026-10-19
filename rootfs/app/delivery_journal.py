"""Bounded Telegram audit log and transactional incoming-update cursor.

Read via sqlite3 over SSH. This is delivery evidence, not a claim that a human
read a message. A pending request after a crash has an unknown delivery result.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from outage_calendar import canonical_warning_key


class DeliveryJournal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP,
                direction TEXT, action TEXT, chat_id TEXT, message_id INTEGER,
                status TEXT, payload TEXT, response TEXT);
            CREATE TABLE IF NOT EXISTS incoming (update_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS cursor (name TEXT PRIMARY KEY, value INTEGER);
            CREATE TABLE IF NOT EXISTS owned_messages (
                chat_id TEXT, message_id INTEGER, PRIMARY KEY(chat_id, message_id));
            CREATE TABLE IF NOT EXISTS deliveries (
                key TEXT PRIMARY KEY, scope TEXT, status TEXT, payload TEXT,
                message_id INTEGER, expires REAL, retry_at REAL DEFAULT 0);
        """)
        self.db.execute("UPDATE events SET status='unknown' WHERE status='pending'")
        self.db.execute("UPDATE deliveries SET status='unknown' WHERE status='pending'")
        self._migrate_upcoming_deliveries()
        self.db.commit()

    def _migrate_upcoming_deliveries(self) -> None:
        """Preserve sent/ambiguous outcomes when warnings stop including end times.

        Copy aliases instead of destroying the old evidence. Only records whose
        stored key matches their chat/address/stream can participate. Each bot
        profile and test/production route retains its own independent journal.
        """
        rows = self.db.execute(
            "SELECT key,status,payload,message_id,expires,retry_at FROM deliveries"
        ).fetchall()
        for old_key, status, raw, message_id, expires, retry_at in rows:
            try:
                payload = json.loads(raw)
                context = payload.get("context") or {}
                stream = context.get("stream")
                if context.get("feature") != "upcoming_outage":
                    continue
                canonical = canonical_warning_key(stream)
                chat, address = str(payload["chat"]), context["address"]
                if not canonical or canonical == stream or not isinstance(address, str):
                    continue
                expected = hashlib.sha256(f"{chat}:upcoming:{address}:{stream}".encode()).hexdigest()
                if old_key != expected:
                    continue
                key = hashlib.sha256(f"{chat}:upcoming:{address}:{canonical}".encode()).hexdigest()
                scope = f"{chat}:{address}:upcoming_outage:{canonical}"
                prior = self.delivery(key)
                if prior is None:
                    self.db.execute(
                        "INSERT INTO deliveries(key,scope,status,payload,message_id,expires,retry_at) VALUES(?,?,?,?,?,?,?)",
                        (key, scope, status, raw, message_id, expires, retry_at),
                    )
                elif prior[0] == "retry" and status in ("ok", "unknown", "rejected"):
                    self.db.execute(
                        "UPDATE deliveries SET status=?,message_id=? WHERE key=?",
                        (status, message_id, key),
                    )
            except (ValueError, TypeError, KeyError, AttributeError):
                continue

    def begin(
        self, action: str, chat: str, message_id: int | None, payload: dict
    ) -> int:
        with self.db:
            row = self.db.execute(
                "INSERT INTO events(direction,action,chat_id,message_id,status,payload) VALUES('out',?,?,?,'pending',?)",
                (
                    action,
                    str(chat),
                    message_id,
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )
        return int(row.lastrowid)

    def finish(
        self, row: int, status: str, response: Any = None, message_id: int | None = None
    ) -> None:
        with self.db:
            self.db.execute(
                "UPDATE events SET status=?,response=?,message_id=COALESCE(?,message_id) WHERE id=?",
                (
                    status,
                    json.dumps(response, ensure_ascii=False, default=str),
                    message_id,
                    row,
                ),
            )
            if status == "ok" and message_id is not None:
                self.db.execute(
                    "INSERT OR IGNORE INTO owned_messages SELECT chat_id,? FROM events WHERE id=? AND action='send'",
                    (message_id, row),
                )
            self._prune()

    def owns(self, chat: str, message_id: int) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM owned_messages WHERE chat_id=? AND message_id=?",
                (str(chat), message_id),
            ).fetchone()
            is not None
        )

    def delivery(self, key: str):
        return self.db.execute(
            "SELECT status,message_id FROM deliveries WHERE key=?", (key,)
        ).fetchone()

    def retry_due(self, key: str, now: float) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM deliveries WHERE key=? AND status='retry' AND retry_at<=? AND expires>?",
                (key, now, now),
            ).fetchone()
            is not None
        )

    def prepare_delivery(
        self, key: str, scope: str, payload: dict, expires: float
    ) -> None:
        with self.db:
            self.db.execute(
                "UPDATE deliveries SET status='superseded' WHERE scope=? AND key!=? AND status='retry'",
                (scope, key),
            )
            self.db.execute(
                "INSERT INTO deliveries(key,scope,status,payload,expires) VALUES(?,?,'pending',?,?) ON CONFLICT(key) DO UPDATE SET status='pending', payload=excluded.payload",
                (
                    key,
                    scope,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    expires,
                ),
            )

    def finish_delivery(self, key: str, status: str, message_id=None) -> None:
        import time

        with self.db:
            self.db.execute(
                "UPDATE deliveries SET status=?,message_id=?,retry_at=? WHERE key=?",
                (status, message_id, time.time() + 60, key),
            )

    def retries(self):
        import time

        with self.db:
            self.db.execute(
                "UPDATE deliveries SET status='expired' WHERE expires<? AND status='retry'",
                (time.time(),),
            )
            self.db.execute(
                "DELETE FROM deliveries WHERE expires<?", (time.time() - 86400,)
            )
        return [
            (key, json.loads(payload))
            for key, payload in self.db.execute(
                "SELECT key,payload FROM deliveries WHERE status='retry' AND retry_at<=?",
                (time.time(),),
            )
        ]

    @property
    def offset(self) -> int:
        row = self.db.execute(
            "SELECT value FROM cursor WHERE name='telegram'"
        ).fetchone()
        return int(row[0]) if row else 0

    def ingest(self, update_id: int, payload: dict, allowed_chat: str) -> None:
        """Commit the selected event and cursor together; discard other chat data."""
        with self.db:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO incoming VALUES(?)", (update_id,)
            ).rowcount
            if inserted:
                for kind in (
                    "message",
                    "edited_message",
                    "channel_post",
                    "edited_channel_post",
                ):
                    msg = payload.get(kind) or {}
                    if str(msg.get("chat", {}).get("id", "")) == str(allowed_chat):
                        self.db.execute(
                            "INSERT INTO events(direction,action,chat_id,message_id,status,payload) VALUES('in',?,?,?,'received',?)",
                            (
                                kind,
                                str(allowed_chat),
                                msg.get("message_id"),
                                json.dumps(payload, ensure_ascii=False),
                            ),
                        )
            self.db.execute(
                "INSERT INTO cursor VALUES('telegram',?) ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)",
                (update_id + 1,),
            )
            self.db.execute(
                "DELETE FROM incoming WHERE update_id < ?", (update_id - 10000,)
            )
            self._prune()

    def _prune(self) -> None:
        self.db.execute(
            "DELETE FROM events WHERE id < (SELECT MAX(id)-10000 FROM events)"
        )
        self.db.execute(
            "DELETE FROM owned_messages WHERE rowid < (SELECT MAX(rowid)-10000 FROM owned_messages)"
        )

    def close(self) -> None:
        self.db.close()

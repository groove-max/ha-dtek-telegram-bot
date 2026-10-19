"""Bounded recipient metadata learned from Bot API updates, never authorization."""

import time


class RecipientDirectory:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS recipients (
            chat_id TEXT PRIMARY KEY, type TEXT, name TEXT, username TEXT,
            available INTEGER, seen REAL, event_at REAL)""")

    def observe(self, payload, bot_id):
        member = payload.get("my_chat_member")
        message = next((payload.get(k) for k in (
            "message", "edited_message", "channel_post", "edited_channel_post"
        ) if payload.get(k)), None)
        callback = payload.get("callback_query") or {}
        if member:
            new = member.get("new_chat_member", {})
            if str(new.get("user", {}).get("id")) != str(bot_id):
                return
            status = new.get("status")
            available = status not in ("left", "kicked")
            if status == "restricted":
                available = bool(new.get("is_member"))
            event, chat = member, member.get("chat", {})
        elif message or callback.get("message"):
            event = message or callback["message"]
            chat = event.get("chat", {})
            sender = callback.get("from") or event.get("from") or {}
            if chat.get("type") == "private" and (
                sender.get("id") != chat.get("id") or sender.get("is_bot")
            ):
                return
            available = True
        else:
            return
        chat_id, kind = chat.get("id"), chat.get("type")
        if type(chat_id) is not int or kind not in ("private", "group", "supergroup", "channel"):
            return
        if (kind == "private" and chat_id <= 0) or (kind != "private" and chat_id >= 0):
            return
        now = time.time()
        event_at = event.get("date", now)
        if not isinstance(event_at, (int, float)):
            return
        name = chat.get("title") or " ".join(str(chat.get(k) or "") for k in ("first_name", "last_name")).strip()
        with self.db:
            self.db.execute("""INSERT INTO recipients VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET type=excluded.type,name=excluded.name,
                username=excluded.username,available=excluded.available,seen=excluded.seen,event_at=excluded.event_at
                WHERE excluded.event_at>=recipients.event_at""",
                (str(chat_id), kind, str(name)[:160], str(chat.get("username") or "")[:64], int(available), now, event_at))
            self.db.execute("DELETE FROM recipients WHERE chat_id NOT IN (SELECT chat_id FROM recipients ORDER BY seen DESC LIMIT 500)")

    def list(self):
        return [dict(zip(("id", "type", "name", "username", "available", "last_seen"), row))
                for row in self.db.execute("SELECT chat_id,type,name,username,available,seen FROM recipients ORDER BY seen DESC,chat_id")]

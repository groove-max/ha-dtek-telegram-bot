"""Expiring invitations and Telegram-native recipient selection; never grants access."""

import asyncio
import hashlib
import json
import re
import secrets
import time

from aiogram.types import (
    ChatAdministratorRights, KeyboardButton, KeyboardButtonRequestChat,
    KeyboardButtonRequestUsers, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

KINDS = {"invite", "users", "private", "group", "channel"}
CANCEL_PICKER = "✖️ Скасувати вибір"
USE_ME = "👤 Мій особистий чат"


def profile_binding(profile):
    return hashlib.sha256(json.dumps([
        profile.key, profile.mode, profile.telegram_bot_id,
        profile.account_entity if profile.mode == "personal" else profile.entity_prefix,
    ]).encode()).hexdigest()


class OnboardingStore:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS onboarding_links (
            id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, kind TEXT, profile_key TEXT,
            binding TEXT, pin INTEGER, created REAL, expires REAL, status TEXT,
            owner INTEGER, request_id INTEGER UNIQUE, result TEXT)""")
        db.commit()

    def create(self, kind, profile_key, binding="", pin=False):
        if kind not in KINDS or not re.fullmatch(r"[\w-]{1,128}", profile_key):
            raise ValueError("Оберіть джерело даних профілю перед створенням посилання.")
        now = time.time()
        token = secrets.token_urlsafe(24)
        row_id = secrets.token_hex(12)
        request_id = secrets.randbelow(2**31 - 1) + 1
        while self.db.execute("SELECT 1 FROM onboarding_links WHERE request_id=?", (request_id,)).fetchone():
            request_id = secrets.randbelow(2**31 - 1) + 1
        with self.db:
            self.db.execute("DELETE FROM onboarding_links WHERE expires<?", (now - 86400,))
            if self.db.execute("SELECT count(*) FROM onboarding_links").fetchone()[0] >= 200:
                raise ValueError("Забагато посилань. Дочекайтеся завершення строку попередніх.")
            self.db.execute("UPDATE onboarding_links SET status='revoked' WHERE profile_key=? AND kind=? AND status IN ('open','selecting')", (profile_key, kind))
            self.db.execute("INSERT INTO onboarding_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                row_id, hashlib.sha256(token.encode()).hexdigest(), kind, profile_key,
                binding, int(pin), now, now + (7 * 86400 if kind == "invite" else 900),
                "open", None, request_id, None,
            ))
        return token, self.get(row_id)

    def _one(self, sql, args):
        cursor = self.db.execute(sql, args)
        row = cursor.fetchone()
        return dict(zip([c[0] for c in cursor.description], row)) if row else None

    def get(self, row_id):
        return self._one("SELECT * FROM onboarding_links WHERE id=?", (row_id,))

    def by_token(self, token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
            return None
        return self._one("SELECT * FROM onboarding_links WHERE token_hash=? AND expires>? AND status IN ('open','selecting')",
                         (hashlib.sha256(token.encode()).hexdigest(), time.time()))

    def claim(self, row_id, user):
        with self.db:
            row = self.get(row_id)
            if not row or row["kind"] == "invite" or row["expires"] <= time.time() or row["status"] not in ("open", "selecting") or row["owner"] not in (None, user):
                return None
            self.db.execute("UPDATE onboarding_links SET status='cancelled' WHERE owner=? AND status='selecting' AND id!=?", (user, row_id))
            self.db.execute("UPDATE onboarding_links SET owner=?,status='selecting' WHERE id=?", (user, row_id))
        return self.get(row_id)

    def active(self, user, request_id=None, *, include_expired=False):
        sql = "SELECT * FROM onboarding_links WHERE owner=? AND status='selecting'"
        args = [user]
        if not include_expired:
            sql += " AND expires>?"
            args.append(time.time())
        if request_id is not None:
            sql += " AND request_id=?"
            args.append(request_id)
        return self._one(sql + " ORDER BY created DESC LIMIT 1", args)

    def finish(self, row_id, user, choices):
        with self.db:
            result = self.db.execute("UPDATE onboarding_links SET status='complete',result=? WHERE id=? AND owner=? AND status='selecting' AND expires>?",
                                     (json.dumps(choices, ensure_ascii=False), row_id, user, time.time()))
        return result.rowcount == 1

    def revoke(self, row_id):
        with self.db:
            self.db.execute("UPDATE onboarding_links SET status='revoked' WHERE id=?", (row_id,))

    def cancel(self, user):
        with self.db:
            self.db.execute("UPDATE onboarding_links SET status='cancelled' WHERE owner=? AND status='selecting'", (user,))

    def snapshot(self, directory):
        peers = {str(p["id"]): p for p in directory.list()}
        result = []
        for (row_id,) in self.db.execute("SELECT id FROM onboarding_links WHERE expires>? ORDER BY created DESC LIMIT 200", (time.time() - 86400,)):
            row = self.get(row_id)
            choices = json.loads(row["result"] or "[]")
            for choice in choices:
                if choice["type"] == "private":
                    peer = peers.get(str(choice["id"]))
                    choice["ready"] = bool(peer and peer["type"] == "private" and peer["available"])
                    choice["reason"] = "" if choice["ready"] else "Попросіть користувача відкрити бота й натиснути Start."
            result.append({k: row[k] for k in ("id", "kind", "profile_key", "binding", "created", "expires", "owner")}
                          | {"status": "expired" if row["expires"] <= time.time() else row["status"], "choices": choices})
        return result


class OnboardingNavigation:
    @property
    def store(self):
        return self.router.tg.journal.onboarding

    def __init__(self, router):
        self.router = router

    async def send(self, user, text, keyboard=None):
        self.router.invalidate_reading_buttons(user)
        # Also clean up setup screens for owners who have no account profile yet.
        return await self.router.tg.replace_menu(user, lambda: self.router.tg._bot.send_message(
            chat_id=user, text=text, reply_markup=keyboard or ReplyKeyboardRemove(),
        ))

    async def handle(self, user, sender, message):
        text = str(message.get("text") or "").strip()
        parts = text.split(maxsplit=1)
        if parts and len(parts) == 2 and parts[1].startswith("dtek_") and await self.router.parse_command(parts[0]) == "start":
            if not self.router.tg.journal.claim_interaction(f"onboard:{user}", 2):
                return True
            row = self.store.by_token(parts[1][5:])
            if not row:
                await self.send(user, "ℹ️ Посилання вже недійсне. Попросіть власника створити нове в налаштуваннях бота.")
            elif row["kind"] == "invite":
                await self.invite(user, sender, row)
            else:
                row = self.store.claim(row["id"], user)
                if row:
                    self.router.invalidate_reading_buttons(user)
                    await self.send(user, "🔎 Оберіть одержувача кнопкою нижче. Вибір з’явиться в Home Assistant; там його потрібно додати до чернетки та застосувати. Дані рахунку тут не відкриваються.\n\nЯкщо кнопки вибору немає, оновіть Telegram або введіть ID у HA вручну.", self.keyboard(row))
                else:
                    await self.send(user, "ℹ️ Це посилання вибору вже використовується. Створіть нове в HA.")
            return True
        shared = message.get("users_shared") or message.get("chat_shared")
        active = self.store.active(user, include_expired=True)
        if shared:
            request_id = shared.get("request_id")
            row = self.store.active(user, request_id) if type(request_id) is int else None
            if not row:
                await self.send(user, "ℹ️ Цей вибір уже неактивний. Відкрийте нове посилання з HA.")
                return True
            await self.selected(user, row, message)
            return True
        if active and text == USE_ME and active["kind"] in ("private", "users"):
            await self.selected(user, active, {"users_shared": {"users": [{"user_id": user, **{k: sender.get(k, "") for k in ("first_name", "last_name", "username")}}]}})
            return True
        if text == CANCEL_PICKER:
            self.store.cancel(user)
            await self.send(user, "Вибір одержувача скасовано. Чернетка показів залишилася без змін. /start — меню.")
            return True
        if active and text.startswith("/"):
            self.store.cancel(user)
            await self.send(user, "Вибір одержувача закрито. Повернутися до нього можна за новим посиланням із HA.")
        return False

    async def invite(self, user, sender, row):
        profile = next((p for p in self.router.app.config.addresses if p.key == row["profile_key"] and p.telegram_bot_id == self.router.bot_id and p.mode == "personal" and profile_binding(p) == row["binding"]), None)
        if not profile:
            await self.send(user, "ℹ️ Профіль цього запрошення змінився. Попросіть власника створити нове посилання.")
            return
        if user in profile.telegram_user_ids:
            await self.send(user, "✅ Доступ до цього профілю вже налаштовано. /start — відкрити меню.")
            return
        status = self.router.tg.journal.requests.request(sender, profile.key, row["binding"])
        reply = {"pending": "🔗 Заявку за запрошенням надіслано власнику. Профіль обрано автоматично; доступ з’явиться після його підтвердження.",
                 "declined": "ℹ️ Заявку відхилено. Зверніться до власника; повторна заявка можлива через добу.",
                 "accepted": "ℹ️ Попередню заявку прийнято, але зараз доступу немає. Зверніться до власника.",
                 "full": "ℹ️ Заявку зараз не вдалося створити. Зверніться до власника."}[status]
        await self.send(user, reply)

    @staticmethod
    def keyboard(row):
        kind = row["kind"]
        if kind in ("users", "private"):
            button = KeyboardButton(text="👥 Обрати користувачів" if kind == "users" else "👤 Обрати користувача", request_users=KeyboardButtonRequestUsers(
                request_id=row["request_id"], user_is_bot=False, max_quantity=10 if kind == "users" else 1,
                request_name=True, request_username=True,
            ))
            buttons = [[button], [KeyboardButton(text=USE_ME)]]
        else:
            required = {k: False for k, field in ChatAdministratorRights.model_fields.items() if field.is_required()}
            required["can_manage_chat"] = True
            if kind == "channel":
                required.update(can_post_messages=True, can_edit_messages=bool(row["pin"]))
            elif row["pin"]:
                required["can_pin_messages"] = True
            bot_rights = ChatAdministratorRights(**required)
            user_rights = bot_rights.model_copy(update={"can_promote_members": kind == "channel" or bool(row["pin"])})
            button = KeyboardButton(text="📣 Обрати канал" if kind == "channel" else "👥 Обрати групу", request_chat=KeyboardButtonRequestChat(
                request_id=row["request_id"], chat_is_channel=kind == "channel", request_title=True, request_username=True,
                bot_administrator_rights=bot_rights if kind == "channel" or row["pin"] else None,
                user_administrator_rights=user_rights,
                bot_is_member=True if kind == "group" and not row["pin"] else None,
            ))
            buttons = [[button]]
        buttons.append([KeyboardButton(text=CANCEL_PICKER)])
        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True, input_field_placeholder="Оберіть одержувача або скасуйте вибір")

    async def selected(self, user, row, message):
        try:
            async with asyncio.timeout(15):
                choices = await self.choices(user, row, message)
            if not self.store.finish(row["id"], user, choices):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            await self.send(user, "ℹ️ Вибір не відповідає запиту або вже застарів. Створіть нове посилання у HA.")
            return
        except Exception:
            await self.send(user, "ℹ️ Не вдалося перевірити доступ бота до чату. Перевірте його права й повторіть вибір кнопкою нижче.", self.keyboard(row))
            return
        await self.send(user, "✅ Вибір передано в Home Assistant. Поверніться до кроку Telegram, перевірте одержувачів, додайте їх до чернетки й натисніть «Застосувати». Нові користувачі мають спочатку відкрити бота та натиснути Start.")

    async def choices(self, user, row, message):
        if row["kind"] in ("users", "private"):
            users = message["users_shared"]["users"]
            if not isinstance(users, list) or not 1 <= len(users) <= (10 if row["kind"] == "users" else 1):
                raise ValueError
            choices = []
            for person in users:
                uid = person["user_id"]
                if type(uid) is not int or not 0 < uid < 2**52 or person.get("is_bot") or uid == self.router.tg._bot.id:
                    raise ValueError
                if any(c["id"] == str(uid) for c in choices):
                    continue
                choices.append({"id": str(uid), "type": "private", "name": " ".join(str(person.get(k) or "") for k in ("first_name", "last_name")).strip()[:160], "username": str(person.get("username") or "")[:64]})
            return choices
        chat_id = message["chat_shared"]["chat_id"]
        if type(chat_id) is not int or not -(2**52) < chat_id < 0:
            raise ValueError
        bot = self.router.tg._bot
        chat = await bot.get_chat(chat_id)
        if chat.type not in (("channel",) if row["kind"] == "channel" else ("group", "supergroup")):
            raise ValueError
        member = await bot.get_chat_member(chat_id, bot.id)
        owner = await bot.get_chat_member(chat_id, user)
        if owner.status not in ("creator", "administrator"):
            raise ValueError
        ready = member.status in ("creator", "administrator")
        if row["kind"] == "group" and not row["pin"] and member.status in ("member", "restricted"):
            permissions = member if member.status == "restricted" else getattr(chat, "permissions", None)
            ready = bool(getattr(permissions, "can_send_messages", False)) and (member.status != "restricted" or bool(getattr(member, "is_member", False)))
        if row["kind"] == "channel":
            ready = ready and bool(getattr(member, "can_post_messages", member.status == "creator"))
        if row["pin"]:
            ready = ready and bool(getattr(member, "can_edit_messages" if row["kind"] == "channel" else "can_pin_messages", member.status == "creator"))
        return [{"id": str(chat_id), "type": str(chat.type), "name": str(chat.title or "")[:160], "username": str(chat.username or "")[:64], "ready": ready,
                 "reason": "" if ready else "Надайте боту права надсилання та, якщо ввімкнено статус, закріплення. Потім повторіть вибір.", "checked_at": time.time()}]

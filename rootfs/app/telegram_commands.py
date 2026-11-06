"""Native Telegram menu and private command dispatch share existing workflows."""

import asyncio
import logging
import re
import time

from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, MenuButtonCommands
from config import delivery_profiles
from telegram_onboarding import profile_binding

logger = logging.getLogger(__name__)
LANGUAGES = ("", "en", "uk", "ru")
LABELS = {
    "start": ("🏠 Головне меню", "🏠 Main menu"),
    "connect": ("🔗 Підключитися", "🔗 Request access"),
    "today": ("📅 Графік на сьогодні", "📅 Today's schedule"),
    "tomorrow": ("🗓 Графік на завтра", "🗓 Tomorrow's schedule"),
    "status": ("⚡️ Стан", "⚡️ Status"),
    "readings": ("📝 Передати покази", "📝 Submit meter readings"),
    "history": ("📋 Покази в ДТЕК", "📋 DTEK reading history"),
    "documents": ("📄 Документи ДТЕК", "📄 DTEK documents"),
    "accounts": ("🔀 Обрати адресу / рахунок", "🔀 Choose address / account"),
    "cancel": ("✖️ Скасувати введення", "✖️ Cancel reading draft"),
    "help": ("❓ Допомога", "❓ Help"),
}


def automatic_status(profile):
    if profile.mode == "building" and profile.member_access:
        return False  # The group's pinned post never replaces a private reply.
    s = profile.status_message
    return s.enabled and s.pin and s.delivery_mode == "pinned_edit" and s.update_interval > 0


def profile_label(profile):
    return ("👤 Кабінет · " if profile.mode == "personal" else "🏢 Будинок · ") + (profile.display_name or "ДТЕК")


def commands_for(profiles, building_access=False):
    if not profiles:
        return ["start", "today", "tomorrow", "status", "accounts", "connect", "help"] if building_access else ["start", "connect", "help"]
    names = ["start", "today", "tomorrow"]
    if building_access or any(not automatic_status(p) for p in profiles):
        names.append("status")
    if any(p.mode == "personal" and p.allow_readings for p in profiles):
        names += ["readings", "history"]
    if any(p.mode == "personal" for p in profiles):
        names.append("documents")
    if len(profiles) > 1 or building_access:
        names.append("accounts")
    return names + ["cancel", "help"]


class NativeCommands:
    """Only the incoming-update owner constructs this synchronizer."""
    def __init__(self, bot, db):
        self.bot, self.db = bot, db
        self.synced = {}
        db.execute("CREATE TABLE IF NOT EXISTS command_scopes (chat_id INTEGER PRIMARY KEY)")
        db.commit()

    async def sync(self, profiles_by_user, *, building_access=False):
        old = {r[0] for r in self.db.execute("SELECT chat_id FROM command_scopes")}
        errors = []
        for user in old - profiles_by_user.keys():
            try:
                for lang in LANGUAGES:
                    await self.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user), language_code=lang)
                with self.db:
                    self.db.execute("DELETE FROM command_scopes WHERE chat_id=?", (user,))
                self.synced.pop(user, None)
            except Exception as exc:
                errors.append(exc)
        for user, profiles in {None: [], **profiles_by_user}.items():
            try:
                await self._set(user, commands_for(profiles, building_access))
            except Exception as exc:
                # A blocked/unavailable chat must not stop setup for everyone else.
                errors.append(exc)
        if errors:
            raise errors[0]

    async def _set(self, user, names):
        if self.synced.get(user) == names:
            return
        scope = BotCommandScopeAllPrivateChats() if user is None else BotCommandScopeChat(chat_id=user)
        if user is not None:
            # Remember before the first API call, including partial failures.
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO command_scopes VALUES(?)", (user,))
        for lang in LANGUAGES:
            await self.bot.set_my_commands(
                commands=[BotCommand(command=name, description=LABELS[name][0 if lang in ("uk", "ru") else 1]) for name in names],
                scope=scope, language_code=lang)
        await self.bot.set_chat_menu_button(chat_id=user, menu_button=MenuButtonCommands())
        self.synced[user] = names


class CommandNavigation:
    async def sync_commands(self):
        if not self.app.config.receive_commands or time.time() < getattr(self, "_commands_retry", 0):
            return
        self._commands_retry = time.time() + 300
        users = set()
        for p in delivery_profiles(self.app.config):
            if p.telegram_bot_id == self.bot_id:
                chat = self.app.bots.chat(p)
                if str(chat).isdigit() and int(chat) > 0:
                    users.add(int(chat))
        profiles = {user: self.profiles(user) for user in users if self.profiles(user)}
        try:
            if not hasattr(self, "_native_commands"):
                self._native_commands = NativeCommands(self.tg._bot, self.tg.journal.db)
            async with asyncio.timeout(20):
                await self._native_commands.sync(profiles, building_access=bool(self.members.candidates()))
        except Exception as exc:
            self._commands_retry = time.time() + max(300, getattr(exc, "retry_after", 0) or 0)
            logger.warning("Telegram command menu sync deferred: %s", type(exc).__name__)
        notified = set()
        for request in self.tg.journal.requests.pending():
            user = request["user_id"]
            target = request.get("profile_key", "")
            eligible = user in profiles
            if target:
                eligible = eligible and any(p.key == target and p.telegram_bot_id == self.bot_id and p.mode == "personal" and user in p.telegram_user_ids and profile_binding(p) == request["binding"] for p in self.app.config.addresses)
            if eligible and self.tg.journal.requests.resolve(user, "accepted", request["revision"], target) and user not in notified:
                notified.add(user)
                # Mark first: a lost Telegram response must not duplicate a welcome after restart.
                try:
                    async with asyncio.timeout(10):
                        await self.send(user, "✅ Доступ підключено. Відкрийте меню Telegram або натисніть /start.")
                except Exception as exc:
                    logger.warning("Connection acceptance reply unavailable: %s", type(exc).__name__)

    async def public_command(self, user, sender, text):
        command = await self.parse_command(text)
        if command is None:
            return False
        if self.members.candidates() and (command in {"today", "tomorrow", "status", "accounts"} or (command == "start" and (self.members.failed or not self.app.config.welcome_enabled))):
            await self.send(user, "ℹ️ Не вдалося перевірити участь у чаті. Спробуйте пізніше; власник має надати боту права адміністратора." if self.members.failed else "ℹ️ Меню будинку доступне учасникам налаштованої групи або каналу. Вступіть до свого чату й відкрийте /start. Для доступу до кабінету — /connect.")
            return True
        if command == "connect":
            if not self.tg.journal.claim_interaction(f"connect:{user}", 5, limit=100):
                return True
            result = self.tg.journal.requests.request(sender)
            text = {
                "pending": "🔗 Заявку на підключення передано власнику бота. Очікуйте підтвердження. Повторно надсилати її не потрібно.",
                "declined": "ℹ️ Власник відхилив заявку. Зверніться до нього; нову заявку можна надіслати через добу.",
                "accepted": "ℹ️ Попередню заявку було прийнято, але зараз доступу немає. Зверніться до власника бота.",
                "full": "ℹ️ Заявку зараз не вдалося створити. Зверніться до власника бота.",
            }[result]
            await self.send(user, text)
            return True
        if command == "help" and self.tg.journal.claim_interaction(f"help:{user}", 5, limit=100):
            await self.send(user, "❓ <b>Як підключитися</b>\nНатисніть /connect, щоб надіслати заявку власнику бота. Доступ з’явиться лише після його підтвердження. /start відкриває привітання.\n\nКоманди також є в кнопці «Меню» поруч із полем повідомлення.")
            return True
        return False

    async def parse_command(self, text):
        match = re.match(r"^/([a-zA-Z0-9_]+)(?:@([a-zA-Z0-9_]+))?(?:\s|$)", text)
        if not match:
            return None
        if match[2]:
            if not hasattr(self, "_username"):
                self._username = (await self.tg._bot.get_me()).username
            if match[2].casefold() != str(self._username).casefold():
                return None
        return match[1].lower()

    async def command(self, user, profiles, text):
        name = await self.parse_command(text)
        if name is None:
            return
        if name == "cancel":
            self.cancel_reading(user)
            await self.send(user, "✖️ Введення скасовано. Ця команда не передає показів і не скасовує вже надісланих у ДТЕК. Відкрити меню: /start.")
            return
        if name == "help":
            self.invalidate_reading_buttons(user)
            await self.send(user, "❓ <b>Команди бота</b>\n" + "\n".join(f"/{n} — {LABELS[n][0]}" for n in commands_for(profiles))
                            + ("\n\nПерегляд графіків не скасовує введені покази. Поверніться до чернетки через /start; /cancel скасовує її. Чернетка зберігається до 5 хвилин, до перезапуску бота. Передача в ДТЕК потребує окремого підтвердження." if any(p.mode == "personal" for p in profiles) else "\n\nМеню будинку: стан і графіки. Участь у групі або каналі перевіряється при кожному зверненні. Покази та документи особистого кабінету тут недоступні."))
            return
        if name == "connect":
            await self.send(user, "✅ Доступ уже налаштовано. Відкрити меню: /start. Для іншого рахунку зверніться до власника бота.")
            return
        if name in ("start", "accounts"):
            await self.menu(user, profiles)
            return
        targets = {"today": ("grid", 0), "tomorrow": ("grid", 1), "status": ("status", None),
                   "readings": ("readings", None), "history": ("history", None), "documents": ("documents", None)}
        if name not in targets:
            await self.send(user, "ℹ️ Невідома команда. Відкрийте /help або /start. Чернетку показів збережено.")
            return
        choices = [p for p in profiles if (name not in ("readings", "history", "documents") or p.mode == "personal")
                   and (name not in ("readings", "history") or p.allow_readings)]
        if not choices:
            await self.send(user, "ℹ️ Ця дія не налаштована для вашого профілю. Зверніться до власника бота.")
            return
        action, payload = targets[name]
        if len(choices) > 1:
            from aiogram.types import InlineKeyboardMarkup
            rows = [self.keyboard(user, p, [(profile_label(p), "command", (action, payload))]).inline_keyboard[0] for p in choices]
            await self.send(user, "🔀 Оберіть адресу або рахунок для цієї дії:", InlineKeyboardMarkup(inline_keyboard=rows))
            return
        await self.action(user, choices[0], action, payload)

    async def resume_reading(self, user):
        session = self.sessions.get(user)
        profile = next((p for p in self.profiles(user) if session and p.key == session["profile"] and p.allow_readings), None)
        if not profile or session["expires"] <= time.time():
            self.cancel_reading(user)
            await self.send(user, "ℹ️ Чернетка вже недоступна. Почніть знову через /readings.")
            return
        self.invalidate_reading_buttons(user)
        zones = {z["zone"] for z in session["context"]["zones"]}
        if zones and zones <= session.get("values", {}).keys():
            if session.get("photo_zone") or (session.get("photos") and set(session["photos"]) != zones):
                await self.photo_action(user, profile, "photos", None)
            else:
                await self.review(user, profile, session)
        else:
            session["input"] = True
            await self.prompt(user, profile, session)

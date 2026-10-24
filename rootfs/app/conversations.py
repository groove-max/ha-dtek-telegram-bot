"""Small, private-chat workflows. Account access comes only from HA configuration."""

from __future__ import annotations

import asyncio
from html import escape
import logging
import secrets
import time
from collections import OrderedDict
from types import SimpleNamespace

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import delivery_profiles
from ha_client import HACommandError
from utils import format_datetime
from reading_photos import ReadingPhotos, PHOTO_ACTIONS
from personal_documents import PersonalDocuments
logger = logging.getLogger(__name__)
ZONE_LABELS = {
    "GENERAL": "Загальні", "DAY": "День", "NIGHT": "Ніч",
    "DAY_2_ZONE": "День", "NIGHT_2_ZONE": "Ніч",
}


class Conversations(ReadingPhotos, PersonalDocuments):
    def __init__(self, orchestrator, profile_id, telegram):
        self.app, self.bot_id, self.tg = orchestrator, profile_id, telegram
        self.buttons = OrderedDict()
        self.sessions = {}
        self._lock = asyncio.Lock()
        self.config = SimpleNamespace(telegram_bot_id=profile_id)
        self.name = "personal_reminders"

    def profiles(self, user_id):
        return [
            a
            for a in delivery_profiles(self.app.config)
            if a.telegram_bot_id == self.bot_id
            and (
                (
                    a.mode == "personal"
                    and a.telegram_user_id == user_id
                    and a.telegram_chat_id == str(user_id)
                    and self.app.bots.chat(a) == str(user_id)
                )
                or (a.mode == "building" and self.app.bots.chat(a) == str(user_id))
            )
        ]

    async def send(self, chat, text, buttons=None, *, transient=True):
        # Commands have their own explicit reply destination; never inherit a house channel.
        if self.tg._test_chat_id and str(chat) != self.tg._test_chat_id:
            return None
        async def operation():
            return await self.tg._bot.send_message(
                chat_id=chat, text=text, parse_mode="HTML", reply_markup=buttons
            )
        if transient and self.profiles(int(chat)):
            return await self.tg.replace_menu(chat, operation)
        return await operation()

    def navigation(self, user, profile, actions=()):
        return self.keyboard(user, profile, [*actions, ("🏠 Меню", "menu", None)])

    async def reply(self, user, profile, title, text, actions=(), *, address=None, transient=True):
        heading = escape(address or profile.display_name or "Мій ДТЕК")
        return await self.send(
            user, f"⚡️ {heading}\n\n<b>{title}</b>\n{text}",
            self.navigation(user, profile, actions), transient=transient,
        )

    def keyboard(self, user, profile, actions):
        now = time.time()
        self.buttons = OrderedDict(
            (k, v) for k, v in self.buttons.items() if v["expires"] > now
        )
        rows = []
        for label, action, payload in actions:
            token = secrets.token_urlsafe(12)
            self.buttons[token] = {
                "user": user,
                "profile": profile.key,
                "action": action,
                "payload": payload,
                "expires": now
                + (300 if action in PHOTO_ACTIONS | {"confirm", "manual", "estimate", "review", "calibration_preview", "calibration_save"} else 604800),
            }
            rows.append(
                [InlineKeyboardButton(text=label, callback_data="dtek:" + token)]
            )
        while len(self.buttons) > 512:
            self.buttons.popitem(last=False)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def handle(self, update):
        payload = (
            update.model_dump(mode="json", exclude_none=True, by_alias=True)
            if hasattr(update, "model_dump")
            else update
        )
        callback = payload.get("callback_query")
        message = (
            (callback or {}).get("message") if callback else payload.get("message")
        )
        sender = (
            (callback or {}).get("from") if callback else (message or {}).get("from")
        )
        if (
            not message
            or not sender
            or sender.get("is_bot")
            or message.get("chat", {}).get("type") != "private"
        ):
            return
        user = sender.get("id")
        chat = message["chat"].get("id")
        if not isinstance(user, int) or user <= 0 or chat != user:
            return
        if self.tg._test_chat_id and str(chat) != self.tg._test_chat_id:
            return
        if not callback and (
            time.time() - message.get("date", 0) > 300
            or not (message.get("text") or message.get("photo") or message.get("document"))
        ):
            return
        async with self._lock:
            self.sessions = {
                k: v for k, v in self.sessions.items() if v["expires"] > time.time()
            }
            self.buttons = OrderedDict((k,v) for k,v in self.buttons.items() if v["expires"] > time.time())
            profiles = self.profiles(user)
            if not profiles:
                if callback:
                    await self.tg._bot.answer_callback_query(
                        callback["id"], text="Доступ не налаштовано."
                    )
                    return
                if (
                    self.app.config.welcome_enabled
                    and self.tg.journal.claim_interaction(
                        f"welcome:{user}", 86400, limit=20
                    )
                ):
                    await self.send(
                        chat,
                        self.app.templates.render("welcome", telegram_user_id=user),
                    )
                return
            if callback:
                await self.tg._bot.answer_callback_query(callback["id"])
                token = str(callback.get("data", "")).removeprefix("dtek:")
                button = self.buttons.get(token)
                if (
                    not button
                    or button["user"] != user
                    or button["expires"] < time.time()
                ):
                    await self.send(chat, "ℹ️ Ця кнопка вже неактивна. Відкрийте меню командою /start.")
                    return
                profile = next(
                    (a for a in profiles if a.key == button["profile"]), None
                )
                if not profile:
                    await self.send(chat, "ℹ️ Профіль більше недоступний.")
                    return
                # Navigation remains reusable; only workflow actions are consumed.
                if button["action"] not in {"menu", "profile", "status", "grid", "result", "history", "readback", "documents", "document"}:
                    self.buttons.pop(token, None)
                try:
                    await self.action(
                        user, profile, button["action"], button["payload"]
                    )
                except Exception as exc:
                    await self.failure(user, exc, profile, action=button["action"], payload=button["payload"])
                return
            if message.get("photo") or message.get("document"):
                session = self.sessions.get(user)
                profile = next((p for p in profiles if session and p.key == session["profile"]), None)
                if not profile or profile.mode != "personal" or not profile.allow_readings:
                    await self.menu(user, profiles)
                    return
                if not self.tg.journal.claim_interaction(f"photo:{user}:{message.get('message_id')}", 300):
                    return
                try:
                    await self.input_photo(user, profile, message)
                except Exception as exc:
                    await self.failure(user, exc, profile, action="photo")
                return
            if not self.tg.journal.claim_interaction(f"input:{user}", 1):
                return
            text = message["text"].strip()
            if text.startswith("/"):
                self.cancel_reading(user)
                await self.menu(user, profiles)
                return
            session = self.sessions.get(user)
            if session and session["expires"] > time.time():
                profile = next(
                    (a for a in profiles if a.key == session["profile"]), None
                )
                if profile and profile.allow_readings:
                    try:
                        await self.input_reading(user, profile, text, session)
                    except Exception as exc:
                        await self.failure(user, exc, profile, action="prepare")
                    return
            await self.menu(user, profiles)

    async def menu(self, user, profiles):
        if len(profiles) > 1:
            rows = []
            for profile in profiles:
                rows.extend(self.keyboard(user, profile, [(
                    "🏠 " + (profile.display_name or "Рахунок ДТЕК"), "profile", None
                )]).inline_keyboard)
            await self.send(user, "⚡️ <b>Мій ДТЕК</b>\n\nОберіть рахунок:", InlineKeyboardMarkup(inline_keyboard=rows))
            return
        for profile in profiles:
            actions = [
                ("📅 Графік на сьогодні", "grid", 0),
                ("🗓 Графік на завтра", "grid", 1),
            ]
            status = profile.status_message
            # Only a pinned, periodically refreshed post replaces manual access.
            # With interval=0, event-driven updates may still occur, but users
            # must be able to request a fresh status at any time.
            automatic_pinned_status = (
                status.enabled
                and status.pin
                and status.delivery_mode == "pinned_edit"
                and status.update_interval > 0
            )
            if not automatic_pinned_status:
                actions.insert(0, ("⚡️ Стан", "status", None))
            if profile.mode == "personal" and profile.allow_readings:
                actions += [
                    ("📝 Передати покази", "readings", None),
                    ("📋 Покази в ДТЕК", "history", None),
                ]
            if profile.mode == "personal":
                actions.append(("📄 Документи ДТЕК", "documents", None))
            if len(self.profiles(user)) > 1:
                actions.append(("🔀 Обрати рахунок", "menu", None))
            await self.send(
                user,
                "⚡️ <b>Мій ДТЕК</b>\n📍 " + escape(
                    profile.display_name
                    or (
                        "Особистий профіль ДТЕК"
                        if profile.mode == "personal"
                        else profile.entity_prefix
                    )
                ) + "\n\nОберіть потрібну дію:",
                self.keyboard(user, profile, actions),
            )

    async def cabinet(self, profile, user, action, **payload):
        # Recheck before every action: an old button cannot authorize a removed profile.
        if profile not in self.profiles(user) or profile.mode != "personal":
            raise HACommandError("unauthorized")
        if (
            action in ("context", "energy", "prepare", "confirm", "readback", "history", "calibration_preview", "calibration_save")
            and not profile.allow_readings
        ):
            raise HACommandError("unauthorized")
        return await self.app.ha.command(
            "dtek_monitor/bot_account",
            entity_id=profile.account_entity,
            action=action,
            telegram_user_id=user,
            telegram_bot_id=str(self.tg._bot.id),
            profile_id=profile.key,
            **payload,
        )

    def cancel_reading(self, user):
        self.sessions.pop(user, None)
        self.invalidate_reading_buttons(user)

    def invalidate_reading_buttons(self, user):
        self.buttons = OrderedDict(
            (k, v)
            for k, v in self.buttons.items()
            if not (
                v["user"] == user and v["action"] in PHOTO_ACTIONS | {
                    "confirm", "manual", "estimate", "review",
                    "calibration_preview", "calibration_save",
                }
            )
        )

    async def action(self, user, profile, action, payload):
        if action in PHOTO_ACTIONS:
            await self.photo_action(user, profile, action, payload)
            return
        if action in ("documents", "document"):
            self.cancel_reading(user)
            await self.document_action(user, profile, action, payload)
            return
        if action in ("menu", "profile", "readings", "manual", "estimate", "review"):
            # Editing, starting over or switching accounts invalidates old confirmations.
            self.cancel_reading(user)
        if action == "menu":
            await self.menu(user, self.profiles(user))
            return
        if action == "profile":
            await self.menu(user, [profile])
            return
        if action == "status":
            # Match the automatic status, including saved template overrides,
            # fresh voltage, cabinet groups and unverified schedule handling.
            text = await self.app.render_status(profile)
            await self.send(user, escape(text), self.navigation(user, profile))
            return
        if action == "grid":
            await self.grid(user, profile, payload)
            return
        if action in ("result", "history"):
            history = await self.cabinet(profile, user, "history", **(payload or {}))
            record = history.get("attempt") or {}
            status = {
                "accepted": "✅ ДТЕК підтвердив прийняття показів.",
                "rejected": "⛔ ДТЕК відхилив покази.",
                "unknown": "❔ Результат невідомий. Перевірте кабінет у HA; повторно не надсилаємо.",
                "pending": "⏳ Передача ще не має підтвердженого результату.",
                "partial": "⚠️ ДТЕК підтвердив не всі зони. Перевірте кабінет у HA.",
            }.get(record.get("status"), "")
            text = self.history_text(history)
            if status:
                text += "\n\n📨 <b>Остання передача через HA</b>\n" + status
                if record.get("at"):
                    text += "\n🕒 " + escape(format_datetime(record["at"]))
            actions = self.history_controls(history)
            if record.get("readings"):
                actions.append(("🔎 Перевірити останню передачу", "readback", None))
            await self.reply(
                user, profile, "📋 Покази в ДТЕК", text, actions,
            )
            return
        if action == "readback":
            result = await self.cabinet(profile, user, "readback")
            text = {
                "matched": "У кабінеті є відповідні покази всіх зон на дату передачі. Це не підтвердження остаточного місячного розрахунку.",
                "partial": "Знайдено покази лише частини зон. Перевірте деталі в HA.",
                "not_found": "Відповідні покази поки не знайдено. Це не означає відмову: ДТЕК може опублікувати їх пізніше або з іншою датою.",
                "ambiguous": "Є кілька відповідних записів. Перевірте історію в HA.",
                "meter_changed": "Дані лічильника змінилися. Перевірте їх у HA.",
            }.get(result.get("status"), "")
            text = self.history_text(result) + ("\n\n🔎 " + text if text else "")
            await self.reply(user, profile, "🔎 Перевірка в кабінеті", text,
                             self.history_controls(result))
            return
        if action == "readings":
            self.sessions.pop(user, None)
            context = await self.cabinet(profile, user, "context")
            if context.get("blocked"):
                await self.reply(
                    user, profile, "⚠️ Передача недоступна",
                    "Передача зараз недоступна. Перевірте попередню спробу та стан лічильника в HA.",
                )
                return
            session = {
                "profile": profile.key,
                "context": context,
                "values": {},
                "expires": time.time() + 300,
            }
            self.sessions[user] = session
            actions = [("✏️ Ввести вручну", "manual", session)]
            energy = {}
            try:
                energy = await self.cabinet(profile, user, "energy")
                session["energy"] = energy
                if energy.get("ready") and energy.get("proposal"):
                    actions.insert(0,
                        (
                            "🔢 Використати розрахунок датчика",
                            "estimate",
                            {"session": session, "energy": energy},
                        )
                    )
            except HACommandError as error:
                energy = {"error": error.code}
            instruction = (
                "Оберіть розрахунок або введіть накопичувальні покази вручну."
                if energy.get("ready") and energy.get("proposal") else
                "Натисніть «Ввести вручну», щоб вказати накопичувальні покази лічильника, кВт·год."
            )
            await self.reply(
                user, profile, "📝 Передача показів",
                f"🧾 Рахунок: {escape(context.get('account', ''))}\n\n"
                + self.energy_text(energy) + "\n\n" + instruction,
                actions, address=context.get("address"),
            )
            return

        if action == "manual":
            self.sessions[user] = {
                **payload,
                "values": {},
                "expires": time.time() + 300,
                "input": True,
            }
            self.sessions[user].pop("source_token", None)
            self.sessions[user].pop("photos", None)
            await self.prompt(user, profile, self.sessions[user])
            return
        if action == "estimate":
            session = payload["session"]
            energy = payload["energy"]
            session["values"] = {
                k: v["send_value"] for k, v in energy["readings"].items()
            }
            session["source_token"] = energy["proposal"]
            self.sessions[user] = session
            await self.review(user, profile, session)
            return
        if action == "review":
            await self.review(user, profile, payload)
            return
        if action == "calibration_preview":
            session = payload
            self.buttons = OrderedDict(
                (k, v) for k, v in self.buttons.items()
                if not (v["user"] == user and v["profile"] == profile.key)
            )
            draft = await self.cabinet(
                profile, user, "calibration_preview",
                revision=session["context"]["revision"], values=dict(session["values"]),
                binding_revision=session["energy"]["binding"]["revision"],
            )
            lines = ["Це локальне коригування. Воно не передає показів у ДТЕК.\n"]
            for zone, row in draft["change"]["changes"].items():
                lines.append(
                    f"🔢 {escape(ZONE_LABELS.get(zone, zone))}: <b>{escape(str(row['actual']))} кВт·год</b>\n"
                    f"Датчик: {escape(str(row['source']))} кВт·год\n"
                    f"🕒 {escape(format_datetime(row['source_at']))}\n"
                    f"Поправка: {escape(str(row['correction']))} кВт·год\n"
                )
            lines.append("Зберігайте лише якщо покази кожного тарифу фізичного лічильника зняті одночасно з наведеним відліком датчика. Давніші покази не підходять.")
            await self.reply(user, profile, "📏 Перевірте калібрування", "\n".join(lines), [
                ("✅ Знято одночасно — зберегти", "calibration_save", {"session": session, "confirmation": draft["confirmation"]}),
                ("↩️ Назад без збереження", "review", session),
            ], address=session["context"].get("address"))
            return
        if action == "calibration_save":
            session = payload["session"]
            self.buttons = OrderedDict(
                (k, v) for k, v in self.buttons.items()
                if not (v["user"] == user and v["profile"] == profile.key)
            )
            await self.cabinet(profile, user, "calibration_save",
                               confirmation=payload["confirmation"], contemporaneous=True)
            session.pop("source_token", None)
            session.pop("energy", None)
            try:
                session["energy"] = await self.cabinet(profile, user, "energy")
            except HACommandError:
                pass
            await self.reply(user, profile, "✅ Калібрування збережено",
                             "Наступні розрахунки врахують поправку. Покази у ДТЕК ще не передані.",
                             [("📝 Перевірити передачу показів", "review", session)])
            return
        if action == "confirm":
            self.sessions.pop(user, None)
            # Remove every outstanding action for this account before attempting the write.
            self.buttons = OrderedDict(
                (k, v)
                for k, v in self.buttons.items()
                if not (v["user"] == user and v["profile"] == profile.key)
            )
            result = await self.cabinet(profile, user, "confirm", confirmation=payload)
            text = {
                "accepted": "✅ ДТЕК підтвердив прийняття показів. Остаточний місячний розрахунок може з’явитися пізніше.",
                "rejected": "⛔ ДТЕК відхилив покази. Перевірте їх у HA.",
                "partial": "⚠️ Підтверджено не всі зони. Перевірте результат у HA; повторно не надсилаємо.",
            }.get(
                result.get("status"),
                "❔ Результат передачі невідомий. Перевірте кабінет у HA; повторно не надсилаємо.",
            )
            if result.get("photos"):
                text += "\n📷 Запит містив фото. Прийняття показів не підтверджує видачу акта."
            if result.get("reason") == "photo_required":
                text += "\n📷 ДТЕК просить фото для перевірки показів. Перевірте результат у кабінеті перед новою спробою."
            await self.reply(user, profile, "📨 Передача показів", text)
            return
        if action == "dismiss_reminder":
            self.app.state.set(profile.key, "reading_reminder_done", payload)
            await self.app.state.save()
            await self.reply(
                user, profile, "✅ Нагадування закрито",
                "Нагадування на цей період закрито. Ця дія не передає покази."
            )
            return
        if action == "snooze":
            self.app.state.set(
                profile.key, "reading_reminder_snooze", time.time() + 86400
            )
            await self.app.state.save()
            await self.reply(user, profile, "⏰ Нагадування відкладено", "Нагадаю завтра, якщо період передачі ще триватиме.")
            return

    @staticmethod
    def history_controls(result):
        view = result.get("history_view")
        if not view:
            return [("🔄 Оновити з ДТЕК", "history", None)]
        mode = bool(view["include_inactive"])
        actions = [("🔄 Оновити з ДТЕК", "history",
                    {"include_inactive": mode, "history_offset": view["offset"]})]
        if mode or view["inactive_count"]:
            actions.append(("📋 Приховати неактивні записи" if mode else "🗂 Показати всі записи ДТЕК",
                            "history", {"include_inactive": not mode, "history_offset": 0}))
        for label, key in (("⬅️ Новіші записи", "previous_offset"), ("➡️ Старіші записи", "next_offset")):
            if view.get(key) is not None:
                actions.append((label, "history", {"include_inactive": mode, "history_offset": view[key]}))
        return actions

    @staticmethod
    def history_text(result):
        """Show official records even when this HA has never submitted anything."""
        view = result.get("history_view") or {}
        rows = [r for r in result.get("latest", []) if r.get("energy") == "ACTIVE"
                and (view.get("include_inactive") or r.get("status") != "inactive")]
        lines = ["Останні накопичувальні покази, опубліковані в кабінеті ДТЕК:"]
        if result.get("years"):
            lines.append(f"📅 За {escape(str(result['years'][-1]))} рік.")
        labels = {"blocked": "заблокований запис", "inactive": "неактивний запис",
                  "unknown": "статус запису не вказаний"}
        for row in rows[:6]:
            zone = escape(ZONE_LABELS.get(row["zone"], row["zone"]))
            lines.append(f"\n📅 {escape(format_datetime(row['date_time']))}\n"
                         f"🔢 {zone}: <b>{escape(str(row['value']))} кВт·год</b>")
            if row.get("status") != "active":
                lines.append(("ℹ️ " if row.get("status") == "inactive" else "⚠️ ")
                             + labels.get(row.get("status"), labels["unknown"]))
        if not rows:
            lines.append("\nℹ️ Є лише неактивні записи. Натисніть «Показати всі записи ДТЕК», щоб переглянути їх."
                         if view.get("total") else
                         "\nℹ️ За перевірений період ДТЕК не повернув записів показів активної енергії.")
        if view.get("inactive_count") and not view.get("include_inactive"):
            lines.append(f"\n🗂 Приховано неактивних записів: {view['inactive_count']}.")
        if rows and view:
            lines.append(f"\n📄 Записи {view['offset'] + 1}–{view['offset'] + len(rows)} з {view['visible']}.")
        if result.get("fetched_at"):
            lines.append("\n🕒 Перевірено: " + escape(format_datetime(result["fetched_at"])))
        lines.append("\nℹ️ Це покази лічильника, а не споживання за місяць. Наявність запису не означає остаточного місячного розрахунку.")
        return "\n".join(lines)

    @staticmethod
    def energy_text(energy):
        """Use the integration's meter reference; never create a second calibration."""
        if energy.get("ready") and energy.get("proposal"):
            mode = (energy.get("binding") or {}).get("mode")
            lines = ["🔢 <b>Розрахунок за датчиком з Home Assistant</b>" if mode != "direct"
                     else "🔢 <b>Покази основного лічильника з Home Assistant</b>"]
            for zone, row in energy["readings"].items():
                lines.append(f"{escape(ZONE_LABELS.get(zone, zone))}: <b>{escape(row['send_value'])} кВт·год</b>")
                if row.get("source", {}).get("at"):
                    lines.append("🕒 Дані датчика: " + escape(format_datetime(row["source"]["at"])))
            if mode != "direct":
                lines.append("Враховано збережену в інтеграції прив’язку до основного лічильника та коригування.")
            lines.append("Це накопичувальні покази. Передача відбудеться лише після вашого підтвердження.")
            return "\n".join(lines)
        reasons = {
            "source_stale": "Дані накопичувального датчика застаріли.",
            "source_unavailable": "Накопичувальний датчик недоступний.",
            "source_reset": "Зафіксовано скидання накопичувального датчика; перевірте калібрування в HA.",
            "source_changed": "Накопичувальний датчик змінився; перевірте прив’язку в HA.",
            "source_jump": "Зафіксовано незвичний стрибок показів; перевірте калібрування в HA.",
            "unauthorized": "Немає доступу до накопичувального датчика в HA.",
        }
        errors = [energy.get("error"), *[r.get("error") for r in energy.get("readings", {}).values()]]
        details = list(dict.fromkeys(reasons.get(e, "Розрахунок недоступний. Перевірте джерело показів у HA.") for e in errors if e))
        if details:
            return "⚠️ " + "\n".join(details)
        if not energy.get("binding"):
            return "ℹ️ Для цього рахунку не налаштовано накопичувальний датчик. Його можна прив’язати та відкалібрувати на панелі «Мій ДТЕК» у HA."
        return "⚠️ Розрахунок зараз недоступний. Перевірте джерело показів у HA."

    async def prompt(self, user, profile, session):
        zones = session["context"]["zones"]
        zone = next(z for z in zones if z["zone"] not in session["values"])
        await self.reply(
            user, profile, "✏️ Введення показів",
            f"🔢 {escape(ZONE_LABELS.get(zone['zone'], zone['zone']))}: попередні покази <b>{escape(zone['previous'])} кВт·год</b>.\n\nВведіть нове ціле число. /cancel — скасувати.",
        )

    async def input_reading(self, user, profile, text, session):
        if not session.get("input"):
            await self.reply(user, profile, "ℹ️ Оберіть дію", "Скористайтеся кнопкою або відкрийте /start.")
            return
        if not text.isascii() or not text.isdecimal() or len(text) > 8:
            await self.reply(user, profile, "✏️ Перевірте число", "Введіть ціле число кВт·год без пробілів.")
            return
        zone = next(
            z for z in session["context"]["zones"] if z["zone"] not in session["values"]
        )
        session["values"][zone["zone"]] = text
        if len(session["values"]) < len(session["context"]["zones"]):
            await self.prompt(user, profile, session)
        else:
            session["input"] = False
            await self.review(user, profile, session)

    async def review(self, user, profile, session):
        if session.get("expires", 0) <= time.time():
            raise HACommandError("confirmation_expired")
        payload = {
            "revision": session["context"]["revision"],
            "values": session["values"],
        }
        if session.get("source_token"):
            payload["source_token"] = session["source_token"]
        if session.get("photos"):
            payload["photos"] = session["photos"]
        self.invalidate_reading_buttons(user)
        self.sessions[user] = session
        session.pop("photo_zone", None)
        draft = await self.cabinet(profile, user, "prepare", **payload)
        lines = [
            f"🧾 Рахунок: {escape(session['context'].get('account', ''))}\n",
            "Перевірте накопичувальні покази:\n",
        ]
        for row in draft["readings"]:
            lines.append(
                f"🔢 {escape(ZONE_LABELS.get(row['zone'], row['zone']))}: {row['previous']} → <b>{row['value']} кВт·год</b>\nРізниця між показами: {row['increase']} кВт·год\n"
            )
        lines.append(
            "ℹ️ Це не обов’язково споживання за календарний місяць.\n\nПередати ці покази у ДТЕК?"
        )
        actions = [
            ("✅ Підтвердити й передати у ДТЕК", "confirm", draft["confirmation"]),
            ("✏️ Виправити вручну", "manual", session),
        ]
        if session["context"].get("photo_submission"):
            actions.append(("📷 Змінити фото" if session.get("photos") else "📷 Додати фото", "photos", None))
        if session.get("photos"):
            lines.append("\n📷 Додано фото: " + ", ".join(escape(ZONE_LABELS.get(z,z)) for z in session["photos"]))
        if not session.get("source_token") and (session.get("energy", {}).get("binding") or {}).get("mode") == "offset":
            actions.append(("📏 Зберегти поправку калібрування", "calibration_preview", session))
        await self.reply(
            user, profile, "📝 Підтвердження показів",
            "\n".join(lines),
            actions, address=session["context"].get("address"),
        )

    async def failure(self, user, error, profile=None, *, action=None, payload=None):
        code = getattr(error, "code", "unavailable")
        text = {
            "not_available": "Рахунок ДТЕК зараз недоступний у Home Assistant. Перевірте стан інтеграції «DTEK Monitor» у HA: дані ще можуть завантажуватися або сервіс ДТЕК може не відповідати.",
            "unauthorized": "Доступ до цієї дії не налаштовано.",
            "invalid_readings": "Покази некоректні: перевірте всі зони та попереднє значення. Почніть з /start.",
            "confirmation_expired": "Підтвердження прострочене. Відкрийте /start та перевірте покази знову.",
            "context_changed": "Дані лічильника змінилися. Почніть з /start.",
            "energy_changed": "Прив’язка або відлік датчика змінилися. Почніть передачу знову, щоб отримати актуальні дані.",
            "source_stale": "Для коригування потрібні свіжі відліки датчиків (до 3 хвилин). Оновіть їх і зніміть покази фізичного лічильника одночасно.",
            "energy_confirmation_expired": "Підтвердження калібрування прострочене або вже використане. Перевірте збережену опорну пару в HA.",
            "calibration_time_required": "Підтвердьте одночасне зняття показів фізичного лічильника й датчиків.",
            "auth_required": "Потрібно повторно увійти до ДТЕК у Home Assistant.",
            "api_changed": "Цей сервіс ДТЕК недоступний або несумісний з інтеграцією. Відкрийте Налаштування → Система → Виправлення у HA. Перевірте оновлення: https://github.com/groove-max/ha-dtek-monitor/releases/latest\nЯкщо вже встановлено останню версію, повідомте про проблему: https://github.com/groove-max/ha-dtek-monitor/issues",
            "check_cabinet": "Попередня передача має невідомий результат. Перевірте кабінет у HA.",
            "rate_limited": "ДТЕК обмежив частоту запитів. Спробуйте пізніше.",
            "invalid_photo": "Не вдалося прочитати фото. Надішліть JPEG, PNG або WebP.",
            "photo_too_large": "Фото завелике. Надішліть менший знімок із чіткими показами (до 20 МБ, до 30 мегапікселів).",
            "photo_album": "Надішліть одне фото окремим повідомленням, без альбому.",
            "photos_incomplete": "Додайте фото кожної тарифної зони або приберіть усі фото.",
            "photos_unsupported": "Оновіть інтеграцію DTEK Monitor у HA, щоб передавати покази з фото.",
            "document_format": "Цей формат документа поки не підтримується. Відкрийте документ в офіційному кабінеті ДТЕК.",
            "document_too_large": "Документ перевищує 2 МБ. Завантажте його в офіційному кабінеті ДТЕК.",
            "document_missing": "Документ більше недоступний у списку ДТЕК. Оновіть список.",
        }.get(
            code,
            "Не вдалося отримати відповідь від Home Assistant або сервісу ДТЕК. Спробуйте пізніше.",
        )
        title = "⚠️ Дію не завершено"
        actions = []
        if action in ("documents", "document"):
            title = "⚠️ Документи зараз недоступні"
            actions = [("📄 Оновити документи", "documents", None)]
        elif action in ("history", "result", "readback"):
            title = ("⚠️ Не вдалося перевірити покази" if action == "readback"
                     else "⚠️ Історія показів недоступна")
            actions = [("🔄 Спробувати ще раз", "readback" if action == "readback" else "history",
                        payload if action in ("history", "result") else None)]
        elif action == "photo":
            text += "\n\nПокази ще не передано. Можна надіслати інше фото або повернутися до вибору тарифу."
            actions = [("📷 До фото", "photos", None)]
        elif action in ("readings", "manual", "estimate", "prepare"):
            title = "⚠️ Не вдалося підготувати передачу"
            text += "\n\nℹ️ На цьому кроці покази не надсилаються у ДТЕК."
            actions = [("📝 Почати знову", "readings", None)]
        elif action == "confirm":
            # A lost response can follow a successful write. Never offer a retry
            # of the confirmation or imply that nothing was submitted.
            title = "⚠️ Результат передачі не підтверджено"
            text += "\n\nПеревірте результат у HA або кабінеті ДТЕК перед новою передачею. Автоматично покази повторно не надсилаємо."
            actions = [("🔎 Перевірити в кабінеті", "readback", None)]
        if profile:
            await self.reply(user, profile, title, text, actions)
        else:
            await self.send(user, "⚠️ " + text)

    async def grid(self, user, profile, offset):
        from schedule_image import render_grid
        from schedule_pages import select_page, render_page, SCOPES
        from aiogram.types import BufferedInputFile

        request = offset if isinstance(offset, dict) else {"offset": int(offset)}
        navigating = "date" in request
        offset = request.get("offset", 0)
        scope, page = request.get("scope", "auto"), request.get("page", 0)
        try:
            if profile.mode == "personal":
                data = await self.cabinet(profile, user, "grid" if navigating else "refresh")
            else:
                entity = f"sensor.{profile.entity_prefix}_schedule_status"
                if not navigating:
                    await self.app.ha.command(
                        "dtek_monitor/schedule_refresh", entity_id=entity
                    )
                data = await self.app.ha.command(
                    "dtek_monitor/schedule_grid", entity_id=entity
                )
        except HACommandError as exc:
            if exc.code != "schedule_refresh_cooldown":
                await self.reply(
                    user, profile, "📅 Графік тимчасово недоступний",
                    "Не вдалося перевірити актуальний графік. Спробуйте пізніше або відкрийте HA.",
                )
                return
            data = (
                await self.cabinet(profile, user, "grid")
                if profile.mode == "personal"
                else await self.app.ha.command(
                    "dtek_monitor/schedule_grid", entity_id=entity
                )
            )
        if navigating:
            offset = next((i for i, day in enumerate(data.get("days", [])[:2]) if day["date"] == request["date"]), None)
            if offset is None:
                await self.reply(user, profile, "📅 Дата вже минула", "Оберіть актуальний графік у меню.")
                return
        actions = []
        try:
            view = select_page(data, offset, profile.mode == "personal", scope, page)
            def target(selected_scope, selected_page=0, full=False):
                return {"offset": offset, "date": view.day["date"], "scope": selected_scope, "page": selected_page, "full": full}
            for name in ("mine", "home", "all"):
                if name != view.scope and (name != "mine" or profile.mode == "personal"):
                    actions.append((SCOPES[name], "grid", target(name)))
            if view.page > 0:
                actions.append((f"◀️ {view.page} із {view.pages}", "grid", target(view.scope, view.page - 1)))
            if view.page + 1 < view.pages:
                actions.append((f"{view.page + 2} із {view.pages} ▶️", "grid", target(view.scope, view.page + 1)))
            actions.append(("📎 Повна таблиця PNG", "grid", target("all", full=True)))
            if request.get("full"):
                png = await asyncio.to_thread(render_grid, data, offset, profile.mode == "personal")
            else:
                png = await asyncio.to_thread(render_page, view)
        except ValueError:
            await self.reply(
                user, profile, "📅 Графік ще недоступний",
                "Для обраної дати або групи немає підтверджених даних. Це не означає, що відключень немає.", actions,
            )
            return
        date = view.day["date"]
        caption = f"📅 Графік на {date}\n{SCOPES[view.scope]} · {view.page + 1} із {view.pages}\n"
        if request.get("full"):
            caption = f"📅 Графік на {date}\nПовна таблиця · Усі групи\n"
        caption += escape(str(data.get("address", ""))[:300])
        if view.unassigned:
            caption += "\nℹ️ Вашу групу ще не підтверджено."
        caption += "\n\n⚠️ При аварійних відключеннях графік може не діяти."
        async def send():
            args = dict(chat_id=user, caption=caption, parse_mode="HTML", reply_markup=self.navigation(user, profile, actions))
            file = BufferedInputFile(png, filename=f"dtek-schedule-{date}.png")
            if request.get("full"):
                return await self.tg._bot.send_document(document=file, **args)
            return await self.tg._bot.send_photo(photo=file, **args)
        await self.tg.replace_menu(user, send)

    async def on_tick(self):
        async with self._lock:
            for user in list(self.sessions):
                if self.sessions[user]["expires"] <= time.time():
                    self.cancel_reading(user)
            for profile in delivery_profiles(self.app.config):
                if (
                    profile.telegram_bot_id != self.bot_id
                    or profile.mode != "personal"
                ):
                    continue
                user = profile.telegram_user_id
                if profile not in self.profiles(user):
                    continue
                try:
                    if profile.reading_reminders:
                        await self.remind(profile, user)
                except Exception as exc:
                    logger.warning(
                        "Personal reminder unavailable: %s", type(exc).__name__
                    )
                try:
                    if profile.document_notifications:
                        await self.check_documents(profile, user)
                except Exception as exc:
                    logger.warning("Personal documents unavailable: %s", type(exc).__name__)

    async def remind(self, profile, user):
        reminder = await self.cabinet(profile, user, "reminder")
        period = reminder.get("period")
        if not reminder.get("due") or not period:
            return
        state = self.app.state.get_all(profile.key)
        if state.get("reading_reminder_done") == period:
            return
        snooze = state.get("reading_reminder_snooze", 0)
        if snooze > time.time() or (
            state.get("reading_reminder_sent") == period and not snooze
        ):
            return
        actions = [
            ("✅ Вже передав", "dismiss_reminder", period),
            ("⏰ Нагадати завтра", "snooze", None),
        ]
        if profile.allow_readings:
            actions.insert(0, ("📝 Передати покази", "readings", None))
        # A crash or ambiguous timeout may have delivered the reminder; avoid a minute-by-minute retry.
        self.app.state.set(profile.key, "reading_reminder_snooze", time.time() + 86400)
        await self.app.state.save()
        await self.reply(
            user, profile, "⏰ Час передати покази",
            "Перевірте та передайте покази лічильника у ДТЕК.\n\nЯкщо вже передали їх іншим способом, закрийте нагадування.",
            actions, transient=False,
        )
        self.app.state.set(profile.key, "reading_reminder_sent", period)
        self.app.state.set(profile.key, "reading_reminder_snooze", 0)
        await self.app.state.save()

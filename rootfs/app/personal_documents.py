"""Private DTEK documents, with persistent deduplication for automatic delivery."""
import base64
import hashlib
from html import escape
import time
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from ha_client import HACommandError


class PersonalDocuments:
    async def document_action(self, user, profile, action, payload):
        if action == "documents":
            result = await self.cabinet(profile, user, "documents")
            rows = result["documents"]
            offset = payload if type(payload) is int else 0
            offset = max(0, min(offset, max(0, (len(rows)-1)//8*8)))
            selected = rows[offset:offset+8]
            actions = [("📄 " + row["name"][:52], "document", row["id"]) for row in selected]
            if offset:
                actions.append(("⬅️ Попередні", "documents", offset-8))
            if offset+8 < len(rows):
                actions.append(("➡️ Наступні", "documents", offset+8))
            actions.append(("🔄 Оновити документи", "documents", offset))
            await self.reply(user, profile, "📄 Документи ДТЕК",
                ("Оберіть документ, щоб отримати PDF. Назви та файли надає ДТЕК.\n\n"
                 + "\n".join(escape(r["name"]) for r in selected)) if rows else
                "ДТЕК поки не повернув документів для цього рахунку. Це не підтверджує завершення перевірки контролера.", actions)
            return
        document = await self.cabinet(profile, user, "document", document_id=payload)
        await self.tg.replace_menu(user, lambda: self.tg._bot.send_document(
            chat_id=user, document=self.document_file(document),
            caption="📄 " + document["name"][:250],
            parse_mode=None,
            reply_markup=self.navigation(user, profile, [("⬅️ Документи", "documents", None)])))

    @staticmethod
    def document_file(document):
        if document.get("mime_type") != "application/pdf" or len(document.get("data", "")) > 3 * 1024 * 1024:
            raise HACommandError("document_format")
        raw = base64.b64decode(document["data"], validate=True)
        if len(raw) > 2 * 1024 * 1024 or not raw.startswith(b"%PDF-"):
            raise HACommandError("document_format")
        return BufferedInputFile(raw, filename=document["name"])

    async def check_documents(self, profile, user):
        checks = getattr(self, "_document_checks", {})
        self._document_checks = checks
        if time.monotonic() - checks.get(profile.key, -float("inf")) < 1800:
            return
        checks[profile.key] = time.monotonic()
        result = await self.cabinet(profile, user, "documents")
        rows = result["documents"]
        prior = self.app.state.get(profile.key, "documents_seen")
        if not isinstance(prior, dict) or prior.get("account") != profile.account_entity:
            self.app.state.set(profile.key, "documents_seen", {"account":profile.account_entity, "ids":[r["id"] for r in rows]})
            await self.app.state.save()
            return
        seen = list(prior["ids"])
        for row in rows:
            if row["id"] in seen:
                continue
            key = hashlib.sha256(f"document:{self.bot_id}:{user}:{profile.key}:{profile.account_entity}:{row['id']}".encode()).hexdigest()
            delivery = self.tg.journal.delivery(key)
            if delivery and delivery[0] in ("ok", "unknown", "pending", "rejected"):
                seen.append(row["id"])
                continue
            try:
                document = await self.cabinet(profile, user, "document", document_id=row["id"])
                file = self.document_file(document)
            except HACommandError as error:
                if error.code not in ("document_format", "document_too_large"):
                    raise
                file = None
            # Configuration can change during the fetch. Never inherit another chat.
            if profile not in self.profiles(user) or not profile.document_notifications:
                return
            self.tg.journal.prepare_delivery(key, key, {"document_id":row["id"]}, time.time()+365*86400)
            caption = "📄 Новий документ ДТЕК\n📍 " + (profile.display_name or "Мій ДТЕК")[:250] + "\n" + row["name"][:250]
            async def operation():
                if file is not None:
                    return await self.tg._bot.send_document(chat_id=user, document=file, caption=caption, parse_mode=None)
                return await self.tg._bot.send_message(chat_id=user, parse_mode=None,
                    text=caption + "\n\nФайл завеликий або має непідтримуваний формат. Завантажте його в офіційному кабінеті ДТЕК.")
            try:
                sent = await self.tg._recorded(operation,
                    action="send", chat=str(user), context={"feature":"documents", "document_id":row["id"]})
            except TelegramRetryAfter:
                self.tg.journal.finish_delivery(key, "retry")
                continue
            except Exception as error:
                self.tg.journal.finish_delivery(key, "rejected" if isinstance(error, TelegramBadRequest) else "unknown")
            else:
                self.tg.journal.finish_delivery(key, "ok", sent.message_id)
            seen.append(row["id"])
        self.app.state.set(profile.key, "documents_seen", {"account":profile.account_entity, "ids":list(dict.fromkeys(seen))[-2000:]})
        await self.app.state.save()

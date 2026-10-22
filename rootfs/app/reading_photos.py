"""Private, bounded photo drafts; never submit or persist image bytes here."""

import asyncio
import base64
from html import escape
from io import BytesIO
import time
import warnings

from aiogram.types import BufferedInputFile
from PIL import Image, ImageOps, UnidentifiedImageError
from ha_client import HACommandError

MAX_INPUT = 20 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
PHOTO_ACTIONS = {"photos", "photo_zone", "photo_remove", "photo_review"}


class BoundedBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_INPUT:
            raise HACommandError("photo_too_large")
        return super().write(data)


def prepare_photo(raw):
    if len(raw) > MAX_INPUT:
        raise HACommandError("photo_too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in ("JPEG", "PNG", "WEBP") or getattr(source, "n_frames", 1) != 1:
                    raise ValueError
                if source.width * source.height > 30_000_000:
                    raise HACommandError("photo_too_large")
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image.thumbnail((2560, 2560))
                rgb = Image.new("RGB", image.size, "white")
                rgb.paste(image, mask=image.getchannel("A"))
                for quality in (92, 84, 76):
                    stream = BytesIO()
                    rgb.save(stream, format="JPEG", quality=quality)
                    data = stream.getvalue()
                    if len(data) <= MAX_OUTPUT:
                        return {"data": base64.b64encode(data).decode(), "mime_type": "image/jpeg"}
        raise HACommandError("photo_too_large")
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HACommandError("invalid_photo") from None


class ReadingPhotos:
    """Mixin using the existing user/profile session and explicit HA confirmation."""

    def photo_session(self, user, profile):
        session = self.sessions.get(user)
        if (not session or session["expires"] <= time.time() or session["profile"] != profile.key
                or profile not in self.profiles(user) or not profile.allow_readings
                or profile.mode != "personal"):
            raise HACommandError("confirmation_expired")
        if not session["context"].get("photo_submission"):
            raise HACommandError("photos_unsupported")
        return session

    async def photo_action(self, user, profile, action, zone):
        from conversations import ZONE_LABELS
        session = self.photo_session(user, profile)
        self.invalidate_reading_buttons(user)
        session.pop("photo_zone", None)
        photos = session.setdefault("photos", {})
        zones = [z["zone"] for z in session["context"]["zones"]]
        if action == "photo_remove":
            photos.clear()
        if action == "photo_review":
            await self.review(user, profile, session)
            return
        if action == "photo_zone":
            if zone not in zones:
                raise HACommandError("invalid_photo")
            session["photo_zone"] = zone
            session["photo_requested"] = int(time.time())
            await self.reply(user, profile, "📷 Фото лічильника",
                f"Тариф: <b>{escape(ZONE_LABELS.get(zone, zone))}</b>. Покази: <b>{escape(session['values'][zone])} кВт·год</b>.\n\n"
                "Надішліть одне фото або файл JPEG, PNG чи WebP до 20 МБ. Покази, тариф і серійний номер мають бути чіткими. Альбоми не підтримуються. Фото ще не надсилається в ДТЕК.",
                [("⬅️ До фото", "photos", None)])
            return
        actions = [(f"{'✅' if z in photos else '📷'} {ZONE_LABELS.get(z, z)} — {'замінити' if z in photos else 'додати'} фото", "photo_zone", z) for z in zones]
        if not photos or set(photos) == set(zones):
            actions.append(("➡️ Перевірити покази", "photo_review", None))
        if photos:
            actions.append(("🗑 Прибрати всі фото", "photo_remove", None))
        await self.reply(user, profile, "📷 Фото до показів",
            "Додайте фото кожного тарифу. Перевірте знімок після оптимізації. Можна прибрати всі фото й передати лише покази.\n\n"
            + "\n".join(f"{'✅' if z in photos else '⬜'} {escape(ZONE_LABELS.get(z,z))}" for z in zones), actions)

    async def input_photo(self, user, profile, message):
        from conversations import ZONE_LABELS
        session = self.photo_session(user, profile)
        zone = session.get("photo_zone")
        if not zone or message.get("date", 0) < session.get("photo_requested", 0):
            await self.reply(user, profile, "📷 Спочатку оберіть тариф", "Натисніть «Додати фото» у підтвердженні показів.")
            return
        if message.get("media_group_id"):
            raise HACommandError("photo_album")
        attachment = (message.get("photo") or [None])[-1] or message.get("document") or {}
        if message.get("document") and attachment.get("mime_type") not in ("image/jpeg", "image/png", "image/webp"):
            raise HACommandError("invalid_photo")
        if attachment.get("file_size", 0) > MAX_INPUT:
            raise HACommandError("photo_too_large")
        async with asyncio.timeout(45):
            file = await self.tg._bot.get_file(attachment["file_id"])
            if (file.file_size or 0) > MAX_INPUT:
                raise HACommandError("photo_too_large")
            stream = BoundedBuffer()
            await self.tg._bot.download_file(file.file_path, destination=stream, timeout=35)
            photo = await asyncio.to_thread(prepare_photo, stream.getvalue())
        if self.photo_session(user, profile) is not session:
            raise HACommandError("confirmation_expired")
        session.setdefault("photos", {})[zone] = photo
        session.pop("photo_zone", None)
        self.invalidate_reading_buttons(user)
        await self.tg.replace_menu(user, lambda: self.tg._bot.send_photo(
            chat_id=user, photo=BufferedInputFile(base64.b64decode(photo["data"]), filename="meter.jpg"),
            caption=f"📷 {ZONE_LABELS.get(zone, zone)} · {session['values'][zone]} кВт·год\nПеревірте чіткість і відповідність показам. Фото ще не передано в ДТЕК.",
            reply_markup=self.navigation(user, profile, [("📷 До фото та перевірки", "photos", None)])))

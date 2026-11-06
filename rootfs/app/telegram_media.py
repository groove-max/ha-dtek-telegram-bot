"""Journaled schedule albums; text delivery does not depend on media support."""

import hashlib
import time

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter, TelegramForbiddenError
from aiogram.types import BufferedInputFile, InputMediaPhoto, ReplyParameters


async def send_schedule_images(service, images, *, chat_id, reply_to, delivery_key,
                               disable_notification, context, schedule=None):
    if not images or len(images) > 10 or not service.journal:
        return True
    chat = service.resolve_chat_id(chat_id)
    # A parent message ID is local to this exact chat. Never attach to an
    # unrelated production message when test routing changes destinations.
    if not service.journal.owns(chat, reply_to):
        return True
    key = hashlib.sha256(f"{chat}:{delivery_key}".encode()).hexdigest()
    journal = service.journal
    previous = journal.delivery(key)
    if previous:
        if previous[0] != "retry":
            return True  # Accepted, permanently rejected, or ambiguous: never repeat.
        if not journal.retry_due(key, time.time()):
            row = journal.db.execute("SELECT expires FROM deliveries WHERE key=?", (key,)).fetchone()
            return row is None or row[0] <= time.time()
    scope = f"{chat}:{context['address']}:{context['feature']}:{context['stream']}"
    journal.prepare_delivery(key, scope, {"chat": chat, "context": context, "reply_to": reply_to}, time.time() + 600)

    messages = []

    async def operation():
        reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
        files = [InputMediaPhoto(media=BufferedInputFile(data, filename=name), caption=caption)
                 for data, name, caption in images]
        if len(files) == 1:
            item = files[0]
            result = await service._bot.send_photo(chat_id=chat, photo=item.media, caption=item.caption,
                                                  reply_parameters=reply, disable_notification=disable_notification)
            messages.append(result)
            return result
        result = await service._bot.send_media_group(chat_id=chat, media=files,
                                                      reply_parameters=reply, disable_notification=disable_notification)
        messages.extend(result)
        return result[0]

    try:
        result = await service._recorded(operation, action="send", chat=chat,
                                         text="\n".join(i[2] for i in images), context=context)
        # Every album page needs independent ownership for later media edits.
        with journal.db:
            for msg in messages:
                journal.db.execute("INSERT OR IGNORE INTO owned_messages VALUES(?,?)", (str(chat), msg.message_id))
        if schedule:
            journal.schedule_images.remember(chat, context['address'], schedule['date'],
                schedule['binding'], [msg.message_id for msg in messages], schedule['groups'],
                schedule.get('interval', 300), schedule.get('content_hash', ''))
        journal.finish_delivery(key, "ok", result.message_id)
        return True
    except Exception as exc:
        retry = isinstance(exc, TelegramRetryAfter) or type(exc).__name__ == "TelegramServerError"
        journal.finish_delivery(key, "retry" if retry else "rejected" if isinstance(exc, TelegramBadRequest) else "unknown")
        if retry:
            with journal.db:
                journal.db.execute("UPDATE deliveries SET retry_at=? WHERE key=?",
                                   (time.time() + max(60, getattr(exc, "retry_after", 0) or 0), key))
        return not retry


async def edit_schedule_image(service, message_id, image, *, chat_id, context):
    """Replace only an owned photo. No fallback send and no sleeping retry."""
    chat = service.resolve_chat_id(chat_id)
    if str(chat) != str(chat_id) or not service.journal or not service.journal.owns(chat, message_id):
        return "gone", 0
    data, name, caption = image
    async def operation():
        return await service._bot.edit_message_media(chat_id=chat, message_id=message_id,
            media=InputMediaPhoto(media=BufferedInputFile(data, filename=name), caption=caption))
    try:
        await service._recorded(operation, action="edit_media", chat=chat, message_id=message_id,
                                text=caption, context=context, retry=False)
        return "ok", 0
    except TelegramRetryAfter as exc:
        return "retry", exc.retry_after
    except TelegramForbiddenError:
        return "gone", 0
    except TelegramBadRequest as exc:
        error = str(exc).lower()
        if "message is not modified" in error:
            return "ok", 0
        if any(term in error for term in ("message to edit not found", "message can't be edited", "message can not be edited", "message_id_invalid", "chat not found")):
            return "gone", 0
        return "retry", 0
    except Exception:
        # Editing the same ID is idempotent even after an ambiguous timeout.
        return "retry", 0

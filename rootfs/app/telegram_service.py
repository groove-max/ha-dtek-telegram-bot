"""Telegram Bot API wrapper using aiogram 3.x."""

from __future__ import annotations

import asyncio
import logging
import time
import hashlib
import fcntl
from pathlib import Path
from contextlib import suppress
from delivery_journal import DeliveryJournal
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

logger = logging.getLogger(__name__)

T = TypeVar("T")
MAX_RETRY_ATTEMPTS = 3
EditMessageResult = Literal["ok", "not_found", "error"]


class TelegramService:
    """Sends messages to a Telegram chat/group via Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        test_chat_id: str = "",
        journal: DeliveryJournal | None = None,
    ) -> None:
        self._bot = Bot(token=bot_token)
        self._default_chat_id = chat_id
        self._test_chat_id = test_chat_id
        self.journal = journal
        self._feedback_task = None
        self._feedback_lock = None

    @property
    def default_chat_id(self) -> str:
        return self._test_chat_id or self._default_chat_id

    def _resolve_chat_id(self, chat_id: str | None) -> str:
        return self._test_chat_id or chat_id or self._default_chat_id

    def resolve_chat_id(self, chat_id: str | None = None) -> str:
        return self._resolve_chat_id(chat_id)

    def _may_mutate(self, chat: str, message_id: int) -> bool:
        return not self._test_chat_id or bool(
            self.journal and self.journal.owns(chat, message_id)
        )

    async def _recorded(
        self, operation, *, action, chat, message_id=None, text=None, context=None
    ):
        row = (
            self.journal.begin(
                action, chat, message_id, {"text": text, "context": context}
            )
            if self.journal
            else None
        )
        try:
            # A send is revalidated by its feature before retrying; sleeping
            # here could deliver an obsolete countdown or voltage alert.
            result = (
                await operation()
                if action == "send"
                else await self._call_with_retry(operation, action=action)
            )
        except Exception as exc:
            # API rejection is definite. Network failure can occur after delivery;
            # do not automatically repeat an ambiguous send.
            status = (
                "rejected"
                if isinstance(exc, (TelegramBadRequest, TelegramRetryAfter))
                or type(exc).__name__ == "TelegramServerError"
                else "unknown"
            )
            if (
                isinstance(exc, TelegramBadRequest)
                and "message is not modified" in str(exc).lower()
            ):
                status = "ok"
            if row is not None:
                self.journal.finish(row, status, {"error_type": type(exc).__name__})
            raise
        if row is not None:
            payload = (
                result.model_dump(mode="json", exclude_none=True)
                if hasattr(result, "model_dump")
                else result
            )
            self.journal.finish(row, "ok", payload, getattr(result, "message_id", None))
        return result

    async def start_feedback(self, lock_path: Path) -> None:
        if not self._test_chat_id or not self.journal:
            raise ValueError("Feedback requires an isolated test target and journal")
        self._feedback_lock = lock_path.open("a")
        fcntl.flock(self._feedback_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        webhook = await self._bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError(
                "Feedback cannot start while a Telegram webhook is configured"
            )
        self._feedback_task = asyncio.create_task(self._poll_feedback())

    async def _poll_feedback(self):
        while True:
            try:
                updates = await self._bot.get_updates(
                    offset=self.journal.offset,
                    timeout=30,
                    allowed_updates=[
                        "message",
                        "edited_message",
                        "channel_post",
                        "edited_channel_post",
                    ],
                )
                for update in updates:
                    self.journal.ingest(
                        update.update_id,
                        update.model_dump(mode="json", exclude_none=True),
                        self._test_chat_id,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram feedback failed: %s", type(exc).__name__)
                if type(exc).__name__ == "TelegramConflictError":
                    logger.error(
                        "Feedback stopped: another Telegram consumer is active"
                    )
                    return
                await asyncio.sleep(5)

    async def _call_with_retry(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        action: str,
    ) -> T:
        """Retry Telegram API calls when the Bot API asks us to back off."""
        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            try:
                return await operation()
            except TelegramRetryAfter as e:
                retry_after = max(float(getattr(e, "retry_after", 1)), 1.0)
                if attempt >= MAX_RETRY_ATTEMPTS:
                    logger.error(
                        "Telegram flood control while %s; giving up after %d attempts",
                        action,
                        attempt,
                    )
                    raise
                logger.warning(
                    "Telegram flood control while %s; retrying in %.1fs (attempt %d/%d)",
                    action,
                    retry_after,
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                )
                await asyncio.sleep(retry_after)

        raise RuntimeError("unreachable")

    async def send_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        parse_mode: ParseMode | None = None,
        disable_notification: bool = False,
        pin: bool = False,
        context: dict | None = None,
        delivery_key: str | None = None,
    ) -> int | None:
        """Send a text message. Returns message_id or None on failure."""
        resolved_chat_id = self._resolve_chat_id(chat_id)
        key = (
            hashlib.sha256(f"{resolved_chat_id}:{delivery_key}".encode()).hexdigest()
            if delivery_key
            else None
        )
        if key and self.journal:
            prior = self.journal.delivery(key)
            if prior:
                if prior[0] == "ok":
                    return prior[1]
                # The feature has just re-read HA and confirmed this notification
                # is still relevant. Use its current text (including countdown).
                if not self.journal.retry_due(key, time.time()):
                    return None
        return await self._send(
            text, resolved_chat_id, parse_mode, disable_notification, pin, context, key
        )

    async def _send(
        self,
        text,
        resolved_chat_id,
        parse_mode,
        disable_notification,
        pin,
        context,
        key,
    ):
        if key and self.journal:
            scope = f"{resolved_chat_id}:{(context or {}).get('address')}:{(context or {}).get('feature')}:{(context or {}).get('stream', '')}"
            expires = min(
                time.time() + 600, (context or {}).get("expires", float("inf"))
            )
            self.journal.prepare_delivery(
                key,
                scope,
                dict(
                    text=text,
                    chat=resolved_chat_id,
                    parse_mode=parse_mode,
                    disable_notification=disable_notification,
                    pin=pin,
                    context=context,
                ),
                expires,
            )
        try:
            msg = await self._recorded(
                lambda: self._bot.send_message(
                    chat_id=resolved_chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    disable_notification=disable_notification,
                ),
                action="send",
                chat=resolved_chat_id,
                text=text,
                context=context,
            )
            if key and self.journal:
                self.journal.finish_delivery(key, "ok", msg.message_id)
            logger.debug("Sent message %d to %s", msg.message_id, resolved_chat_id)

            if pin:
                await self.pin_message(
                    msg.message_id,
                    chat_id=resolved_chat_id,
                    disable_notification=True,
                )

            return msg.message_id
        except Exception as exc:
            if key and self.journal:
                status = (
                    "retry"
                    if isinstance(exc, TelegramRetryAfter)
                    or type(exc).__name__ == "TelegramServerError"
                    else (
                        "rejected" if isinstance(exc, TelegramBadRequest) else "unknown"
                    )
                )
                self.journal.finish_delivery(key, status)
            logger.error(
                "Failed to send message to %s: %s", resolved_chat_id, type(exc).__name__
            )
            return None

    async def retry_pending(self, state) -> None:
        if not self.journal:
            return
        for key, payload in self.journal.retries():
            context = payload.get("context") or {}
            # These features retry only after fetching a consistent, fresh
            # observation themselves. Never replay cached schedules or power alerts.
            if context.get("feature") in (
                "group_change",
                "schedule_change",
                "upcoming_outage",
                "power_presence",
            ):
                continue
            recorded = context.get("state") or {}
            prefix = context.get("address", "")
            relevant = ("current_groups", "power_state")
            if context.get("feature") == "emergency":
                relevant += (
                    "last_outage_start",
                    "last_outage_end",
                    "last_outage_description",
                )
            if any(
                recorded.get(field) != state.get(prefix, field) for field in relevant
            ):
                self.journal.finish_delivery(key, "superseded")
                continue
            if any(
                state.get(prefix, field) != value
                for field, value in context.get("guards", {}).items()
            ):
                self.journal.finish_delivery(key, "superseded")
                continue
            await self._send(
                payload["text"],
                self._resolve_chat_id(payload["chat"]),
                payload.get("parse_mode"),
                payload["disable_notification"],
                payload["pin"],
                context,
                key,
            )

    async def edit_message_result(
        self,
        message_id: int,
        text: str,
        *,
        chat_id: str | None = None,
        parse_mode: ParseMode | None = None,
    ) -> EditMessageResult:
        """Edit an existing message and classify the outcome."""
        resolved_chat_id = self._resolve_chat_id(chat_id)
        if not self._may_mutate(resolved_chat_id, message_id):
            return "not_found"
        try:
            await self._recorded(
                lambda: self._bot.edit_message_text(
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                ),
                action="edit",
                chat=resolved_chat_id,
                message_id=message_id,
                text=text,
            )
            logger.debug("Edited message %d", message_id)
            return "ok"
        except TelegramBadRequest as e:
            err = str(e)
            err_lower = err.lower()
            if "message is not modified" in err_lower:
                logger.debug("Message %d content unchanged, skipping", message_id)
                return "ok"
            if (
                "message to edit not found" in err_lower
                or "message_id_invalid" in err_lower
                or "message can't be edited" in err_lower
            ):
                logger.info(
                    "Message %d is not editable in the current chat, will re-create",
                    message_id,
                )
                return "not_found"
            logger.exception("Failed to edit message %d", message_id)
            return "error"
        except Exception:
            logger.exception("Failed to edit message %d", message_id)
            return "error"

    async def edit_message(
        self,
        message_id: int,
        text: str,
        *,
        chat_id: str | None = None,
        parse_mode: ParseMode | None = None,
    ) -> bool:
        """Backward-compatible boolean wrapper around edit_message_result."""
        result = await self.edit_message_result(
            message_id,
            text,
            chat_id=chat_id,
            parse_mode=parse_mode,
        )
        return result == "ok"

    async def pin_message(
        self,
        message_id: int,
        *,
        chat_id: str | None = None,
        disable_notification: bool = True,
    ) -> bool:
        """Pin a message in the chat. Returns True on success."""
        resolved_chat_id = self._resolve_chat_id(chat_id)
        if not self._may_mutate(resolved_chat_id, message_id):
            return False
        try:
            await self._recorded(
                lambda: self._bot.pin_chat_message(
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                    disable_notification=disable_notification,
                ),
                action="pin",
                chat=resolved_chat_id,
                message_id=message_id,
            )
            logger.debug("Pinned message %d", message_id)
            return True
        except Exception:
            logger.exception("Failed to pin message %d", message_id)
            return False

    async def unpin_message(
        self,
        message_id: int,
        *,
        chat_id: str | None = None,
    ) -> bool:
        """Unpin a message. Returns True on success."""
        resolved_chat_id = self._resolve_chat_id(chat_id)
        if not self._may_mutate(resolved_chat_id, message_id):
            return False
        try:
            await self._recorded(
                lambda: self._bot.unpin_chat_message(
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                ),
                action="unpin",
                chat=resolved_chat_id,
                message_id=message_id,
            )
            logger.debug("Unpinned message %d", message_id)
            return True
        except Exception:
            logger.exception("Failed to unpin message %d", message_id)
            return False

    async def close(self) -> None:
        """Close the bot session."""
        if self._feedback_task:
            self._feedback_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._feedback_task
        if self._feedback_lock:
            self._feedback_lock.close()
        if self.journal:
            self.journal.close()
        try:
            await self._bot.session.close()
        except Exception:
            logger.exception("Failed to close bot session")

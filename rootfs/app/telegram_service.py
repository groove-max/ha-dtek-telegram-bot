"""Telegram Bot API wrapper using aiogram 3.x."""

from __future__ import annotations

import asyncio
import logging
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

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot = Bot(token=bot_token)
        self._default_chat_id = chat_id

    @property
    def default_chat_id(self) -> str:
        return self._default_chat_id

    def _resolve_chat_id(self, chat_id: str | None) -> str:
        return chat_id or self._default_chat_id

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
    ) -> int | None:
        """Send a text message. Returns message_id or None on failure."""
        resolved_chat_id = self._resolve_chat_id(chat_id)
        try:
            msg = await self._call_with_retry(
                lambda: self._bot.send_message(
                    chat_id=resolved_chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    disable_notification=disable_notification,
                ),
                action=f"sending a message to {resolved_chat_id}",
            )
            logger.debug("Sent message %d to %s", msg.message_id, resolved_chat_id)

            if pin:
                await self.pin_message(
                    msg.message_id,
                    chat_id=resolved_chat_id,
                    disable_notification=True,
                )

            return msg.message_id
        except Exception:
            logger.exception("Failed to send message to %s", resolved_chat_id)
            return None

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
        try:
            await self._call_with_retry(
                lambda: self._bot.edit_message_text(
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                ),
                action=f"editing message {message_id}",
            )
            logger.debug("Edited message %d", message_id)
            return "ok"
        except TelegramBadRequest as e:
            err = str(e)
            if "message is not modified" in err:
                logger.debug("Message %d content unchanged, skipping", message_id)
                return "ok"
            if "message to edit not found" in err or "MESSAGE_ID_INVALID" in err:
                logger.info("Message %d no longer exists, will re-create", message_id)
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
        try:
            await self._call_with_retry(
                lambda: self._bot.pin_chat_message(
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                    disable_notification=disable_notification,
                ),
                action=f"pinning message {message_id}",
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
        try:
            await self._call_with_retry(
                lambda: self._bot.unpin_chat_message(
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                ),
                action=f"unpinning message {message_id}",
            )
            logger.debug("Unpinned message %d", message_id)
            return True
        except Exception:
            logger.exception("Failed to unpin message %d", message_id)
            return False

    async def close(self) -> None:
        """Close the bot session."""
        try:
            await self._bot.session.close()
        except Exception:
            logger.exception("Failed to close bot session")

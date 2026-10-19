"""Replace only recorded bot menu screens; notifications and status are untouched."""

import asyncio
import logging
import time

from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


class DialogueScreens:
    def __init__(self, db, bot):
        self.db, self.bot = db, bot
        self.lock = asyncio.Lock()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS dialogue_screens (chat_id TEXT PRIMARY KEY, message_id INTEGER, created REAL);
            CREATE TABLE IF NOT EXISTS obsolete_screens (chat_id TEXT, message_id INTEGER, created REAL, PRIMARY KEY(chat_id,message_id));
        """)

    async def replace(self, chat, operation):
        if not str(chat).isdigit() or int(chat) <= 0:
            raise ValueError("Menu screens require an explicit private chat")
        chat = str(chat)
        async with self.lock:
            result = await operation()
            message_id = getattr(result, "message_id", None)
            if type(message_id) is not int or message_id <= 0:
                raise ValueError("Telegram did not return a menu message ID")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO obsolete_screens SELECT chat_id,message_id,created FROM dialogue_screens WHERE chat_id=? AND message_id!=?", (chat, message_id))
                self.db.execute("INSERT INTO dialogue_screens VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET message_id=excluded.message_id,created=excluded.created", (chat, message_id, time.time()))
            await self.cleanup(chat)
            return result

    async def cleanup(self, chat):
        rows = self.db.execute("""SELECT message_id,created FROM obsolete_screens
            WHERE chat_id=? AND message_id NOT IN (SELECT message_id FROM dialogue_screens WHERE chat_id=?)
            ORDER BY created DESC LIMIT 20""", (str(chat), str(chat))).fetchall()
        for message_id, created in rows:
            expired = time.time() - created >= 48 * 3600 - 30
            try:
                async with asyncio.timeout(5):
                    if expired:
                        # Telegram cannot delete old messages. Make obsolete controls inert.
                        await self.bot.edit_message_reply_markup(chat_id=chat, message_id=message_id, reply_markup=None)
                    else:
                        await self.bot.delete_message(chat_id=chat, message_id=message_id)
            except TelegramBadRequest:
                # Already deleted or no longer deletable; never target other IDs.
                pass
            except Exception as exc:
                logger.warning("Menu cleanup delayed: %s", type(exc).__name__)
                break
            with self.db:
                self.db.execute("DELETE FROM obsolete_screens WHERE chat_id=? AND message_id=?", (str(chat), message_id))
        with self.db:
            self.db.execute("DELETE FROM obsolete_screens WHERE created<?", (time.time() - 7 * 86400,))

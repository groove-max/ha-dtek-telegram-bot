"""On-demand building access; never enumerate members or create delivery routes."""

import asyncio
import time
from collections import OrderedDict


def group_chat(chat):
    value = str(chat or "")
    return (value.startswith("-") and value[1:].isdigit() and int(value) < 0) or value.startswith("@")


class BuildingAccess:
    def __init__(self, app, bot_id, telegram):
        self.app, self.bot_id, self.tg = app, bot_id, telegram
        self.grants = OrderedDict()
        self.failed = False

    def candidates(self):
        if not self.app.config.receive_commands or self.tg._test_chat_id:
            return []
        return [p for p in self.app.config.addresses
                if p.mode == "building" and p.member_access
                and p.telegram_bot_id == self.bot_id and group_chat(self.app.bots.chat(p))]

    def binding(self, profile):
        return (profile.key, profile.entity_prefix, str(self.app.bots.chat(profile)), self.bot_id)

    def profiles(self, user):
        at, bindings = self.grants.get(user, (0, ()))
        if time.monotonic() - at > 60:
            return []
        return [p for p in self.candidates() if self.binding(p) in bindings]

    async def refresh(self, user):
        """Every incoming private interaction obtains new membership evidence.

        A short-lived snapshot supports synchronous authorization in the shared
        conversation code. It is discarded before checking; failures never reuse
        a previous positive result. Different profiles sharing a chat use one
        check. A bot without administrator rights cannot grant membership access.
        """
        self.grants.pop(user, None)
        self.failed = False
        candidates = self.candidates()
        if not candidates:
            return
        chats = dict.fromkeys(str(self.app.bots.chat(p)) for p in candidates)
        semaphore = asyncio.Semaphore(4)

        async def check(chat):
            async with semaphore:
                try:
                    async with asyncio.timeout(8):
                        bot = self.tg._bot
                        own = await bot.get_chat_member(chat_id=chat, user_id=bot.id)
                        if own.status not in ("creator", "administrator"):
                            self.failed = True
                            return chat, False
                        member = await bot.get_chat_member(chat_id=chat, user_id=user)
                        present = member.status in ("creator", "administrator", "member") or (
                            member.status == "restricted" and member.is_member is True)
                        return chat, present
                except Exception:
                    self.failed = True
                    return chat, False

        try:
            async with asyncio.timeout(15):
                result = dict(await asyncio.gather(*(check(chat) for chat in chats)))
        except TimeoutError:
            self.failed = True
            return
        bindings = {self.binding(p) for p in candidates if result.get(str(self.app.bots.chat(p)))}
        self.grants[user] = (time.monotonic(), bindings)
        while len(self.grants) > 512:
            self.grants.popitem(last=False)

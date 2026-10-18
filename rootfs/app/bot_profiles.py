"""Private Telegram credentials and public, reusable destination profiles."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aiogram import Bot
from state_store import atomic_write

PROFILES_PATH = Path("/data/telegram_bots.json")


@dataclass(frozen=True)
class BotProfile:
    id: str
    bot_id: str
    name: str
    username: str = ""
    token: str = field(default="", repr=False)
    chat_id: str = ""
    test_chat_id: str = ""
    test_mode: bool = False
    feedback_enabled: bool = False

    def public(self) -> dict:
        return {key: value for key, value in asdict(self).items() if key != "token"}


def default_profile(config) -> BotProfile:
    prefix, separator, _ = config.telegram_bot_token.partition(":")
    return BotProfile(
        id="default",
        bot_id=prefix if separator and prefix.isdecimal() else "",
        name="Основний бот",
        token=config.telegram_bot_token,
        chat_id=config.telegram_chat_id,
        test_chat_id=config.test_chat_id,
        test_mode=bool(config.test_chat_id),
        feedback_enabled=config.feedback_enabled,
    )


class ProfileConflict(ValueError):
    """A concurrent editor changed the private profiles."""


class BotProfileStore:
    """Atomic, mode-0600 storage. Tokens never appear in read API responses."""

    def __init__(self, path: Path = PROFILES_PATH):
        self.path = path

    def read(self) -> dict[str, BotProfile]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {key: BotProfile(**value) for key, value in raw.items()}

    def revision(self) -> str:
        content = self.path.read_bytes() if self.path.exists() else b""
        return hashlib.sha256(content).hexdigest()

    def snapshot(self, config) -> dict:
        profiles = [default_profile(config), *self.read().values()]
        return {"revision": self.revision(), "profiles": [p.public() for p in profiles]}

    def _check(self, expected: str) -> None:
        if expected != self.revision():
            raise ProfileConflict(
                "Список ботів уже змінено. Оновіть список і повторіть спробу."
            )

    async def save(self, payload: dict, config) -> BotProfile:
        self._check(payload.get("expected_revision", ""))
        profiles = self.read()
        profile_id = str(payload.get("id", ""))
        previous = profiles.get(profile_id)
        if profile_id and previous is None:
            raise ValueError("Цього профілю вже немає. Оновіть список ботів.")
        token = str(payload.get("token", "")).strip() or (
            previous.token if previous else ""
        )
        name = str(payload.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ValueError("Вкажіть назву бота (до 80 символів).")
        test_chat_id = str(payload.get("test_chat_id", "")).strip()
        feedback = payload.get("feedback_enabled", False) is True
        test_mode = payload.get("test_mode", False) is True
        if test_mode and not test_chat_id:
            raise ValueError("Для тестового режиму вкажіть тестовий чат.")
        if feedback and not (test_chat_id and test_mode):
            raise ValueError(
                "Для збору зворотного зв’язку ввімкніть тестовий режим і вкажіть тестовий чат."
            )
        bot = None
        try:
            bot = Bot(token=token)
            async with asyncio.timeout(15):
                me = await bot.get_me()
            bot_id = str(me.id)
            username = me.username or ""
        except Exception:
            # Telegram exceptions and validation errors can contain credentials.
            raise ValueError(
                "Не вдалося перевірити токен у Telegram. Перевірте токен і з’єднання."
            ) from None
        finally:
            if bot is not None:
                await bot.session.close()
        if bot_id == default_profile(config).bot_id:
            raise ValueError("Це основний бот. Оберіть його в налаштуваннях адреси.")
        if previous and previous.bot_id != bot_id:
            raise ValueError("Токен належить іншому боту. Додайте окремий профіль.")
        if any(p.bot_id == bot_id and p.id != profile_id for p in profiles.values()):
            raise ValueError("Цього бота вже додано. Оберіть його у списку.")
        profile = BotProfile(
            id=profile_id or f"bot_{bot_id}",
            bot_id=bot_id,
            username=username,
            name=name,
            token=token,
            chat_id=str(payload.get("chat_id", "")).strip(),
            test_chat_id=test_chat_id,
            test_mode=test_mode,
            feedback_enabled=feedback,
        )
        # Recheck after getMe; two tabs must not overwrite each other's credentials.
        self._check(payload.get("expected_revision", ""))
        profiles[profile.id] = profile
        self._write(profiles)
        return profile

    def delete(self, profile_id: str, expected: str, referenced: set[str]) -> None:
        self._check(expected)
        if profile_id == "default" or profile_id in referenced:
            raise ValueError(
                "Бот використовується. Спочатку змініть адреси та перезапустіть застосунок."
            )
        profiles = self.read()
        if profile_id not in profiles:
            raise ValueError("Профіль не знайдено.")
        del profiles[profile_id]
        self._write(profiles)

    def _write(self, profiles: dict[str, BotProfile]) -> None:
        atomic_write(
            self.path,
            json.dumps({k: asdict(v) for k, v in profiles.items()}, ensure_ascii=False),
        )


def validate_destinations(config, store: BotProfileStore) -> None:
    profiles = {"default": default_profile(config), **store.read()}
    seen = set()
    for address in config.addresses:
        if address.entity_prefix in seen:
            raise ValueError(
                "Адресу додано двічі. Для кожної адреси залиште один запис."
            )
        seen.add(address.entity_prefix)
        profile = profiles.get(address.telegram_bot_id)
        if not profile or not profile.token:
            raise ValueError(
                "Для адреси обрано неналаштованого бота. Додайте або оберіть бота."
            )
        if not (
            (profile.test_chat_id if profile.test_mode else "")
            or address.telegram_chat_id
            or profile.chat_id
        ):
            raise ValueError(
                "Вкажіть чат для адреси або чат за замовчуванням обраного бота."
            )

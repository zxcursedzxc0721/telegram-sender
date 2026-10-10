"""Telethon-клиенты и конвертация Markdown.

Отправка: Telethon получает оригинальный Markdown с parse_mode='md' и сам
расставляет entities — форматирование гарантированно доходит до чатов.

Превью в панели: Telethon парсит Markdown в (текст, entities), а мы рисуем
из этого HTML — превью совпадает с тем, что реально уйдёт в чат.
"""
from __future__ import annotations

import html as html_mod
import logging
import time
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.extensions import markdown as tg_markdown

from ..config import SESSIONS_DIR, config
from ..proxy import telethon_proxy

log = logging.getLogger(__name__)

# Сколько клиентов держим подключёнными одновременно. Каждый клиент — это
# живое MTProto-соединение + слушатель апдейтов + кэш entity. На десятках
# аккаунтов это дорого, поэтому лишние отключаем (LRU): при следующей
# отправке клиент молча подключится заново.
MAX_CONNECTED = 10

# Теги для каждого типа entity Telethon
_ENT_TAGS = {
    "MessageEntityBold": ("b",),
    "MessageEntityItalic": ("i",),
    "MessageEntityStrike": ("s",),
    "MessageEntityCode": ("code",),
    "MessageEntityPre": ("pre",),
    "MessageEntityTextUrl": ("a",),
    "MessageEntityUnderline": ("u",),
    "MessageEntitySpoiler": ("tg-spoiler",),
}


def _utf16_to_py_index(text: str, utf16_pos: int) -> int:
    """Смещение в UTF-16 code units -> индекс в python-строке."""
    units = 0
    for i, ch in enumerate(text):
        if units >= utf16_pos:
            return i
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def md_to_html(text: str) -> str:
    """Markdown (диалект Telethon) -> HTML для превью в панели."""
    if not text:
        return ""
    clean, entities = tg_markdown.parse(text)
    if not entities:
        return html_mod.escape(clean)

    # Собираем вставки тегов по позициям (в python-индексах)
    openings: dict[int, list[str]] = {}
    closings: dict[int, list[tuple[int, str]]] = {}  # позиция -> [(порядок, тег)]

    for ent in entities:
        name = type(ent).__name__
        tags = _ENT_TAGS.get(name)
        if not tags:
            continue
        start = _utf16_to_py_index(clean, ent.offset)
        end = _utf16_to_py_index(clean, ent.offset + ent.length)
        tag = tags[0]
        if name == "MessageEntityTextUrl":
            url = html_mod.escape(getattr(ent, "url", "") or "", quote=True)
            open_tag = f'<a href="{url}">'
        else:
            open_tag = f"<{tag}>"
        openings.setdefault(start, []).append(open_tag)
        closings.setdefault(end, []).append((start, f"</{tag}>"))

    out: list[str] = []
    for i, ch in enumerate(clean):
        if i in closings:
            # закрываем в обратном порядке открытия
            for _, close_tag in sorted(closings[i], reverse=True):
                out.append(close_tag)
        if i in openings:
            out.extend(openings[i])
        out.append(html_mod.escape(ch))
    # закрываем висящие теги в конце
    for end_pos in sorted(closings):
        if end_pos >= len(clean):
            for _, close_tag in sorted(closings[end_pos], reverse=True):
                out.append(close_tag)
    return "".join(out)


class AccountClient:
    """Живой Telethon-клиент одного аккаунта."""

    def __init__(self, account_id: int, session_name: str):
        self.account_id = account_id
        self.session_name = session_name
        self.client = TelegramClient(
            str(SESSIONS_DIR / session_name),
            config.api_id,
            config.api_hash,
            flood_sleep_threshold=60,  # сами спим при флуде до 60 сек
            proxy=telethon_proxy(config.proxy),  # прокси, если задан в .env
        )
        self._connected = False
        self.last_used: float = time.monotonic()

    async def ensure_connected(self) -> None:
        self.last_used = time.monotonic()
        if not self._connected:
            await self.client.connect()
            self._connected = True

    async def is_authorized(self) -> bool:
        await self.ensure_connected()
        return bool(await self.client.is_user_authorized())

    async def _resolve(self, chat_ref: str):
        """Превращает chat_ref из панели в entity Telethon.

        Понимает "@username", числовой id и "invite:<hash>" (приватная
        инвайт-ссылка t.me/+... — для неё нужен отдельный вызов, резолвить
        как юзернейм нельзя).
        """
        if chat_ref.startswith("invite:"):
            h = chat_ref.split(":", 1)[1]
        elif chat_ref.startswith("@+"):
            # старые записи в БД могли сохраниться с мусорным "@+hash" — чиним
            h = chat_ref[2:]
        else:
            return int(chat_ref) if chat_ref.lstrip("-").isdigit() else chat_ref

        from telethon.tl.functions.messages import (
            CheckChatInviteRequest,
            ImportChatInviteRequest,
        )
        from telethon.tl.types import ChatInviteAlready

        invite = await self.client(CheckChatInviteRequest(h))
        if isinstance(invite, ChatInviteAlready):
            return invite.chat  # уже состоим — просто берём чат
        updates = await self.client(ImportChatInviteRequest(h))  # вступаем
        return updates.chats[0]

    async def send(self, chat_ref: str, text: str, photo_path: Optional[str] = None) -> None:
        """Отправка поста: фото + подпись. Markdown уходит нативным parse_mode='md'."""
        await self.ensure_connected()
        entity = await self._resolve(chat_ref)
        body = text or "(пустой пост)"
        if photo_path and Path(photo_path).exists():
            await self.client.send_file(
                entity, photo_path, caption=body, parse_mode="md"
            )
        else:
            await self.client.send_message(entity, body, parse_mode="md")

    async def disconnect(self) -> None:
        if self._connected:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._connected = False


class ClientManager:
    """Пул клиентов по account_id. Ленивая инициализация, переиспользование.

    Если подключений набралось больше ``MAX_CONNECTED``, самый давно
    неиспользуемый клиент отключается — при следующей отправке он поднимется
    заново. Так дюжины аккаунтов не держат десятки живых соединений.
    """

    def __init__(self) -> None:
        self._pool: dict[int, AccountClient] = {}

    def get(self, account_id: int, session_name: str) -> AccountClient:
        acc = self._pool.get(account_id)
        if acc is not None:
            acc.last_used = time.monotonic()
            return acc

        self._evict_oldest()
        acc = AccountClient(account_id, session_name)
        self._pool[account_id] = acc
        return acc

    def _evict_oldest(self) -> None:
        """Отключаем самого старого неиспользуемого, если пул переполнен."""
        while len(self._pool) >= MAX_CONNECTED:
            oldest_id = min(self._pool, key=lambda i: self._pool[i].last_used)
            victim = self._pool.pop(oldest_id)
            log.info("пул переполнен: отключаю аккаунт %s (простаивал дольше всех)", oldest_id)
            self._disconnect_bg(victim)

    @staticmethod
    def _disconnect_bg(acc: AccountClient) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(acc.disconnect())
        except RuntimeError:
            pass  # цикл уже закрыт — отключаться некому

    def drop(self, account_id: int) -> None:
        acc = self._pool.pop(account_id, None)
        if acc:
            self._disconnect_bg(acc)

    async def stop_all(self) -> None:
        for acc in self._pool.values():
            await acc.disconnect()
        self._pool.clear()


manager = ClientManager()

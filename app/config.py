"""Конфигурация приложения: загрузка .env, пути, настройки."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"   #Telethon-сессии аккаунтов
MEDIA_DIR = DATA_DIR / "media"         #фотографии постов

load_dotenv(BASE_DIR / ".env")


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return frozenset(ids)


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: frozenset[int] = field(
        default_factory=lambda: _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    )
    api_id: int = int(os.getenv("API_ID", "0") or 0)
    api_hash: str = os.getenv("API_HASH", "")
    # Необязательный прокси для Telegram API, если напрямую нет доступа.
    # Примеры: socks5://user:pass@127.0.0.1:1080  или  http://127.0.0.1:8080
    proxy: str = os.getenv("PROXY", "")

    def validate(self) -> None:
        if not self.bot_token:
            raise SystemExit("BOT_TOKEN не задан. Заполни файл .env (см. .env.example)")
        if not self.admin_ids:
            raise SystemExit("ADMIN_IDS не задан. Укажи свой Telegram ID в .env")
        if not self.api_id or not self.api_hash:
            raise SystemExit("API_ID/API_HASH не заданы. Возьми их на https://my.telegram.org")


config = Config()

# создаём рабочие папки при импорте
for _dir in (DATA_DIR, SESSIONS_DIR, MEDIA_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

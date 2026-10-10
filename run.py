"""Точка входа: python run.py"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram.types import BotCommand

from app.bot import bot, dp, scheduler_loop
from app.config import config
from app.database import init_db
from app.handlers import accounts, posts, start, tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logging.getLogger("aiogram").setLevel(logging.WARNING)
log = logging.getLogger("run")


async def on_startup() -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="menu", description="Меню рассылок"),
        ]
    )


async def main() -> None:
    config.validate()
    await init_db()

    # роутеры: start первый (ловит /start), дальше остальные
    dp.include_router(start.router)
    dp.include_router(accounts.router)
    dp.include_router(posts.router)
    dp.include_router(tasks.router)

    scheduler = asyncio.create_task(scheduler_loop(), name="scheduler")
    await on_startup()
    log.info("Бот запущен. Админы: %s", sorted(config.admin_ids))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

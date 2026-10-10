"""Создание бота, роутеров и планировщика рассылок."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from telethon.errors import FloodWaitError

from .config import config
from .database import SessionLocal, Task
from .keyboards import task_list_kb
from .proxy import aiogram_session
from .tgsend.manager import manager

log = logging.getLogger(__name__)

_bot_session = aiogram_session(config.proxy)
bot = Bot(
    token=config.bot_token,
    session=_bot_session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


async def admin_only(event=None, **kwargs):
    """Фильтр: панелью пользуются только админы из ADMIN_IDS.

    aiogram вызывает callable-фильтр с событием первым позиционным аргументом,
    а ``event_from_user`` кладёт в kwargs (UserContextMiddleware). У Message
    атрибута ``event_from_user`` нет — он называется ``from_user``, поэтому
    проверяем все варианты.
    """
    user = kwargs.get("event_from_user")
    if user is None:
        user = getattr(event, "event_from_user", None) or getattr(event, "from_user", None)
    return user is not None and user.id in config.admin_ids


# ---------------------------------------------------------------- рассылки

# task_id -> utc-момент, до которого Telegram запрещает слать (флуд-пауза).
# Держим в памяти: это временный кулдаун, не состояние рассылки.
_flood_until: dict[int, datetime] = {}

# Сколько рассылок шлём одновременно. Больше — риск словить флуд у Телеграма,
# меньше — при большом числе аккаунтов очередь будет разгребаться медленно.
_SEND_CONCURRENCY = 5
_send_sem = asyncio.Semaphore(_SEND_CONCURRENCY)


async def run_task(task_id: int) -> str:
    """Одна отправка рассылки: аккаунт -> чат. Обновляет счётчики и ошибки.

    Возвращает короткий статус для показа пользователю в панели.
    """
    from sqlalchemy.orm import selectinload

    async with SessionLocal() as session:
        task = (
            await session.execute(
                select(Task)
                .where(Task.id == task_id)
                .options(selectinload(Task.post), selectinload(Task.account))
            )
        ).scalar_one_or_none()
        if task is None:
            return "❌ Рассылка не найдена (удалена?)"

        account = task.account
        post = task.post
        if not account.enabled:
            return (
                f"⚠️ Аккаунт <b>{account.name}</b> выключен — "
                "включи его в разделе «Аккаунты»."
            )
        if not task.enabled:
            return "⏸ Рассылка на паузе — сначала включи её кнопкой ▶️."

        client = manager.get(account.id, account.session_name)
        text = post.text.replace("{link}", task.link or "")
        prev_error = task.last_error
        try:
            await client.send(task.chat_ref, text, post.photo_path)
            task.sent_count += 1
            task.last_error = None
            status = f"✅ Отправлено в <code>{task.chat_ref}</code> (всего: {task.sent_count})"
        except FloodWaitError as exc:
            # Не ошибка, а пауза: Telegram попросил подождать. Не считаем её
            # поломкой и не шлём уведомление — просто ставим кулдаун в памяти,
            # не трогая last_sent_at (иначе сломается дата «последняя отправка»).
            wait = max(exc.seconds, 1)
            _flood_until[task.id] = datetime.utcnow() + timedelta(seconds=wait)
            status = (
                f"⏳ Telegram попросил подождать <b>{wait} с</b> — "
                "отправлю после паузы (это не ошибка)."
            )
            log.info("task %s: flood wait %ss", task_id, wait)
        except Exception as exc:  # noqa: BLE001 — показываем ошибку в панели
            task.last_error = str(exc)[:250]
            log.warning("task %s failed: %s", task_id, exc)
            status = f"❌ Не вышло: <code>{task.last_error}</code>"
            task.last_sent_at = datetime.utcnow()  # повтор по расписанию
            if task.last_error != prev_error:
                await _notify_admin(
                    f"⚠️ Рассылка #{task_id} (<b>{post.title}</b> → "
                    f"<code>{task.chat_ref}</code>) упала:\n{task.last_error}"
                )
        else:
            task.last_sent_at = datetime.utcnow()
        await session.commit()
        return status


async def _notify_admin(text: str) -> None:
    """Лучшее уведомление админу: одна ошибка = одно сообщение, без спама."""
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 — уведомление не должно ронять рассылку
            continue


async def scheduler_loop() -> None:
    """Каждые 30 секунд ищем рассылки, которым пора отправиться, и шлём их."""
    while True:
        try:
            now = datetime.utcnow()
            async with SessionLocal() as session:
                rows = (
                    await session.execute(
                        select(Task).where(Task.enabled.is_(True))
                    )
                ).scalars().all()

                due_ids: list[int] = []
                for t in rows:
                    # ждём, пока истечёт флуд-пауза
                    if t.id in _flood_until and _flood_until[t.id] > now:
                        continue
                    _flood_until.pop(t.id, None)

                    if t.last_sent_at is None:
                        due_ids.append(t.id)  # первая отправка сразу
                        continue
                    next_time = t.last_sent_at + timedelta(minutes=t.interval_minutes)
                    if next_time <= now:
                        due_ids.append(t.id)

            if due_ids:
                await asyncio.gather(*(_guarded_run(tid) for tid in due_ids))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduler iteration failed")
        await asyncio.sleep(30)


async def _guarded_run(task_id: int) -> None:
    """Одна рассылка в общей очереди: ловим всё, чтобы одна не уронила остальные."""
    async with _send_sem:
        try:
            await run_task(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("task %s crashed", task_id)


async def send_now(task_id: int) -> str:
    """Ручная немедленная отправка (кнопка «Отправить сейчас»). Возвращает статус."""
    return await run_task(task_id)

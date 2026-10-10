"""Рассылки: мастер создания (пост -> аккаунты -> чаты -> ссылки -> интервал),
список, пауза/запуск, отправка сейчас, удаление."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..bot import admin_only, send_now
from ..database import Account, Post, SessionLocal, Task
from ..keyboards import (
    account_pick_kb,
    back_menu,
    chat_input_kb,
    chats_kb,
    confirm_kb,
    interval_kb,
    skip_link_kb,
    task_list_kb,
    task_show_kb,
)
from ..tgsend.manager import manager
from ..utils import edit_or_send

router = Router()
router.message.filter(admin_only)
router.callback_query.filter(admin_only)

CHATS_PER_PAGE = 8


class TaskSG(StatesGroup):
    chat = State()      # ручной ввод чата
    link = State()      # ссылка для конкретного аккаунта
    iv_custom = State() # свой интервал


# ---------------------------------------------------------------- список

@router.callback_query(F.data == "menu:tasks")
async def tasks_menu(cb: CallbackQuery) -> None:
    async with SessionLocal() as s:
        tasks = (
            (
                await s.execute(
                    select(Task)
                    .options(selectinload(Task.post), selectinload(Task.account))
                    .order_by(Task.id)
                )
            )
            .scalars()
            .all()
        )
    if tasks:
        text = (
            f"📨 <b>Рассылки</b> ({len(tasks)})\n\n"
            "🟢 — работает, 🔴 — на паузе\n"
            "Интервал отправки задан для каждой рассылки отдельно."
        )
    else:
        text = "📨 <b>Рассылки</b>\n\nПока пусто. Создай первую!"
    await edit_or_send(cb.message, text, task_list_kb(tasks))
    await cb.answer()


@router.callback_query(F.data.startswith("task:show:"))
async def task_show(cb: CallbackQuery, note: str | None = None, answer: bool = True) -> None:
    task_id = int(cb.data.split(":")[2])
    async with SessionLocal() as s:
        t = (
            await s.execute(
                select(Task)
                .where(Task.id == task_id)
                .options(selectinload(Task.post), selectinload(Task.account))
            )
        ).scalar_one_or_none()
    if not t:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    link = t.link or "<i>без ссылки</i>"
    err = f"\n⚠️ Последняя ошибка: <code>{t.last_error}</code>" if t.last_error else ""
    last = t.last_sent_at.strftime("%d.%m %H:%M") if t.last_sent_at else "ещё не отправлялась"
    head = f"{note}\n\n" if note else ""
    text = (
        f"{head}📨 <b>Рассылка #{t.id}</b>\n\n"
        f"📄 Пост: <b>{t.post.title}</b>\n"
        f"👤 Аккаунт: <b>{t.account.name}</b> ({'🟢' if t.account.enabled and t.enabled else '🔴'})\n"
        f"💬 Чат: <code>{t.chat_ref}</code>\n"
        f"🔗 Ссылка: {link}\n"
        f"⏱ Интервал: каждые <b>{_fmt_iv(t.interval_minutes)}</b>\n"
        f"📤 Отправлено: {t.sent_count}\n"
        f"🕒 Последняя отправка: {last}{err}"
    )
    await edit_or_send(cb.message, text, task_show_kb(t))
    if answer:
        await cb.answer()


def _fmt_iv(minutes: int) -> str:
    if minutes % 1440 == 0 and minutes >= 1440:
        return f"{minutes // 1440} дн."
    if minutes % 60 == 0 and minutes >= 60:
        return f"{minutes // 60} ч."
    return f"{minutes} мин"


# ---------------------------------------------------------------- вкл/выкл/сейчас/удаление

@router.callback_query(F.data.startswith("task:on:"))
async def task_on(cb: CallbackQuery) -> None:
    task_id = int(cb.data.split(":")[2])
    async with SessionLocal() as s:
        t = await s.get(Task, task_id)
        if t:
            t.enabled = True
            await s.commit()
    await cb.answer("▶️ Рассылка запущена")
    await task_show(cb, note="▶️ Рассылка запущена", answer=False)


@router.callback_query(F.data.startswith("task:off:"))
async def task_off(cb: CallbackQuery) -> None:
    task_id = int(cb.data.split(":")[2])
    async with SessionLocal() as s:
        t = await s.get(Task, task_id)
        if t:
            t.enabled = False
            await s.commit()
    await cb.answer("⏸ Рассылка на паузе")
    await task_show(cb, note="⏸ Рассылка на паузе", answer=False)


@router.callback_query(F.data.startswith("task:now:"))
async def task_now(cb: CallbackQuery) -> None:
    task_id = int(cb.data.split(":")[2])
    await cb.answer("🚀 Отправляю...")
    status = await send_now(task_id)  # вернёт и успех, и причину отказа
    await task_show(cb, note=status, answer=False)


@router.callback_query(F.data.startswith("task:del:"))
async def task_del_ask(cb: CallbackQuery) -> None:
    task_id = int(cb.data.split(":")[2])
    await edit_or_send(cb.message, 
        "⚠️ Удалить эту рассылку?",
        confirm_kb(f"task:delc:{task_id}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("task:delc:"))
async def task_del_confirm(cb: CallbackQuery) -> None:
    _, _, task_id, action = cb.data.split(":")
    if action != "yes":
        await cb.answer("Отменено")
        return await tasks_menu(cb)
    async with SessionLocal() as s:
        t = await s.get(Task, int(task_id))
        if t:
            await s.delete(t)
            await s.commit()
    await cb.answer("🗑 Рассылка удалена")
    await tasks_menu(cb)


# ---------------------------------------------------------------- мастер создания

@router.callback_query(F.data == "task:new")
async def task_new(cb: CallbackQuery, state: FSMContext) -> None:
    async with SessionLocal() as s:
        posts = (await s.execute(select(Post).order_by(Post.id.desc()))).scalars().all()
    if not posts:
        await cb.answer("Сначала создай пост в разделе «Посты»", show_alert=True)
        return
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    kb = InlineKeyboardBuilder()
    for p in posts:
        kb.row(InlineKeyboardButton(text=f"📄 {p.title}", callback_data=f"task:post:{p.id}"))
    kb.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="task:cancel", style="danger"))
    await state.set_data({})
    await edit_or_send(cb.message, "📨 Новая рассылка\n\nШаг 1/4 — выбери <b>пост</b>:", kb.as_markup())
    await cb.answer()


@router.callback_query(F.data == "task:cancel")
async def task_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("Отменено")
    await tasks_menu(cb)


@router.callback_query(F.data.startswith("task:post:"))
async def task_pick_post(cb: CallbackQuery, state: FSMContext) -> None:
    post_id = int(cb.data.split(":")[2])
    await state.update_data(post_id=post_id, accounts=[], targets={}, idx=0)
    async with SessionLocal() as s:
        accounts = (
            (await s.execute(select(Account).where(Account.enabled.is_(True)).order_by(Account.id)))
            .scalars()
            .all()
        )
    if not accounts:
        await cb.answer("Нет включённых аккаунтов — добавь их в «Аккаунты»", show_alert=True)
        return
    await edit_or_send(cb.message, 
        "Шаг 2/4 — выбери <b>аккаунты</b>, которые будут отправлять пост "
        "(можно несколько):",
        account_pick_kb(accounts, set()),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("task:acc:"))
async def task_pick_acc(cb: CallbackQuery, state: FSMContext) -> None:
    arg = cb.data.split(":")[2]
    data = await state.get_data()
    selected = set(data.get("accounts", []))

    if arg == "next":
        if not selected:
            return await cb.answer("Выбери хотя бы один аккаунт", show_alert=True)
        await state.update_data(accounts=sorted(selected), idx=0)
        await _ask_chat(cb.message, state, 0)
        return await cb.answer()
    acc_id = int(arg)
    if acc_id in selected:
        selected.discard(acc_id)
    else:
        selected.add(acc_id)
    await state.update_data(accounts=sorted(selected))

    async with SessionLocal() as s:
        accounts = (
            (await s.execute(select(Account).where(Account.enabled.is_(True)).order_by(Account.id)))
            .scalars()
            .all()
        )
    await edit_or_send(cb.message, 
        "Шаг 2/4 — выбери <b>аккаунты</b> (можно несколько):",
        account_pick_kb(accounts, selected),
    )
    await cb.answer()


async def _ask_chat(target: Message, state: FSMContext, idx: int) -> None:
    """Шаг 3: сразу просим прислать ссылку на чат для аккаунта #idx.

    Список диалогов не грузим заранее — он тянет сеть и тормозит шаг.
    Он открывается по кнопке «Выбрать из диалогов» (см. task_chat_list).
    """
    data = await state.get_data()
    acc_ids: list[int] = data["accounts"]
    acc_id = acc_ids[idx]
    async with SessionLocal() as s:
        acc = await s.get(Account, acc_id)

    await state.set_state(TaskSG.chat)
    await edit_or_send(
        target,
        f"Шаг 3/4 — чат для аккаунта <b>{acc.name}</b> "
        f"({idx + 1}/{len(acc_ids)}):\n\n"
        "📩 Пришли <b>ссылку на чат</b>:\n"
        "• публичный — <code>@username</code> или <code>t.me/name</code>\n"
        "• приватный — <code>t.me/+...</code> (бот вступит сам)\n"
        "• либо числовой id",
        chat_input_kb(),
    )


async def _fetch_dialogs(acc: Account) -> list[tuple[str, str, int]]:
    """Список диалогов аккаунта: (id_str, title, count). Пусто, если не подключён."""
    try:
        client = manager.get(acc.id, acc.session_name)
        await client.ensure_connected()
        if not await client.is_authorized():
            return []
        out: list[tuple[str, str, int]] = []
        async for d in client.client.iter_dialogs(limit=50):
            title = d.name or "(без названия)"
            out.append((str(d.id), title[:40], d.message_count or 0))
        return out
    except Exception:  # noqa: BLE001
        return []


@router.callback_query(F.data == "task:chat:list")
async def task_chat_list(cb: CallbackQuery, state: FSMContext) -> None:
    """Диалоги аккаунта — только по явной кнопке, чтобы шаг 3 открывался мгновенно."""
    data = await state.get_data()
    idx = data.get("idx", 0)
    acc_ids = data.get("accounts", [])
    if not acc_ids:
        return await cb.answer("Мастер рассылки устарел — начни заново", show_alert=True)
    async with SessionLocal() as s:
        acc = await s.get(Account, acc_ids[idx])

    await cb.answer("Загружаю диалоги…")
    dialogs = await _fetch_dialogs(acc)
    if not dialogs:
        return await edit_or_send(
            cb.message,
            "😕 Аккаунт не прислал диалоги (не подключён или нет прав). "
            "Просто пришли ссылку на чат вручную:",
            chat_input_kb(),
        )
    await state.update_data(chats=dialogs, chats_page=0)
    total_pages = max(1, (len(dialogs) + CHATS_PER_PAGE - 1) // CHATS_PER_PAGE)
    await edit_or_send(
        cb.message,
        f"Диалоги аккаунта <b>{acc.name}</b> — выбери чат "
        f"({idx + 1}/{len(acc_ids)}):",
        chats_kb(dialogs[:CHATS_PER_PAGE], 0, total_pages),
    )


@router.callback_query(F.data.startswith("task:chats:"))
async def task_chats_page(cb: CallbackQuery, state: FSMContext) -> None:
    arg = cb.data.split(":")[2]
    if arg == "noop":
        return await cb.answer()
    page = int(arg)
    data = await state.get_data()
    chats = data.get("chats", [])
    total_pages = max(1, (len(chats) + CHATS_PER_PAGE - 1) // CHATS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    await state.update_data(chats_page=page)
    idx = data.get("idx", 0)
    acc_ids = data.get("accounts", [])
    async with SessionLocal() as s:
        acc = await s.get(Account, acc_ids[idx])
    page_items = chats[page * CHATS_PER_PAGE:(page + 1) * CHATS_PER_PAGE]
    await edit_or_send(cb.message, 
        f"Шаг 3/4 — чат для аккаунта <b>{acc.name}</b> "
        f"({idx + 1}/{len(acc_ids)}):\n\nВыбери из диалогов или введи вручную.",
        chats_kb(page_items, page, total_pages),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("task:chat:"))
async def task_pick_chat(cb: CallbackQuery, state: FSMContext) -> None:
    arg = cb.data.split(":")[2]
    if arg == "manual":
        await state.set_state(TaskSG.chat)
        await edit_or_send(
            cb.message,
            "✍️ Пришли ссылку на чат: <code>@username</code>, <code>t.me/...</code> "
            "(в т.ч. приватную <code>t.me/+...</code>) или числовой id:",
            chat_input_kb(),
        )
        return await cb.answer()
    await _store_chat(cb.message, state, arg)
    await cb.answer()


@router.message(TaskSG.chat)
async def task_manual_chat(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    chat = _normalize_chat(raw)
    if not chat:
        return await message.answer(
            "❌ Не похоже на @username, t.me-ссылку или id.\n"
            "Пришли, например: <code>@mychannel</code>, "
            "<code>t.me/+AbCdEf...</code> или <code>-1001234567890</code>."
        )
    await state.set_state(None)
    await _store_chat(message, state, chat)


def _normalize_chat(raw: str) -> str | None:
    """Приводит введённую пользователем ссылку/имя к виду, который понимает отправка.

    Возвращает один из вариантов:
      - "@username"            публичный канал/чат
      - "invite:<hash>"        приватная инвайт-ссылка t.me/+... (нужен join)
      - "-100123..."           числовой id (канал/супергруппа)
    """
    raw = raw.strip().split("?")[0].split("#")[0]

    # приватная инвайт-ссылка: t.me/+XXXX, t.me/joinchat/XXXX, t.me/c/ID/...
    low = raw.lower()
    if "t.me/" in low:
        tail = raw.split("t.me/", 1)[1].strip("/")
        first = tail.split("/", 1)[0]
        if first.startswith("+"):
            return ("invite:" + first[1:]) if len(first) > 1 else None
        if first.lower() == "joinchat":
            rest = tail.split("/", 1)[1] if "/" in tail else ""
            return ("invite:" + rest.split("/")[0]) if rest else None
        if first.lower() == "c":
            # приватный канал: t.me/c/<id>/<msg> -> id канала
            parts = tail.split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                return "-100" + parts[1]
        # публичная ссылка: t.me/username (возможно с /123 в конце)
        if first:
            return "@" + first

    if raw.startswith("@") and len(raw) > 1:
        return raw
    if raw.lstrip("-").isdigit():
        return raw
    if raw.isidentifier():  # просто имя публичного канала
        return "@" + raw
    return None


def _pretty_chat(chat_ref: str) -> str:
    """Человекочитаемый вид чата для показа в панели."""
    if chat_ref.startswith("invite:"):
        return "t.me/+" + chat_ref.split(":", 1)[1]
    return chat_ref


async def _store_chat(target: Message, state: FSMContext, chat_ref: str) -> None:
    data = await state.get_data()
    idx = data.get("idx", 0)
    acc_ids = data.get("accounts", [])
    acc_id = acc_ids[idx]
    targets = data.get("targets", {})
    targets[str(acc_id)] = {"chat": chat_ref, "link": None}
    await state.update_data(targets=targets)

    async with SessionLocal() as s:
        acc = await s.get(Account, acc_id)
    await state.set_state(TaskSG.link)
    await edit_or_send(
        target,
        f"Шаг 4/4 — <b>ссылка</b> для аккаунта <b>{acc.name}</b>\n"
        f"(чать: <code>{_pretty_chat(chat_ref)}</code>)\n\n"
        "Пришли свою реферальную/персональную ссылку — она подставится вместо "
        "<code>{link}</code> в тексте поста.\n"
        "Или нажми «Пропустить».",
        skip_link_kb(),
    )


@router.callback_query(TaskSG.link, F.data == "task:link:skip")
async def task_link_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await _next_account_or_finish(cb.message, state, None)
    await cb.answer()


@router.message(TaskSG.link)
async def task_link_input(message: Message, state: FSMContext) -> None:
    link = (message.text or "").strip()[:512]
    await state.set_state(None)
    await _next_account_or_finish(message, state, link)


async def _next_account_or_finish(target: Message, state: FSMContext, link: str | None) -> None:
    data = await state.get_data()
    idx = data.get("idx", 0)
    acc_ids = data.get("accounts", [])
    targets = data.get("targets", {})
    acc_key = str(acc_ids[idx])
    if link is not None:
        targets[acc_key]["link"] = link
    await state.update_data(targets=targets)

    if idx + 1 < len(acc_ids):
        await state.update_data(idx=idx + 1)
        await state.set_state(None)
        return await _ask_chat(target, state, idx + 1)

    # все аккаунты настроены -> интервал
    await state.set_state(None)
    await edit_or_send(
        target,
        "⏱ Шаг 4/4 — <b>интервал отправки</b> (как часто пост уходит в каждый чат):",
        interval_kb(),
    )


@router.callback_query(F.data.startswith("task:iv:"))
async def task_interval(cb: CallbackQuery, state: FSMContext) -> None:
    arg = cb.data.split(":")[2]
    if arg == "custom":
        await state.set_state(TaskSG.iv_custom)
        await edit_or_send(
            cb.message,
            "✏️ Пришли интервал <b>в минутах</b> (например, <code>45</code>):",
            back_menu(),
        )
        return await cb.answer()
    await _create_tasks(cb.message, state, int(arg))
    await cb.answer()


@router.message(TaskSG.iv_custom)
async def task_interval_custom(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        return await message.answer("❌ Нужно целое число минут (минимум 1).")
    await state.set_state(None)
    await _create_tasks(message, state, int(raw))


async def _create_tasks(target: Message, state: FSMContext, interval: int) -> None:
    data = await state.get_data()
    post_id = data["post_id"]
    targets: dict[str, dict] = data["targets"]
    created: list[int] = []
    async with SessionLocal() as s:
        for acc_key, info in targets.items():
            t = Task(
                post_id=post_id,
                account_id=int(acc_key),
                chat_ref=info["chat"],
                link=info.get("link"),
                interval_minutes=interval,
                enabled=True,
            )
            s.add(t)
            await s.flush()
            created.append(t.id)
        await s.commit()
    await state.clear()
    await edit_or_send(
        target,
        f"✅ Создано рассылок: <b>{len(created)}</b>\n"
        f"⏱ Интервал: каждые <b>{_fmt_iv(interval)}</b>\n\n"
        "Первая отправка произойдёт в ближайшие 30 секунд.",
        back_menu(),
    )

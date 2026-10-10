"""Аккаунты: список, подключение (номер -> код -> 2FA), вкл/выкл, удаление."""
from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from ..bot import admin_only
from ..config import SESSIONS_DIR, config
from ..database import Account, SessionLocal
from ..utils import edit_or_send
from ..keyboards import account_list_kb, account_show_kb, back_menu, confirm_kb
from ..tgsend.manager import manager

router = Router()
router.message.filter(admin_only)
router.callback_query.filter(admin_only)


class LoginSG(StatesGroup):
    phone = State()
    code = State()
    password = State()


# живые клиенты durante логина: user_id -> TelegramClient
_pending: dict[int, TelegramClient] = {}


# ---------------------------------------------------------------- список

@router.callback_query(F.data == "menu:accounts")
async def accounts_menu(cb: CallbackQuery) -> None:
    async with SessionLocal() as s:
        accounts = (await s.execute(select(Account).order_by(Account.id))).scalars().all()
    if accounts:
        text = f"👤 <b>Аккаунты</b> ({len(accounts)})\n\n🟢 — отправляет\n🔴 — выключен"
    else:
        text = "👤 <b>Аккаунты</b>\n\nПока нет аккаунтов. Нажми «Подключить»."
    await edit_or_send(cb.message, text, account_list_kb(accounts))
    await cb.answer()


@router.callback_query(F.data.startswith("acc:show:"))
async def acc_show(cb: CallbackQuery) -> None:
    acc_id = int(cb.data.split(":")[2])
    async with SessionLocal() as s:
        acc = await s.get(Account, acc_id)
    if not acc:
        return await cb.answer("Аккаунт не найден", show_alert=True)
    status = "🟢 включён" if acc.enabled else "🔴 выключен"
    text = (
        f"👤 <b>{acc.name}</b>\n"
        f"📞 {acc.phone}\n"
        f"Статус: {status}\n\n"
        "Выключенный аккаунт не удаляется — он просто не участвует в рассылках."
    )
    await edit_or_send(cb.message, text, account_show_kb(acc))
    await cb.answer()


# ---------------------------------------------------------------- вкл/выкл

@router.callback_query(F.data.startswith("acc:on:"))
async def acc_on(cb: CallbackQuery) -> None:
    acc_id = int(cb.data.split(":")[2])
    async with SessionLocal() as s:
        acc = await s.get(Account, acc_id)
        if acc:
            acc.enabled = True
            await s.commit()
    await cb.answer("🟢 Аккаунт включён")
    await acc_show(cb)


@router.callback_query(F.data.startswith("acc:off:"))
async def acc_off(cb: CallbackQuery) -> None:
    acc_id = int(cb.data.split(":")[2])
    async with SessionLocal() as s:
        acc = await s.get(Account, acc_id)
        if acc:
            acc.enabled = False
            await s.commit()
    await cb.answer("🔴 Аккаунт выключен")
    await acc_show(cb)


# ---------------------------------------------------------------- удаление

@router.callback_query(F.data.startswith("acc:del:"))
async def acc_del_ask(cb: CallbackQuery) -> None:
    acc_id = int(cb.data.split(":")[2])
    await edit_or_send(cb.message, 
        "⚠️ Удалить аккаунт и его рассылки? Это действие необратимо.",
        confirm_kb(f"acc:delc:{acc_id}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("acc:delc:"))
async def acc_del_confirm(cb: CallbackQuery) -> None:
    _, _, acc_id, action = cb.data.split(":")
    if action != "yes":
        await cb.answer("Отменено")
        return await accounts_menu(cb)
    async with SessionLocal() as s:
        acc = await s.get(Account, int(acc_id))
        if acc:
            manager.drop(acc.id)
            await s.delete(acc)
            await s.commit()
    await cb.answer("🗑 Аккаунт удалён")
    await accounts_menu(cb)


# ---------------------------------------------------------------- логин

@router.callback_query(F.data == "acc:add")
async def acc_add(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LoginSG.phone)
    await edit_or_send(cb.message, 
        "📞 Приши номер телефона аккаунта <b>в международном формате</b>\n"
        "Например: <code>+79991234567</code>",
        back_menu(),
    )
    await cb.answer()


@router.message(LoginSG.phone)
async def acc_phone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        return await message.answer("❌ Неверный формат. Пример: <code>+79991234567</code>")

    session_name = f"acc_{uuid.uuid4().hex[:12]}"
    client = TelegramClient(str(SESSIONS_DIR / session_name), config.api_id, config.api_hash)
    await client.connect()
    try:
        await client.send_code_request(phone)
    except Exception as exc:  # noqa: BLE001
        await client.disconnect()
        return await message.answer(f"❌ Telegram отклонил номер: {exc}")

    _pending[message.from_user.id] = client
    await state.update_data(phone=phone, session_name=session_name)
    await state.set_state(LoginSG.code)
    await message.answer("✉️ Пришли код из Telegram (5 цифр).")


@router.message(LoginSG.code)
async def acc_code(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = (message.text or "").strip().replace(" ", "")
    client = _pending.get(message.from_user.id)
    if client is None:
        await state.clear()
        return await message.answer("❌ Сессия входа истекла, начни заново /start")
    try:
        await client.sign_in(data["phone"], code)
    except SessionPasswordNeededError:
        await state.set_state(LoginSG.password)
        return await message.answer("🔒 Аккаунт защищён паролем 2FA. Приши пароль.")
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        return await message.answer("❌ Код неверный или устарел. Приши код ещё раз.")
    except Exception as exc:  # noqa: BLE001
        return await message.answer(f"❌ Ошибка входа: {exc}")
    await _finish_login(message, state, client)


@router.message(LoginSG.password)
async def acc_password(message: Message, state: FSMContext) -> None:
    client = _pending.get(message.from_user.id)
    if client is None:
        await state.clear()
        return await message.answer("❌ Сессия входа истекла, начни заново /start")
    try:
        await client.sign_in(password=(message.text or "").strip())
    except Exception as exc:  # noqa: BLE001
        return await message.answer(f"❌ Пароль неверный: {exc}")
    await _finish_login(message, state, client)


async def _finish_login(message: Message, state: FSMContext, client: TelegramClient) -> None:
    _pending.pop(message.from_user.id, None)
    data = await state.get_data()
    me = await client.get_me()
    name = " ".join(filter(None, [me.first_name, me.last_name])) or (me.username or "Аккаунт")
    async with SessionLocal() as s:
        exists = (
            await s.execute(select(Account).where(Account.phone == data["phone"]))
        ).scalar_one_or_none()
        if exists:
            await client.disconnect()
            await state.clear()
            return await message.answer(
                "⚠️ Такой номер уже подключён.", reply_markup=back_menu()
            )
        acc = Account(
            name=name[:64],
            phone=data["phone"],
            session_name=data["session_name"],
            enabled=True,
        )
        s.add(acc)
        await s.commit()
        await s.refresh(acc)
    # сразу кладём клиент в пул — отправка без задержек на подключение
    pool_client = manager.get(acc.id, acc.session_name)
    pool_client.client = client
    pool_client._connected = True
    await state.clear()
    await message.answer(
        f"✅ Аккаунт <b>{name}</b> подключён и активен.",
        reply_markup=back_menu(),
    )

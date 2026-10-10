"""Стартовое меню и раздел «Помощь»."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..bot import admin_only
from ..keyboards import back_menu, main_menu
from ..utils import edit_or_send

router = Router()
router.message.filter(admin_only)
router.callback_query.filter(admin_only)

HELP_TEXT = (
    "<b>ℹ️ Как пользоваться</b>\n\n"
    "1️⃣ <b>Аккаунты</b> — подключи Telegram-аккаунты (нужны API ID/API HASH с my.telegram.org).\n"
    "   Выключенный аккаунт ничего не отправляет, но остаётся в списке.\n\n"
    "2️⃣ <b>Посты</b> — создай сообщение. Поддерживается Markdown:\n"
    "   <code>**жирный**</code> <code>__курсив__</code> <code>~~зачёркнутый~~</code>\n"
    "   <code>`код`</code> <code>[текст](ссылка)</code> <code>```блок кода```</code>\n"
    "   Плюс фото — просто пришли его боту при создании поста.\n"
    "   В тексте можно поставить <code>{link}</code> — туда подставится ссылка аккаунта.\n\n"
    "3️⃣ <b>Рассылки</b> — свяжи пост + аккаунты + чаты. Для каждого аккаунта "
    "можно задать свою ссылку и свой интервал.\n\n"
    "⚡ Отправка идёт фоном, переключения между разделами мгновенные."
)


@router.message(Command("start", "menu"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Панель рассылок</b>\n\n"
        "Управляй аккаунтами, постами и автоотправкой прямо здесь.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_main(cb: CallbackQuery) -> None:
    await edit_or_send(
        cb.message,
        "👋 <b>Панель рассылок</b>\n\n"
        "Управляй аккаунтами, постами и автоотправкой прямо здесь.",
        main_menu(),
    )
    await cb.answer()


@router.callback_query(F.data == "menu:help")
async def menu_help(cb: CallbackQuery) -> None:
    await edit_or_send(cb.message, HELP_TEXT, back_menu())
    await cb.answer()

"""Общие помощники UI для панели."""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

# Причины, по которым edit_text не сработает и нужно прислать новое сообщение:
#  - фото-сообщение бота (редактировать можно только текст);
#  - сообщение, которое боту не принадлежит или оно слишком старое.
_EDIT_ERRORS = (
    "there is no text in the message to edit",
    "message can't be edited",
    "message to edit not found",
    "MESSAGE_ID_INVALID",
)
# «Ничего не изменилось» — это не ошибка, а признак успеха: молча выходим.
_NOT_MODIFIED = "message is not modified"


async def edit_or_send(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Заменить содержимое сообщения. Если править нельзя (фото, чужое или
    старое сообщение) — удаляем старое, если выйдет, и шлём новое.

    Своё сообщение бот определяет по ``from_user.is_bot``: чужие (например,
    введённый пользователем текст в мастере) править и удалять не может, поэтому
    сразу шлём новое, без лишних неудачных сетевых вызовов."""
    is_own = bool(message.from_user and message.from_user.is_bot)

    if is_own:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if _NOT_MODIFIED in str(exc).lower():
                return  # контент совпал с текущим — править нечего
            if not any(e.lower() in str(exc).lower() for e in _EDIT_ERRORS):
                raise
        # правка невозможна (фото/старое) -> пробуем удалить старое
        try:
            await message.delete()
        except TelegramBadRequest:
            pass  # уже удалённое или слишком старое — не критично

    # чужое сообщение или удалённое -> просто шлём новое
    await message.answer(text, reply_markup=reply_markup)

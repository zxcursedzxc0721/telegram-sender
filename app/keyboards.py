"""Клавиатуры панели. Цветные кнопки Bot API 10.x: style=success/danger/primary."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _btn(text: str, cb: str, style: str | None = None) -> InlineKeyboardButton:
    """Кнопка с необязательным цветом (danger=красная, success=зелёная, primary=синяя)."""
    if style:
        return InlineKeyboardButton(text=text, callback_data=cb, style=style)
    return InlineKeyboardButton(text=text, callback_data=cb)


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📨 Рассылки", "menu:tasks", "primary"))
    kb.row(_btn("📣 Посты", "menu:posts"), _btn("👤 Аккаунты", "menu:accounts"))
    kb.row(_btn("➕ Новая рассылка", "task:new", "success"))
    kb.row(_btn("ℹ️ Помощь", "menu:help"))
    return kb.as_markup()


def back_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🔙 В меню", "menu:main"))
    return kb.as_markup()


def chat_input_kb() -> InlineKeyboardMarkup:
    """Шаг 3 мастера: ссылка на чат вводится сразу, диалоги — по кнопке."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📋 Выбрать из диалогов", "task:chat:list"))
    kb.row(_btn("🔙 В меню", "menu:main"))
    return kb.as_markup()


def confirm_kb(prefix: str) -> InlineKeyboardMarkup:
    """Диалог подтверждения: зелёное «Да», красное «Нет»."""
    kb = InlineKeyboardBuilder()
    kb.row(
        _btn("✅ Да", f"{prefix}:yes", "success"),
        _btn("❌ Нет", f"{prefix}:no", "danger"),
    )
    return kb.as_markup()


def account_list_kb(accounts: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for a in accounts:
        # включён — зелёная точка, выключен — красная
        dot = "🟢" if a.enabled else "🔴"
        kb.row(_btn(f"{dot} {a.name} · {a.phone}", f"acc:show:{a.id}"))
    kb.row(_btn("➕ Подключить аккаунт", "acc:add", "success"))
    kb.row(_btn("🔙 В меню", "menu:main"))
    return kb.as_markup()


def account_show_kb(acc) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if acc.enabled:
        kb.row(_btn("🔴 Выключить отправку", f"acc:off:{acc.id}", "danger"))
    else:
        kb.row(_btn("🟢 Включить отправку", f"acc:on:{acc.id}", "success"))
    kb.row(_btn("🗑 Удалить аккаунт", f"acc:del:{acc.id}", "danger"))
    kb.row(_btn("🔙 К аккаунтам", "menu:accounts"))
    return kb.as_markup()


def post_list_kb(posts: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in posts:
        kb.row(_btn(f"📄 {p.title}", f"post:show:{p.id}"))
    kb.row(_btn("➕ Новый пост", "post:new", "success"))
    kb.row(_btn("🔙 В меню", "menu:main"))
    return kb.as_markup()


def post_show_kb(post) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✏️ Изменить текст", f"post:edit:{post.id}"))
    kb.row(_btn("🖼 Заменить фото", f"post:photo:{post.id}"))
    kb.row(_btn("🗑 Удалить пост", f"post:del:{post.id}", "danger"))
    kb.row(_btn("🔙 К постам", "menu:posts"))
    return kb.as_markup()


def task_list_kb(tasks: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in tasks:
        dot = "🟢" if t.enabled else "🔴"
        kb.row(_btn(f"{dot} #{t.id} {t.post.title} → {t.chat_ref}", f"task:show:{t.id}"))
    kb.row(_btn("➕ Новая рассылка", "task:new", "success"))
    kb.row(_btn("🔙 В меню", "menu:main"))
    return kb.as_markup()


def task_show_kb(task) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if task.enabled:
        kb.row(_btn("⏸ Пауза", f"task:off:{task.id}", "danger"))
    else:
        kb.row(_btn("▶️ Запустить", f"task:on:{task.id}", "success"))
    kb.row(_btn("🚀 Отправить сейчас", f"task:now:{task.id}", "primary"))
    kb.row(_btn("🗑 Удалить", f"task:del:{task.id}", "danger"))
    kb.row(_btn("🔙 К рассылкам", "menu:tasks"))
    return kb.as_markup()


def skip_link_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⏭ Пропустить ссылку", "task:link:skip"))
    kb.row(_btn("🔙 Отмена", "task:cancel", "danger"))
    return kb.as_markup()


def interval_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for minutes, label in ((5, "5 мин"), (15, "15 мин"), (30, "30 мин"), (60, "1 час"), (180, "3 часа"), (360, "6 часов"), (720, "12 часов"), (1440, "24 часа")):
        kb.row(_btn(label, f"task:iv:{minutes}"))
    kb.row(_btn("✏️ Своя цифра (в минутах)", "task:iv:custom"))
    kb.row(_btn("🔙 Отмена", "task:cancel", "danger"))
    return kb.as_markup()


def chats_kb(chats: list[tuple[str, str, int]], page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Список чатов аккаунта: (id_str, title, messages_count), постранично."""
    kb = InlineKeyboardBuilder()
    for cid, title, _ in chats:
        kb.row(_btn(f"💬 {title}", f"task:chat:{cid}"))
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"task:chats:{page-1}")
    nav.button(text=f"{page+1}/{total_pages}", callback_data="task:chats:noop")
    if page < total_pages - 1:
        nav.button(text="➡️", callback_data=f"task:chats:{page+1}")
    kb.attach(nav)
    kb.row(_btn("✍️ Ввести ссылку", "task:chat:manual"))
    kb.row(_btn("🔙 Отмена", "task:cancel", "danger"))
    return kb.as_markup()


def account_pick_kb(accounts: list, selected: set[int]) -> InlineKeyboardMarkup:
    """Мультивыбор аккаунтов для рассылки: выбранные — зелёные, нет — серые."""
    kb = InlineKeyboardBuilder()
    for a in accounts:
        if a.id in selected:
            kb.row(_btn(f"✅ {a.name}", f"task:acc:{a.id}", "success"))
        else:
            kb.row(_btn(f"☐ {a.name}", f"task:acc:{a.id}"))
    kb.row(_btn("➡️ Далее", "task:acc:next", "primary"))
    kb.row(_btn("🔙 Отмена", "task:cancel", "danger"))
    return kb.as_markup()

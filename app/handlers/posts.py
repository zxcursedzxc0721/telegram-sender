"""Посты: создание (текст с Markdown + фото), превью, правка, удаление."""
from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from ..bot import admin_only, bot
from ..config import MEDIA_DIR
from ..database import Post, SessionLocal
from ..keyboards import back_menu, confirm_kb, post_list_kb, post_show_kb
from ..tgsend.manager import md_to_html
from ..utils import edit_or_send

router = Router()
router.message.filter(admin_only)
router.callback_query.filter(admin_only)


class PostSG(StatesGroup):
    title = State()
    text = State()
    photo = State()
    edit_text = State()
    replace_photo = State()


# ---------------------------------------------------------------- список

@router.callback_query(F.data == "menu:posts")
async def posts_menu(cb: CallbackQuery) -> None:
    async with SessionLocal() as s:
        posts = (await s.execute(select(Post).order_by(Post.id.desc()))).scalars().all()
    if posts:
        text = f"📣 <b>Посты</b> ({len(posts)})\n\nВ тексте можно использовать <code>{'{link}'}</code> — вставит ссылку аккаунта."
    else:
        text = "📣 <b>Посты</b>\n\nПока нет постов. Создай первый!"
    await edit_or_send(cb.message, text, post_list_kb(posts))
    await cb.answer()


@router.callback_query(F.data.startswith("post:show:"))
async def post_show(cb: CallbackQuery) -> None:
    post_id = int(cb.data.split(":")[2])
    async with SessionLocal() as s:
        post = await s.get(Post, post_id)
    if not post:
        return await cb.answer("Пост не найден", show_alert=True)
    await _send_preview(cb.message, post, edit=True)
    await cb.answer()


async def _send_preview(message: Message, post: Post, edit: bool = False) -> None:
    """Показать пост так, как его увидят подписчики (Markdown -> HTML)."""
    body = md_to_html(post.text.replace("{link}", "https://example.com/ref"))
    header = f"👁 <b>Превью поста «{post.title}»</b>\n<i>(ссылка подставлена для примера)</i>\n\n"
    if post.photo_path and Path(post.photo_path).exists():
        from aiogram.types import FSInputFile

        await message.answer_photo(
            photo=FSInputFile(post.photo_path),
            caption=header + body,
            reply_markup=post_show_kb(post),
        )
        if edit:
            try:
                await message.delete()
            except Exception:  # noqa: BLE001
                pass
    else:
        text = header + (body or "<i>(пусто)</i>")
        if edit:
            await edit_or_send(message, text, post_show_kb(post))
        else:
            await message.answer(text, reply_markup=post_show_kb(post))


# ---------------------------------------------------------------- создание

@router.callback_query(F.data == "post:new")
async def post_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostSG.title)
    await edit_or_send(
        cb.message,
        "📝 Создание поста\n\nШаг 1/3 — <b>название</b> (для себя, коротко):",
        back_menu(),
    )
    await cb.answer()


@router.message(PostSG.title)
async def post_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()[:64]
    if not title:
        return await message.answer("❌ Название не может быть пустым.")
    await state.update_data(title=title)
    await state.set_state(PostSG.text)
    await message.answer(
        "Шаг 2/3 — <b>текст поста</b> с Markdown.\n\n"
        "Поддерживается: <code>**жирный**</code> <code>__курсив__</code> "
        "<code>~~зачёркнутый~~</code> <code>`код`</code> <code>[текст](ссылка)</code> "
        "<code>```блок```</code>\n\n"
        "Вставь <code>{link}</code> там, где нужна ссылка конкретного аккаунта.",
        reply_markup=back_menu(),
    )


@router.message(PostSG.text)
async def post_text(message: Message, state: FSMContext) -> None:
    text = message.text or message.caption or ""
    if not text.strip():
        return await message.answer("❌ Текст не может быть пустым.")
    await state.update_data(text=text)
    await state.set_state(PostSG.photo)
    await message.answer(
        "Шаг 3/3 — пришли <b>фото</b> для поста или нажми кнопку, чтобы пост был без фото.",
        reply_markup=_skip_photo_kb(),
    )


def _skip_photo_kb():
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Без фото", callback_data="post:photo:skip")
    return kb.as_markup()


@router.callback_query(PostSG.photo, F.data == "post:photo:skip")
async def post_photo_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(photo=None)
    await _finalize_post(cb.message, state, cb)
    await cb.answer()


@router.message(PostSG.photo, F.photo)
async def post_photo(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    data = await state.get_data()
    path = MEDIA_DIR / f"post_{abs(hash(data['title']))}_{photo.file_unique_id}.jpg"
    await bot.download(file, destination=str(path))
    await state.update_data(photo=str(path))
    await _finalize_post(message, state, None)


@router.message(PostSG.photo)
async def post_photo_wrong(message: Message) -> None:
    await message.answer("❌ Это не фото. Приши фото или нажми «⏭ Без фото».")


async def _finalize_post(target: Message, state: FSMContext, cb: CallbackQuery | None) -> None:
    data = await state.get_data()
    async with SessionLocal() as s:
        post = Post(title=data["title"], text=data["text"], photo_path=data.get("photo"))
        s.add(post)
        await s.commit()
        await s.refresh(post)
    await state.clear()
    await target.answer(f"✅ Пост <b>«{post.title}»</b> сохранён!", reply_markup=back_menu())
    await _send_preview(target, post, edit=False)


# ---------------------------------------------------------------- правка

@router.callback_query(F.data.startswith("post:edit:"))
async def post_edit(cb: CallbackQuery, state: FSMContext) -> None:
    post_id = int(cb.data.split(":")[2])
    await state.set_state(PostSG.edit_text)
    await state.update_data(edit_post_id=post_id)
    await edit_or_send(
        cb.message,
        "✏️ Приши <b>новый текст</b> поста (Markdown сохраняется):",
        back_menu(),
    )
    await cb.answer()


@router.message(PostSG.edit_text)
async def post_edit_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = message.text or ""
    async with SessionLocal() as s:
        post = await s.get(Post, data["edit_post_id"])
        if post:
            post.text = text
            await s.commit()
    await state.clear()
    await message.answer("✅ Текст обновлён!", reply_markup=back_menu())


@router.callback_query(F.data.regexp(r"^post:photo:\d+$"))
async def post_replace_photo_ask(cb: CallbackQuery, state: FSMContext) -> None:
    post_id = int(cb.data.split(":")[2])
    await state.set_state(PostSG.replace_photo)
    await state.update_data(replace_post_id=post_id)
    await edit_or_send(
        cb.message,
        "🖼 Приши <b>новое фото</b> для этого поста (или нажми «Без фото», чтобы убрать).",
        _skip_photo_kb(),
    )
    await cb.answer()


@router.message(PostSG.replace_photo, F.photo)
async def post_replace_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    path = MEDIA_DIR / f"post_{data['replace_post_id']}_{photo.file_unique_id}.jpg"
    await bot.download(file, destination=str(path))
    async with SessionLocal() as s:
        post = await s.get(Post, data["replace_post_id"])
        if post:
            post.photo_path = str(path)
            await s.commit()
    await state.clear()
    await message.answer("✅ Фото заменено!", reply_markup=back_menu())


@router.callback_query(PostSG.replace_photo, F.data == "post:photo:skip")
async def post_replace_photo_skip(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    async with SessionLocal() as s:
        post = await s.get(Post, data["replace_post_id"])
        if post:
            post.photo_path = None
            await s.commit()
    await state.clear()
    await edit_or_send(cb.message, "✅ Фото убрано из поста.", back_menu())
    await cb.answer()


@router.message(PostSG.replace_photo)
async def post_replace_photo_wrong(message: Message) -> None:
    await message.answer("❌ Это не фото. Приши фото или нажми «⏭ Без фото».")


@router.callback_query(F.data.startswith("post:del:"))
async def post_del_ask(cb: CallbackQuery) -> None:
    post_id = int(cb.data.split(":")[2])
    await edit_or_send(
        cb.message,
        "⚠️ Удалить пост и все рассылки с ним?",
        confirm_kb(f"post:delc:{post_id}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("post:delc:"))
async def post_del_confirm(cb: CallbackQuery) -> None:
    _, _, post_id, action = cb.data.split(":")
    if action != "yes":
        await cb.answer("Отменено")
        return await posts_menu(cb)
    async with SessionLocal() as s:
        post = await s.get(Post, int(post_id))
        if post:
            await s.delete(post)
            await s.commit()
    await cb.answer("🗑 Пост удалён")
    await posts_menu(cb)

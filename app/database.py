"""Асинхронная база данных (SQLite через SQLAlchemy 2.0 + aiosqlite)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, event, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import DATA_DIR

DB_PATH = DATA_DIR / "bot.db"
ENGINE = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False, pool_pre_ping=True)

# WAL — быстрая работа с частыми записями (таймеры рассылок)
@event.listens_for(ENGINE.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = async_sessionmaker(ENGINE, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Account(Base):
    """Подключённый Telegram-аккаунт (user-account, MTProto)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(32), unique=True)
    session_name: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    tasks: Mapped[list["Task"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Post(Base):
    """Пост: текст с Markdown + опциональное фото. {link} — плейсхолдер ссылки."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    photo_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    tasks: Mapped[list["Task"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class Task(Base):
    """Рассылка: отправка конкретного поста конкретным аккаунтом в конкретный чат
    по расписанию, со своей ссылкой и интервалом."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    chat_ref: Mapped[str] = mapped_column(String(255))       # @username или числовой id
    link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # своя ссылка аккаунта
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    post: Mapped["Post"] = relationship(back_populates="tasks")
    account: Mapped["Account"] = relationship(back_populates="tasks")


async def init_db() -> None:
    async with ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

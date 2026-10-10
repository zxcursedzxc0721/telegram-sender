"""Разбор URL прокси и подготовка параметров для aiogram и Telethon.

Поддерживаемые схемы в PROXY (.env):
    socks5://user:pass@127.0.0.1:1080
    socks4://127.0.0.1:1080
    http://user:pass@127.0.0.1:8080
"""
from __future__ import annotations

from urllib.parse import urlparse


def parse_proxy(url: str) -> tuple[str, str, int, str | None, str | None] | None:
    """Возвращает (scheme, host, port, user, password) или None, если прокси не задан."""
    if not url:
        return None
    u = urlparse(url)
    if not u.hostname or not u.port:
        raise ValueError(f"Некорректный PROXY: {url!r} (нужно scheme://host:port)")
    return (u.scheme.lower(), u.hostname, u.port, u.username, u.password)


def aiogram_session(proxy_url: str):
    """Сессия aiohttp для aiogram с прокси (или None — тогда обычная сессия)."""
    if not proxy_url:
        return None
    from aiogram.client.session.aiohttp import AiohttpSession

    return AiohttpSession(proxy=proxy_url)


def telethon_proxy(proxy_url: str):
    """Кортеж прокси для Telethon (или None). Требует PySocks для socks-схем."""
    parsed = parse_proxy(proxy_url)
    if parsed is None:
        return None
    scheme, host, port, user, password = parsed

    try:
        import socks
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Для прокси нужен PySocks: pip install pysocks"
        ) from exc

    if scheme.startswith("socks"):
        ptype = socks.SOCKS5 if scheme == "socks5" else socks.SOCKS4
    elif scheme == "http":
        ptype = socks.HTTP
    else:
        raise SystemExit(f"Неподдерживаемая схема прокси: {scheme} (нужно socks4/5 или http)")

    # rdns=True — DNS резолвим через прокси (важно для блокируемых доменов)
    return (ptype, host, port, True, user, password)

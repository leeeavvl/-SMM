"""Сбор просмотров постов Telegram-канала с публичной страницы (t.me/s/<channel>).

Публичная страница превью канала показывает количество просмотров у каждого
поста без авторизации — тот же метод, что используется при обычном открытии
ссылки t.me/s/<channel> в браузере. Работает только для публичных каналов.
"""
from __future__ import annotations

import re

import httpx

VIEWS_MULTIPLIERS = {"K": 1_000, "M": 1_000_000}


def _parse_views(text: str) -> int | None:
    text = text.strip().upper().replace(",", ".")
    match = re.match(r"^(\d+(?:\.\d+)?)([KM]?)$", text)
    if not match:
        return None
    value = float(match.group(1))
    mult = VIEWS_MULTIPLIERS.get(match.group(2), 1)
    return int(value * mult)


def fetch_channel_post_views(username: str) -> dict[int, int]:
    """Возвращает {message_id: views} для постов, видимых на публичной странице канала."""
    username = username.lstrip("@")
    url = f"https://t.me/s/{username}"
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Не удалось загрузить публичную страницу канала: {exc}") from exc

    html = resp.text
    results: dict[int, int] = {}

    # Каждый пост обёрнут в блок с data-post="username/<id>" и содержит
    # ссылку класса tgme_widget_message_views с текстом вида "1.2K".
    for block_match in re.finditer(
        r'data-post="' + re.escape(username) + r'/(\d+)"(.*?)(?=data-post="|\Z)',
        html,
        re.DOTALL,
    ):
        message_id = int(block_match.group(1))
        block = block_match.group(2)
        views_match = re.search(r'tgme_widget_message_views">([^<]+)<', block)
        if views_match:
            views = _parse_views(views_match.group(1))
            if views is not None:
                results[message_id] = views

    return results


def extract_message_id(url: str | None) -> int | None:
    if not url:
        return None
    match = re.search(r"/(\d+)/?$", url)
    return int(match.group(1)) if match else None


def extract_username(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"t\.me/([A-Za-z0-9_]+)/\d+", url)
    return match.group(1) if match else None

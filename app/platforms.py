"""Registry of supported social platforms and their (stub) publishers.

Each platform has a real publish integration to be wired up once API
credentials are available. Until then, `publish` simulates the call so the
rest of the app (queue, statuses, history) works end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import httpx


@dataclass(frozen=True)
class Platform:
    id: str
    name: str
    color: str
    credential_fields: tuple[str, ...]


PLATFORMS: list[Platform] = [
    Platform("telegram", "Telegram", "#26A5E4", ("bot_token", "chat_id")),
    Platform("instagram", "Instagram", "#E1306C", ("access_token", "account_id")),
    Platform("threads", "Threads", "#000000", ("access_token", "account_id")),
    Platform("vk", "VK", "#0077FF", ("access_token", "group_id")),
    Platform("dzen", "Дзен", "#FF3D00", ("api_key", "channel_id")),
    Platform("zakon_ru", "Zakon.ru", "#1F3A5F", ("login", "password")),
    Platform("youtube", "YouTube", "#FF0000", ("client_id", "client_secret", "refresh_token")),
    Platform("tiktok", "TikTok", "#010101", ("access_token", "account_id")),
    Platform("pinterest", "Pinterest", "#E60023", ("access_token", "board_id")),
    Platform("tzh", "Т—Ж", "#FFDD2D", ("login", "password")),
    Platform("facebook", "Facebook", "#1877F2", ("access_token", "page_id")),
    Platform("x", "X (Twitter)", "#000000", ("api_key", "api_secret", "access_token", "access_secret")),
    Platform("linkedin", "LinkedIn", "#0A66C2", ("access_token", "organization_id")),
    Platform("avito", "Авито", "#00AAFF", ("api_key", "user_id")),
]

PLATFORM_MAP: dict[str, Platform] = {p.id: p for p in PLATFORMS}


def publish_stub(platform_id: str, title: str, body: str, media_url: Optional[str], credentials: dict) -> dict:
    """Simulated publish call. Returns a result dict like a real adapter would.

    Replace this with real API calls per platform (see PLATFORM_MAP) once
    credentials/OAuth are configured for that platform.
    """
    if not credentials:
        return {"ok": False, "error": "Платформа не подключена (нет учётных данных)"}
    fake_url = f"https://example.com/{platform_id}/posts/{abs(hash(title + body)) % 100000}"
    return {"ok": True, "url": fake_url}


def publish_telegram(platform_id: str, title: str, body: str, media_url: Optional[str], credentials: dict) -> dict:
    """Real publish to Telegram via Bot API (bot must be admin of the channel)."""
    bot_token = credentials.get("bot_token")
    chat_id = credentials.get("chat_id")
    if not bot_token or not chat_id:
        return {"ok": False, "error": "Не заданы bot_token или chat_id"}

    text = f"{title}\n\n{body}" if title and title not in body else body

    try:
        if media_url:
            resp = httpx.post(
                f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                json={"chat_id": chat_id, "photo": media_url, "caption": text[:1024]},
                timeout=30,
            )
        else:
            resp = httpx.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=30,
            )
        data = resp.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Ошибка сети при обращении к Telegram: {exc}"}

    if not data.get("ok"):
        return {"ok": False, "error": data.get("description", "Неизвестная ошибка Telegram Bot API")}

    result = data["result"]
    message_id = result.get("message_id")
    username = (result.get("chat") or {}).get("username")
    if not username and isinstance(chat_id, str) and chat_id.startswith("@"):
        username = chat_id.lstrip("@")

    url = f"https://t.me/{username}/{message_id}" if username and message_id else None
    return {"ok": True, "url": url, "message_id": message_id, "channel_username": username}


PUBLISHERS: dict[str, Callable] = {p.id: publish_stub for p in PLATFORMS}
PUBLISHERS["telegram"] = publish_telegram

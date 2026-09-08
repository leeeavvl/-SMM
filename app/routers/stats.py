from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.database import db_cursor, row_to_dict
from app.schemas import ManualStatsUpdate
from app.telegram_stats import extract_message_id, extract_username, fetch_channel_post_views

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
def list_stats():
    with db_cursor() as cur:
        cur.execute(
            "SELECT cp.id AS content_platform_id, cp.platform_id, cp.url, cp.published_at, "
            "c.title, ps.views, ps.likes, ps.comments, ps.source, ps.updated_at "
            "FROM content_platforms cp "
            "JOIN content c ON c.id = cp.content_id "
            "LEFT JOIN post_stats ps ON ps.content_platform_id = cp.id "
            "WHERE cp.status = 'published' "
            "ORDER BY cp.published_at DESC"
        )
        rows = [row_to_dict(r) for r in cur.fetchall()]

    views_values = [r["views"] for r in rows if r["views"] is not None]
    avg_views = sum(views_values) / len(views_values) if views_values else None

    for r in rows:
        if r["views"] is None or avg_views is None:
            r["performance"] = None
        elif r["views"] >= avg_views * 1.2:
            r["performance"] = "above"
        elif r["views"] <= avg_views * 0.8:
            r["performance"] = "below"
        else:
            r["performance"] = "average"

    return {"items": rows, "avg_views": avg_views}


@router.post("/telegram/refresh")
def refresh_telegram_stats():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, url FROM content_platforms WHERE platform_id = 'telegram' "
            "AND status = 'published' AND url IS NOT NULL"
        )
        rows = cur.fetchall()

    if not rows:
        return {"updated": 0, "message": "Нет опубликованных постов Telegram для обновления"}

    username = None
    for r in rows:
        username = extract_username(r["url"])
        if username:
            break
    if not username:
        raise HTTPException(400, "Не удалось определить username канала из ссылок публикаций")

    try:
        views_by_message = fetch_channel_post_views(username)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    updated = 0
    with db_cursor() as cur:
        for r in rows:
            message_id = extract_message_id(r["url"])
            if message_id is None or message_id not in views_by_message:
                continue
            views = views_by_message[message_id]
            cur.execute(
                "INSERT INTO post_stats (content_platform_id, views, source, updated_at) "
                "VALUES (?, ?, 'telegram_auto', datetime('now')) "
                "ON CONFLICT(content_platform_id) DO UPDATE SET "
                "views = excluded.views, source = 'telegram_auto', updated_at = datetime('now')",
                (r["id"], views),
            )
            updated += 1

    return {"updated": updated}


@router.post("/{content_platform_id}/manual")
def update_manual_stats(content_platform_id: int, payload: ManualStatsUpdate):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM content_platforms WHERE id = ?", (content_platform_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Публикация не найдена")
        cur.execute(
            "INSERT INTO post_stats (content_platform_id, views, likes, comments, source, updated_at) "
            "VALUES (?, ?, ?, ?, 'manual', datetime('now')) "
            "ON CONFLICT(content_platform_id) DO UPDATE SET "
            "views = excluded.views, likes = excluded.likes, comments = excluded.comments, "
            "source = 'manual', updated_at = datetime('now')",
            (content_platform_id, payload.views, payload.likes, payload.comments),
        )
    return {"ok": True}

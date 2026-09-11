from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.database import db_cursor, row_to_dict
from app.platforms import PLATFORM_MAP
from app.scheduler import process_due_publications
from app.schemas import PublishRequest

router = APIRouter(prefix="/api", tags=["publish"])


@router.post("/content/{content_id}/publish")
def publish_content(content_id: int, payload: PublishRequest):
    unknown = [p for p in payload.platform_ids if p not in PLATFORM_MAP]
    if unknown:
        raise HTTPException(400, f"Неизвестные платформы: {', '.join(unknown)}")

    with db_cursor() as cur:
        cur.execute("SELECT * FROM content WHERE id = ?", (content_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Материал не найден")

        for platform_id in payload.platform_ids:
            cur.execute(
                "INSERT INTO content_platforms (content_id, platform_id, status, scheduled_at) "
                "VALUES (?, ?, 'queued', ?) "
                "ON CONFLICT(content_id, platform_id) DO UPDATE SET "
                "status = 'queued', scheduled_at = excluded.scheduled_at, error = NULL",
                (content_id, platform_id, payload.scheduled_at),
            )
        cur.execute(
            "UPDATE content SET status = ? WHERE id = ?",
            ("scheduled" if payload.scheduled_at else "publishing", content_id),
        )

    if not payload.scheduled_at:
        process_due_publications()

    with db_cursor() as cur:
        cur.execute("SELECT * FROM content_platforms WHERE content_id = ?", (content_id,))
        return [row_to_dict(r) for r in cur.fetchall()]


@router.get("/publish/queue")
def publish_queue():
    with db_cursor() as cur:
        cur.execute(
            "SELECT cp.*, c.title FROM content_platforms cp "
            "JOIN content c ON c.id = cp.content_id "
            "ORDER BY cp.id DESC"
        )
        return [row_to_dict(r) for r in cur.fetchall()]


@router.post("/publish/queue/{cp_id}/cancel")
def cancel_queued_publication(cp_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM content_platforms WHERE id = ?", (cp_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Запись не найдена")
        if row["status"] != "queued":
            raise HTTPException(400, "Отменить можно только публикацию, которая ещё в очереди")

        cur.execute(
            "UPDATE content_platforms SET status = 'canceled', scheduled_at = NULL, error = NULL WHERE id = ?",
            (cp_id,),
        )
        content_id = row["content_id"]
        cur.execute(
            "SELECT COUNT(*) AS n FROM content_platforms WHERE content_id = ? AND status = 'queued'",
            (content_id,),
        )
        # Если больше нет площадок в очереди — материал возвращается в черновики,
        # чтобы не висел в статусе «запланировано»/«публикуется» без активной задачи.
        if cur.fetchone()["n"] == 0:
            cur.execute("UPDATE content SET status = 'draft' WHERE id = ?", (content_id,))

        cur.execute("SELECT cp.*, c.title FROM content_platforms cp JOIN content c ON c.id = cp.content_id WHERE cp.id = ?", (cp_id,))
        return row_to_dict(cur.fetchone())

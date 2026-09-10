from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.auto_learning import run_auto_learning
from app.database import db_cursor, load_json
from app.platforms import PUBLISHERS

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _do_publish(cur, cp_row, content_row) -> None:
    platform_id = cp_row["platform_id"]
    cur.execute("SELECT connected, credentials FROM platforms WHERE id = ?", (platform_id,))
    prow = cur.fetchone()
    credentials = load_json(prow["credentials"]) if prow and prow["connected"] else {}

    publisher = PUBLISHERS.get(platform_id)
    result = publisher(
        platform_id,
        content_row["title"],
        content_row["body"],
        content_row["media_url"],
        credentials,
    )

    if result.get("ok"):
        cur.execute(
            "UPDATE content_platforms SET status = 'published', published_at = datetime('now'), "
            "url = ?, error = NULL WHERE id = ?",
            (result.get("url"), cp_row["id"]),
        )
    else:
        cur.execute(
            "UPDATE content_platforms SET status = 'failed', error = ? WHERE id = ?",
            (result.get("error", "Неизвестная ошибка"), cp_row["id"]),
        )


def process_due_publications() -> None:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM content_platforms WHERE status = 'queued' "
            "AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))"
        )
        due = cur.fetchall()
        for cp_row in due:
            cur.execute("SELECT * FROM content WHERE id = ?", (cp_row["content_id"],))
            content_row = cur.fetchone()
            if content_row:
                _do_publish(cur, cp_row, content_row)
                cur.execute(
                    "UPDATE content SET status = 'published' WHERE id = ?",
                    (content_row["id"],),
                )


def _run_auto_learning_job() -> None:
    try:
        result = run_auto_learning()
        if result.get("analyzed"):
            logger.info(
                "auto_learning: проанализировано постов: %s, идей добавлено в базу: %s",
                result["analyzed"], result["ideas_added"],
            )
    except Exception:
        # Фоновая задача не должна ронять планировщик целиком — просто
        # пробуем ещё раз на следующем прогоне.
        logger.exception("auto_learning: прогон завершился с ошибкой")


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(process_due_publications, "interval", seconds=15, id="process_due", replace_existing=True)
        scheduler.add_job(_run_auto_learning_job, "interval", minutes=30, id="auto_learning", replace_existing=True)
        scheduler.start()

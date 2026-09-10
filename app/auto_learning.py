"""Автообучение: приложение само анализирует опубликованные посты по мере
поступления статистики и сохраняет выводы в «Базу» — без ручных кликов
«Анализировать» / «Добавить идеи в базу».

Работает как периодическая фоновая задача (см. app.scheduler): каждый пост,
у которого есть хоть какая-то статистика (просмотры/лайки/комментарии) и
либо ещё не было анализа, либо статистика обновилась после последнего
анализа — переанализируется, а идеи по улучшению сразу попадают в
knowledge_base (source='idea'), откуда уже подмешиваются во все будущие
генерации (см. app.ai.get_knowledge_context).
"""
from __future__ import annotations

import json
import logging

from app.ai import AIConfigError, AIGenerationError, analyze_post
from app.database import db_cursor, get_setting, row_to_dict
from app.platforms import PLATFORM_MAP

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_RUN = 5


def is_enabled() -> bool:
    return (get_setting("auto_learning_enabled") or "1") == "1"


def _find_candidates(cur) -> list[dict]:
    cur.execute(
        """
        SELECT c.id AS content_id, c.title, c.body, cp.platform_id,
               MAX(ps.updated_at) AS stats_updated_at, ca.created_at AS analyzed_at
        FROM content c
        JOIN content_platforms cp ON cp.content_id = c.id
        JOIN post_stats ps ON ps.content_platform_id = cp.id
        LEFT JOIN content_analysis ca ON ca.content_id = c.id
        WHERE ps.views IS NOT NULL OR ps.likes IS NOT NULL OR ps.comments IS NOT NULL
        GROUP BY c.id
        HAVING analyzed_at IS NULL OR stats_updated_at > analyzed_at
        ORDER BY stats_updated_at DESC
        LIMIT ?
        """,
        (MAX_ITEMS_PER_RUN,),
    )
    return [row_to_dict(r) for r in cur.fetchall()]


def _get_stats_for_content(cur, content_id: int) -> list[dict]:
    cur.execute(
        "SELECT cp.platform_id, ps.views, ps.likes, ps.comments "
        "FROM content_platforms cp JOIN post_stats ps ON ps.content_platform_id = cp.id "
        "WHERE cp.content_id = ?",
        (content_id,),
    )
    return [
        {
            "platform_id": r["platform_id"],
            "platform_name": PLATFORM_MAP[r["platform_id"]].name if r["platform_id"] in PLATFORM_MAP else r["platform_id"],
            "views": r["views"],
            "likes": r["likes"],
            "comments": r["comments"],
        }
        for r in cur.fetchall()
    ]


def run_auto_learning() -> dict:
    """Возвращает сводку прогона — удобно и для планировщика, и для ручного
    запуска/проверки из настроек."""
    if not is_enabled():
        return {"skipped": "disabled"}

    with db_cursor() as cur:
        candidates = _find_candidates(cur)

    analyzed = 0
    ideas_added = 0
    errors: list[str] = []

    for item in candidates:
        with db_cursor() as cur:
            stats = _get_stats_for_content(cur, item["content_id"])
        try:
            result = analyze_post(item["title"], item["body"], item["platform_id"], stats)
        except (AIConfigError, AIGenerationError) as exc:
            # Провайдер не настроен/недоступен — тихо пропускаем, попробуем
            # снова на следующем прогоне (не спамим ошибками фоновой задачи).
            errors.append(f"{item['title']}: {exc}")
            logger.info("auto_learning: анализ поста %s не удался: %s", item["content_id"], exc)
            continue

        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO content_analysis (content_id, score, feedback, suggestions) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(content_id) DO UPDATE SET "
                "score = excluded.score, feedback = excluded.feedback, "
                "suggestions = excluded.suggestions, created_at = datetime('now')",
                (item["content_id"], result["score"], result["feedback"], json.dumps(result["suggestions"], ensure_ascii=False)),
            )
            for suggestion in result["suggestions"]:
                cur.execute(
                    "INSERT INTO knowledge_base (title, content, source) VALUES (?, ?, 'idea')",
                    (f"Автоанализ «{item['title']}»", suggestion),
                )
                ideas_added += 1
        analyzed += 1

    return {"analyzed": analyzed, "ideas_added": ideas_added, "errors": errors}

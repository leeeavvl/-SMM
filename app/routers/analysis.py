from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from app.ai import AIConfigError, AIGenerationError, analyze_post
from app.database import db_cursor, row_to_dict
from app.platforms import PLATFORM_MAP

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _get_stats_for_content(cur, content_id: int) -> list[dict]:
    cur.execute(
        "SELECT cp.platform_id, ps.views, ps.likes, ps.comments "
        "FROM content_platforms cp JOIN post_stats ps ON ps.content_platform_id = cp.id "
        "WHERE cp.content_id = ?",
        (content_id,),
    )
    stats = []
    for r in cur.fetchall():
        stats.append(
            {
                "platform_id": r["platform_id"],
                "platform_name": PLATFORM_MAP[r["platform_id"]].name if r["platform_id"] in PLATFORM_MAP else r["platform_id"],
                "views": r["views"],
                "likes": r["likes"],
                "comments": r["comments"],
            }
        )
    return stats


def _serialize(row) -> dict:
    d = row_to_dict(row)
    d["suggestions"] = json.loads(d["suggestions"]) if d.get("suggestions") else []
    return d


@router.get("")
def list_analysis():
    with db_cursor() as cur:
        cur.execute(
            "SELECT c.id AS content_id, c.title, c.body, c.status, "
            "ca.score, ca.feedback, ca.suggestions, ca.created_at AS analyzed_at "
            "FROM content c LEFT JOIN content_analysis ca ON ca.content_id = c.id "
            "ORDER BY c.created_at DESC"
        )
        items = [_serialize(r) for r in cur.fetchall()]
        for item in items:
            item["stats"] = _get_stats_for_content(cur, item["content_id"])
        return items


@router.post("/{content_id}")
def run_analysis(content_id: int):
    with db_cursor() as cur:
        cur.execute(
            "SELECT c.*, cp.platform_id FROM content c "
            "LEFT JOIN content_platforms cp ON cp.content_id = c.id "
            "WHERE c.id = ? LIMIT 1",
            (content_id,),
        )
        content = cur.fetchone()
        if not content:
            raise HTTPException(404, "Материал не найден")
        stats = _get_stats_for_content(cur, content_id)

    try:
        result = analyze_post(content["title"], content["body"], content["platform_id"], stats)
    except AIConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except AIGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO content_analysis (content_id, score, feedback, suggestions) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(content_id) DO UPDATE SET "
            "score = excluded.score, feedback = excluded.feedback, "
            "suggestions = excluded.suggestions, created_at = datetime('now')",
            (content_id, result["score"], result["feedback"], json.dumps(result["suggestions"], ensure_ascii=False)),
        )
        cur.execute(
            "SELECT c.id AS content_id, c.title, c.body, c.status, "
            "ca.score, ca.feedback, ca.suggestions, ca.created_at AS analyzed_at "
            "FROM content c LEFT JOIN content_analysis ca ON ca.content_id = c.id WHERE c.id = ?",
            (content_id,),
        )
        item = _serialize(cur.fetchone())
        item["stats"] = stats
        return item


@router.post("/{content_id}/save-ideas")
def save_ideas(content_id: int):
    with db_cursor() as cur:
        cur.execute(
            "SELECT c.title, ca.suggestions FROM content_analysis ca "
            "JOIN content c ON c.id = ca.content_id WHERE ca.content_id = ?",
            (content_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Сначала выполните анализ этого поста")

    suggestions = json.loads(row["suggestions"]) if row["suggestions"] else []
    if not suggestions:
        return {"added": 0}

    with db_cursor() as cur:
        for s in suggestions:
            cur.execute(
                "INSERT INTO knowledge_base (title, content, source) VALUES (?, ?, 'idea')",
                (f"По посту «{row['title']}»", s),
            )
    return {"added": len(suggestions)}

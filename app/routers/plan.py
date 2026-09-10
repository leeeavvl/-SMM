from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException

from app.ai import AIConfigError, AIGenerationError, generate_plan, generate_posts
from app.database import db_cursor, get_setting, row_to_dict
from app.google_sheets import SheetsConfigError, SheetsSyncError, append_plan_items, update_cell_text
from app.plan_options import get_options
from app.schemas import PlanGenerateRequest, PlanLinkContentRequest, PlanWriteRequest

router = APIRouter(prefix="/api/plan", tags=["plan"])


def _auto_sync_enabled() -> bool:
    return (get_setting("google_sheets_auto_sync") or "0") == "1"


def _sync_unsynced_to_sheet() -> tuple[int, list[str]]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM content_plan WHERE synced_to_sheet = 0")
        rows = [row_to_dict(r) for r in cur.fetchall()]
    if not rows:
        return 0, []
    try:
        placements, errors = append_plan_items(rows)
    except SheetsConfigError as exc:
        return 0, [str(exc)]

    with db_cursor() as cur:
        for plan_id, sheet_tab, sheet_row in placements:
            cur.execute(
                "UPDATE content_plan SET synced_to_sheet = 1, sheet_tab = ?, sheet_row = ? WHERE id = ?",
                (sheet_tab, sheet_row, plan_id),
            )
    return len(placements), errors


@router.get("/options")
def plan_options():
    return get_options()


@router.get("")
def list_plan():
    with db_cursor() as cur:
        cur.execute(
            "SELECT cp.*, c.title AS content_title, c.status AS content_status "
            "FROM content_plan cp LEFT JOIN content c ON c.id = cp.content_id "
            "ORDER BY cp.plan_date ASC, cp.id ASC"
        )
        return [row_to_dict(r) for r in cur.fetchall()]


@router.post("/generate")
def generate(payload: PlanGenerateRequest):
    try:
        start_date = dt.date.fromisoformat(payload.start_date)
        end_date = dt.date.fromisoformat(payload.end_date)
    except ValueError as exc:
        raise HTTPException(400, f"Некорректная дата: {exc}") from exc
    if end_date < start_date:
        raise HTTPException(400, "Дата «по» не может быть раньше даты «с»")
    period_days = (end_date - start_date).days + 1
    if period_days > 60:
        raise HTTPException(400, "Период не может превышать 60 дней")

    try:
        items, errors = generate_plan(
            platform_id=payload.platform_id,
            rows=[r.model_dump() for r in payload.rows],
            period_days=period_days,
            tone=payload.tone,
        )
    except AIConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except AIGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc

    with db_cursor() as cur:
        for item in items:
            plan_date = (start_date + dt.timedelta(days=item["day_offset"])).isoformat()
            cur.execute(
                "INSERT INTO content_plan (plan_date, platform_id, topic, direction, content_type, format, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'idea')",
                (
                    plan_date,
                    item["platform_id"],
                    item["topic"],
                    item["direction"],
                    item["content_type"],
                    item["format"],
                ),
            )
        cur.execute(
            "SELECT cp.*, c.title AS content_title, c.status AS content_status "
            "FROM content_plan cp LEFT JOIN content c ON c.id = cp.content_id "
            "ORDER BY cp.plan_date ASC, cp.id ASC"
        )
        all_items = [row_to_dict(r) for r in cur.fetchall()]

    sheet_errors: list[str] = []
    if _auto_sync_enabled():
        _, sheet_errors = _sync_unsynced_to_sheet()

    return {"items": all_items, "errors": errors, "sheet_errors": sheet_errors}


@router.post("/sync-sheet")
def sync_sheet():
    count, errors = _sync_unsynced_to_sheet()
    if errors and count == 0:
        raise HTTPException(502, "; ".join(errors))
    return {"synced": count, "errors": errors}


@router.post("/{plan_id}/write")
def write_post(plan_id: int, payload: PlanWriteRequest):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM content_plan WHERE id = ?", (plan_id,))
        plan_item = cur.fetchone()
    if not plan_item:
        raise HTTPException(404, "Пункт плана не найден")

    plan_brief = (
        f"Направление/рубрика: {plan_item['direction']}. "
        f"Тип контента: {plan_item['content_type']}. "
        f"Формат публикации: {plan_item['format']}."
    )
    try:
        variants, errors = generate_posts(
            topic=plan_item["topic"],
            brief=plan_brief,
            tone="нейтральный",
            platform_ids=[plan_item["platform_id"]],
            variants=1,
            length=payload.length,
        )
    except AIConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except AIGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc

    if not variants:
        raise HTTPException(502, "Модель не вернула текст поста")

    post = variants[0]
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO content (title, body, tags) VALUES (?, ?, ?)",
            (post["title"], post["body"], post.get("tags", "")),
        )
        content_id = cur.lastrowid
        cur.execute(
            "UPDATE content_plan SET status = 'drafted', content_id = ? WHERE id = ?",
            (content_id, plan_id),
        )
        cur.execute(
            "SELECT cp.*, c.title AS content_title, c.status AS content_status "
            "FROM content_plan cp LEFT JOIN content c ON c.id = cp.content_id WHERE cp.id = ?",
            (plan_id,),
        )
        updated = row_to_dict(cur.fetchone())

    return updated


@router.post("/{plan_id}/link-content")
def link_content(plan_id: int, payload: PlanLinkContentRequest):
    """Привязывает материал, написанный/сохранённый в редакторе поста (открытом
    из «Контент-плана»), к пункту плана — чтобы план отражал, что текст готов."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM content_plan WHERE id = ?", (plan_id,))
        plan_item = cur.fetchone()
        if not plan_item:
            raise HTTPException(404, "Пункт плана не найден")
        cur.execute("SELECT id FROM content WHERE id = ?", (payload.content_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Материал не найден")

        new_status = "drafted" if plan_item["status"] == "idea" else plan_item["status"]
        cur.execute(
            "UPDATE content_plan SET content_id = ?, status = ? WHERE id = ?",
            (payload.content_id, new_status, plan_id),
        )
        cur.execute(
            "SELECT cp.*, c.title AS content_title, c.status AS content_status "
            "FROM content_plan cp LEFT JOIN content c ON c.id = cp.content_id WHERE cp.id = ?",
            (plan_id,),
        )
        return row_to_dict(cur.fetchone())


@router.post("/{plan_id}/approve")
def approve_post(plan_id: int):
    """Помечает пост одобренным и вписывает готовый текст в колонку «текст» Google Таблицы."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT cp.*, c.body AS content_body FROM content_plan cp "
            "LEFT JOIN content c ON c.id = cp.content_id WHERE cp.id = ?",
            (plan_id,),
        )
        plan_item = cur.fetchone()
    if not plan_item:
        raise HTTPException(404, "Пункт плана не найден")
    if not plan_item["content_id"]:
        raise HTTPException(400, "Сначала напишите текст поста («Написать текст»)")

    with db_cursor() as cur:
        cur.execute("UPDATE content_plan SET status = 'approved' WHERE id = ?", (plan_id,))

    sheet_tab = plan_item["sheet_tab"]
    sheet_row = plan_item["sheet_row"]
    body = plan_item["content_body"] or ""

    if not sheet_tab or not sheet_row:
        # строка ещё не попадала в таблицу — допишем её сразу с текстом
        item = row_to_dict(plan_item)
        item["status"] = "approved"
        item["body"] = body
        try:
            placements, errors = append_plan_items([item])
        except SheetsConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        if placements:
            plan_id_, sheet_tab, sheet_row = placements[0]
            with db_cursor() as cur:
                cur.execute(
                    "UPDATE content_plan SET synced_to_sheet = 1, sheet_tab = ?, sheet_row = ? WHERE id = ?",
                    (sheet_tab, sheet_row, plan_id),
                )
        elif errors:
            raise HTTPException(502, "; ".join(errors))
    else:
        try:
            update_cell_text(sheet_tab, sheet_row, body, status="approved")
        except (SheetsConfigError, SheetsSyncError) as exc:
            raise HTTPException(502, str(exc)) from exc

    with db_cursor() as cur:
        cur.execute(
            "SELECT cp.*, c.title AS content_title, c.status AS content_status "
            "FROM content_plan cp LEFT JOIN content c ON c.id = cp.content_id WHERE cp.id = ?",
            (plan_id,),
        )
        return row_to_dict(cur.fetchone())


@router.delete("/{plan_id}")
def delete_plan_item(plan_id: int):
    with db_cursor() as cur:
        cur.execute("DELETE FROM content_plan WHERE id = ?", (plan_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Пункт плана не найден")
    return {"ok": True}

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.database import db_cursor, row_to_dict
from app.schemas import ContentCreate, ContentUpdate

router = APIRouter(prefix="/api/content", tags=["content"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "uploads"
ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}
MAX_MEDIA_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/upload-media")
async def upload_media(file: UploadFile = File(...)):
    ext = ALLOWED_MEDIA_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(400, "Поддерживаются только изображения и видео (jpg, png, gif, webp, mp4, mov, webm)")

    data = await file.read()
    if len(data) > MAX_MEDIA_BYTES:
        raise HTTPException(400, "Файл слишком большой (максимум 25 МБ)")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / filename).write_bytes(data)
    return {"url": f"/static/uploads/{filename}"}


def _with_platforms(cur, content_id: int) -> dict:
    cur.execute("SELECT * FROM content WHERE id = ?", (content_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Материал не найден")
    data = row_to_dict(row)
    cur.execute("SELECT * FROM content_platforms WHERE content_id = ?", (content_id,))
    data["platforms"] = [row_to_dict(r) for r in cur.fetchall()]
    return data


@router.get("")
def list_content():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM content ORDER BY created_at DESC")
        items = [row_to_dict(r) for r in cur.fetchall()]
        for item in items:
            cur.execute("SELECT * FROM content_platforms WHERE content_id = ?", (item["id"],))
            item["platforms"] = [row_to_dict(r) for r in cur.fetchall()]
    return items


@router.post("")
def create_content(payload: ContentCreate):
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO content (title, body, media_url, tags) VALUES (?, ?, ?, ?)",
            (payload.title, payload.body, payload.media_url, payload.tags),
        )
        content_id = cur.lastrowid
        data = _with_platforms(cur, content_id)
    return data


@router.get("/{content_id}")
def get_content(content_id: int):
    with db_cursor() as cur:
        return _with_platforms(cur, content_id)


@router.put("/{content_id}")
def update_content(content_id: int, payload: ContentUpdate):
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "Нет данных для обновления")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with db_cursor() as cur:
        cur.execute(
            f"UPDATE content SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), content_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Материал не найден")
        data = _with_platforms(cur, content_id)
    return data


@router.delete("/{content_id}")
def delete_content(content_id: int):
    with db_cursor() as cur:
        cur.execute("DELETE FROM content WHERE id = ?", (content_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Материал не найден")
    return {"ok": True}

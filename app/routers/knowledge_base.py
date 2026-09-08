from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import db_cursor, row_to_dict

router = APIRouter(prefix="/api/knowledge-base", tags=["knowledge-base"])


class KnowledgeBaseCreate(BaseModel):
    title: str = ""
    content: str = Field(min_length=1)


@router.get("")
def list_entries():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM knowledge_base ORDER BY id DESC")
        return [row_to_dict(r) for r in cur.fetchall()]


@router.post("")
def create_entry(payload: KnowledgeBaseCreate):
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO knowledge_base (title, content, source) VALUES (?, ?, 'upload')",
            (payload.title.strip(), payload.content.strip()),
        )
        cur.execute("SELECT * FROM knowledge_base WHERE id = ?", (cur.lastrowid,))
        return row_to_dict(cur.fetchone())


@router.delete("/{entry_id}")
def delete_entry(entry_id: int):
    with db_cursor() as cur:
        cur.execute("DELETE FROM knowledge_base WHERE id = ?", (entry_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Запись не найдена")
    return {"ok": True}

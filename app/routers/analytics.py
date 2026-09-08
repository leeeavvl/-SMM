from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.database import db_cursor, row_to_dict
from app.platforms import PLATFORM_MAP
from app.schemas import SaleCreate

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytics/summary")
def analytics_summary():
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM content")
        total_content = cur.fetchone()["n"]

        cur.execute("SELECT status, COUNT(*) AS n FROM content GROUP BY status")
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}

        cur.execute(
            "SELECT platform_id, status, COUNT(*) AS n FROM content_platforms "
            "GROUP BY platform_id, status"
        )
        per_platform: dict[str, dict[str, int]] = {pid: {} for pid in PLATFORM_MAP}
        for r in cur.fetchall():
            per_platform.setdefault(r["platform_id"], {})[r["status"]] = r["n"]

        cur.execute(
            "SELECT platform_id, SUM(orders) AS orders, SUM(revenue) AS revenue "
            "FROM sales GROUP BY platform_id"
        )
        sales_per_platform = {
            r["platform_id"]: {"orders": r["orders"] or 0, "revenue": r["revenue"] or 0}
            for r in cur.fetchall()
        }

        cur.execute("SELECT SUM(orders) AS orders, SUM(revenue) AS revenue FROM sales")
        totals_row = cur.fetchone()
        totals = {"orders": totals_row["orders"] or 0, "revenue": totals_row["revenue"] or 0}

    return {
        "total_content": total_content,
        "content_by_status": by_status,
        "publications_per_platform": per_platform,
        "sales_per_platform": sales_per_platform,
        "sales_totals": totals,
    }


@router.get("/sales")
def list_sales():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM sales ORDER BY date DESC, id DESC")
        return [row_to_dict(r) for r in cur.fetchall()]


@router.post("/sales")
def create_sale(payload: SaleCreate):
    if payload.platform_id not in PLATFORM_MAP:
        raise HTTPException(400, "Неизвестная платформа")
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO sales (platform_id, date, orders, revenue, notes) "
            "VALUES (?, COALESCE(?, date('now')), ?, ?, ?)",
            (payload.platform_id, payload.date, payload.orders, payload.revenue, payload.notes),
        )
        cur.execute("SELECT * FROM sales WHERE id = ?", (cur.lastrowid,))
        return row_to_dict(cur.fetchone())


@router.delete("/sales/{sale_id}")
def delete_sale(sale_id: int):
    with db_cursor() as cur:
        cur.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Запись не найдена")
    return {"ok": True}

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import ai, analysis, analytics, canva, content, knowledge_base, plan, platforms, publish, settings, stats
from app.scheduler import start_scheduler

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="Контент-завод", lifespan=lifespan)

app.include_router(content.router)
app.include_router(platforms.router)
app.include_router(publish.router)
app.include_router(analytics.router)
app.include_router(settings.router)
app.include_router(ai.router)
app.include_router(plan.router)
app.include_router(stats.router)
app.include_router(analysis.router)
app.include_router(knowledge_base.router)
app.include_router(canva.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

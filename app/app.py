from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
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

# Если приложение выложено в открытый доступ (например, на Railway), у него
# самого нет авторизации — задайте APP_AUTH_USER / APP_AUTH_PASSWORD как
# переменные окружения на хостинге, и весь сайт закроется базовым паролем.
# Локально (без этих переменных) ничего не меняется — пароль не спрашивается.
_auth_user = os.environ.get("APP_AUTH_USER")
_auth_password = os.environ.get("APP_AUTH_PASSWORD")
_basic_auth = HTTPBasic(auto_error=False) if _auth_user else None

if _basic_auth:

    @app.middleware("http")
    async def require_basic_auth(request: Request, call_next):
        credentials: HTTPBasicCredentials | None = await _basic_auth(request)
        valid = bool(credentials) and secrets.compare_digest(
            credentials.username, _auth_user
        ) and secrets.compare_digest(credentials.password, _auth_password or "")
        if not valid:
            # HTTPException не превращается в ответ автоматически из
            # middleware (в отличие от обработчиков маршрутов) — отдаём
            # Response напрямую, иначе получится 500 вместо 401.
            return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})
        return await call_next(request)


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

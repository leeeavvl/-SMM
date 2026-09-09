from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app import canva
from app.database import set_setting

router = APIRouter(prefix="/api/canva", tags=["canva"])


class CanvaCredentials(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class CreateDesignRequest(BaseModel):
    title: str = "Пост для соцсетей"
    width: int = Field(default=1080, ge=40, le=8000)
    height: int = Field(default=1080, ge=40, le=8000)


@router.get("/status")
def canva_status():
    return {
        "configured": bool(canva.get_client_id() and canva.get_client_secret()),
        "connected": canva.is_connected(),
    }


@router.post("/credentials")
def save_credentials(payload: CanvaCredentials):
    set_setting("canva_client_id", payload.client_id.strip())
    set_setting("canva_client_secret", payload.client_secret.strip())
    return {"ok": True}


@router.get("/oauth/start")
def oauth_start():
    try:
        url = canva.build_authorize_url()
    except canva.CanvaConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url)


@router.get("/oauth/callback")
def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(f"/?view=settings&canva_error={error}")
    if not code or not state:
        return RedirectResponse("/?view=settings&canva_error=missing_code")
    try:
        canva.handle_oauth_callback(code, state)
    except (canva.CanvaConfigError, canva.CanvaAPIError) as exc:
        return RedirectResponse(f"/?view=settings&canva_error={exc}")
    return RedirectResponse("/?view=settings&canva_connected=1")


@router.post("/disconnect")
def disconnect():
    canva.disconnect()
    return {"ok": True}


@router.post("/designs")
def create_design(payload: CreateDesignRequest):
    try:
        return canva.create_design(payload.title, payload.width, payload.height)
    except canva.CanvaConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except canva.CanvaAPIError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/designs/{design_id}/export")
def export_design(design_id: str):
    try:
        return canva.export_and_wait(design_id)
    except canva.CanvaConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except canva.CanvaAPIError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/exports/{job_id}")
def get_export_status(job_id: str):
    try:
        return canva.check_export(job_id)
    except canva.CanvaConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except canva.CanvaAPIError as exc:
        raise HTTPException(502, str(exc)) from exc

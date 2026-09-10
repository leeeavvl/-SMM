from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile

from app import brandbook
from app.ai import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OPENAI_MODEL,
    get_gemini_model,
    get_ollama_base_url,
    get_ollama_model,
    get_openai_model,
    get_provider,
)
from app.database import DEFAULT_BRAND_DESCRIPTION, DEFAULT_BRAND_NAME, get_setting, set_setting
from app.google_sheets import SheetsConfigError, SheetsSyncError, test_connection
from app.schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _current_settings() -> dict:
    has_key = bool(get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))
    has_openai_key = bool(get_setting("openai_api_key") or os.environ.get("OPENAI_API_KEY"))
    has_gemini_key = bool(get_setting("gemini_api_key") or os.environ.get("GEMINI_API_KEY"))
    return {
        "anthropic_api_key_set": has_key,
        "openai_api_key_set": has_openai_key,
        "openai_model": get_openai_model(),
        "gemini_api_key_set": has_gemini_key,
        "gemini_model": get_gemini_model(),
        "ai_provider": get_provider(),
        "ollama_model": get_ollama_model(),
        "ollama_base_url": get_ollama_base_url(),
        "brand_name": get_setting("brand_name") or DEFAULT_BRAND_NAME,
        "brand_description": get_setting("brand_description") or DEFAULT_BRAND_DESCRIPTION,
        "google_sheet_url": get_setting("google_sheet_url") or "",
        "google_sheet_worksheet": get_setting("google_sheet_worksheet") or "",
        "google_service_account_path": get_setting("google_service_account_path") or "",
        "google_sheets_auto_sync": (get_setting("google_sheets_auto_sync") or "0") == "1",
        "brand_book": brandbook.get_brand_profile(),
        "auto_learning_enabled": (get_setting("auto_learning_enabled") or "1") == "1",
    }


@router.get("")
def get_settings():
    return _current_settings()


@router.post("")
def update_settings(payload: SettingsUpdate):
    if payload.anthropic_api_key is not None:
        set_setting("anthropic_api_key", payload.anthropic_api_key.strip())
    if payload.openai_api_key is not None:
        set_setting("openai_api_key", payload.openai_api_key.strip())
    if payload.openai_model is not None:
        set_setting("openai_model", payload.openai_model.strip() or DEFAULT_OPENAI_MODEL)
    if payload.gemini_api_key is not None:
        set_setting("gemini_api_key", payload.gemini_api_key.strip())
    if payload.gemini_model is not None:
        set_setting("gemini_model", payload.gemini_model.strip() or DEFAULT_GEMINI_MODEL)
    if payload.ai_provider is not None:
        set_setting("ai_provider", payload.ai_provider.strip())
    if payload.ollama_model is not None:
        set_setting("ollama_model", payload.ollama_model.strip() or DEFAULT_OLLAMA_MODEL)
    if payload.ollama_base_url is not None:
        set_setting("ollama_base_url", payload.ollama_base_url.strip() or DEFAULT_OLLAMA_URL)
    if payload.brand_name is not None:
        set_setting("brand_name", payload.brand_name.strip() or DEFAULT_BRAND_NAME)
    if payload.brand_description is not None:
        set_setting("brand_description", payload.brand_description.strip() or DEFAULT_BRAND_DESCRIPTION)
    if payload.google_sheet_url is not None:
        set_setting("google_sheet_url", payload.google_sheet_url.strip())
    if payload.google_sheet_worksheet is not None:
        set_setting("google_sheet_worksheet", payload.google_sheet_worksheet.strip())
    if payload.google_service_account_path is not None:
        set_setting("google_service_account_path", payload.google_service_account_path.strip())
    if payload.google_sheets_auto_sync is not None:
        set_setting("google_sheets_auto_sync", "1" if payload.google_sheets_auto_sync else "0")
    if payload.auto_learning_enabled is not None:
        set_setting("auto_learning_enabled", "1" if payload.auto_learning_enabled else "0")

    return _current_settings()


@router.get("/ollama-status")
def ollama_status():
    base_url = get_ollama_base_url()
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return {"reachable": True, "models": models}
    except httpx.HTTPError:
        return {"reachable": False, "models": []}


@router.post("/run-auto-learning")
def run_auto_learning_now():
    from app.auto_learning import run_auto_learning

    return run_auto_learning()


@router.post("/brand-book")
async def upload_brand_book(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        raise HTTPException(400, "Загрузите брендбук в формате PDF")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "Файл слишком большой (максимум 20 МБ)")
    try:
        profile = brandbook.parse_and_store_brand_book(data)
    except Exception as exc:
        raise HTTPException(400, f"Не удалось разобрать PDF: {exc}") from exc
    return {"uploaded": True, **profile}


@router.get("/google-sheets-status")
def google_sheets_status():
    try:
        return test_connection()
    except SheetsConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SheetsSyncError as exc:
        raise HTTPException(502, str(exc)) from exc

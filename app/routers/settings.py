from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException

from app.ai import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, get_ollama_base_url, get_ollama_model, get_provider
from app.database import DEFAULT_BRAND_DESCRIPTION, DEFAULT_BRAND_NAME, get_setting, set_setting
from app.google_sheets import SheetsConfigError, SheetsSyncError, test_connection
from app.schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _current_settings() -> dict:
    has_key = bool(get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "anthropic_api_key_set": has_key,
        "ai_provider": get_provider(),
        "ollama_model": get_ollama_model(),
        "ollama_base_url": get_ollama_base_url(),
        "brand_name": get_setting("brand_name") or DEFAULT_BRAND_NAME,
        "brand_description": get_setting("brand_description") or DEFAULT_BRAND_DESCRIPTION,
        "google_sheet_url": get_setting("google_sheet_url") or "",
        "google_sheet_worksheet": get_setting("google_sheet_worksheet") or "",
        "google_service_account_path": get_setting("google_service_account_path") or "",
        "google_sheets_auto_sync": (get_setting("google_sheets_auto_sync") or "0") == "1",
    }


@router.get("")
def get_settings():
    return _current_settings()


@router.post("")
def update_settings(payload: SettingsUpdate):
    if payload.anthropic_api_key is not None:
        set_setting("anthropic_api_key", payload.anthropic_api_key.strip())
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


@router.get("/google-sheets-status")
def google_sheets_status():
    try:
        return test_connection()
    except SheetsConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SheetsSyncError as exc:
        raise HTTPException(502, str(exc)) from exc

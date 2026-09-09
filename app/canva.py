"""Интеграция с Canva Connect API.

OAuth 2.0 с PKCE (обязателен для Canva): пользователь один раз подключает свой
аккаунт Canva в «Настройках», после чего в редакторе поста можно создать дизайн
в Canva (открывается в новой вкладке для оформления) и забрать готовый экспорт
обратно как медиа поста.

Токены и код-верификатор PKCE хранятся в таблице settings (как остальные
API-ключи в проекте) — приложение однопользовательское и локальное, отдельное
хранилище сессий не нужно.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.database import get_setting, set_setting

AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
API_BASE = "https://api.canva.com/rest/v1"

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8000/api/canva/oauth/callback"
SCOPES = "asset:read asset:write design:content:read design:content:write design:meta:read"

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads"


class CanvaConfigError(RuntimeError):
    pass


class CanvaAPIError(RuntimeError):
    pass


def get_client_id() -> str | None:
    return get_setting("canva_client_id")


def get_client_secret() -> str | None:
    return get_setting("canva_client_secret")


def get_redirect_uri() -> str:
    return get_setting("canva_redirect_uri") or DEFAULT_REDIRECT_URI


def is_connected() -> bool:
    return bool(get_setting("canva_access_token"))


def _require_client_credentials() -> tuple[str, str]:
    client_id = get_client_id()
    client_secret = get_client_secret()
    if not client_id or not client_secret:
        raise CanvaConfigError(
            "Canva Client ID / Client Secret не настроены. Добавьте их в разделе «Настройки»."
        )
    return client_id, client_secret


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def build_authorize_url() -> str:
    client_id, _ = _require_client_credentials()

    code_verifier = _b64url(secrets.token_bytes(64))
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(24))

    # Однопользовательское приложение — храним верификатор/state в settings,
    # отдельная сессия не нужна.
    set_setting("canva_pending_code_verifier", code_verifier)
    set_setting("canva_pending_state", state)

    params = {
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
        "response_type": "code",
        "client_id": client_id,
        "scope": SCOPES,
        "state": state,
        "redirect_uri": get_redirect_uri(),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def handle_oauth_callback(code: str, state: str) -> None:
    expected_state = get_setting("canva_pending_state")
    code_verifier = get_setting("canva_pending_code_verifier")
    if not expected_state or state != expected_state:
        raise CanvaConfigError("Неверный state — попробуйте подключить Canva заново.")
    if not code_verifier:
        raise CanvaConfigError("Истёк код авторизации — попробуйте подключить Canva заново.")

    client_id, client_secret = _require_client_credentials()
    try:
        resp = httpx.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": get_redirect_uri(),
            },
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CanvaAPIError(f"Canva отклонила обмен кода на токен: {exc.response.text}") from exc

    data = resp.json()
    _store_tokens(data)
    set_setting("canva_pending_code_verifier", "")
    set_setting("canva_pending_state", "")


def _store_tokens(data: dict) -> None:
    set_setting("canva_access_token", data["access_token"])
    if data.get("refresh_token"):
        set_setting("canva_refresh_token", data["refresh_token"])
    expires_in = int(data.get("expires_in", 3600))
    set_setting("canva_token_expires_at", str(int(time.time()) + expires_in - 60))


def _refresh_access_token() -> str:
    client_id, client_secret = _require_client_credentials()
    refresh_token = get_setting("canva_refresh_token")
    if not refresh_token:
        raise CanvaConfigError("Canva не подключена. Подключите её в «Настройках».")
    try:
        resp = httpx.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CanvaConfigError(
            "Не удалось обновить подключение к Canva — переподключите её в «Настройках»."
        ) from exc
    data = resp.json()
    _store_tokens(data)
    return data["access_token"]


def _access_token() -> str:
    token = get_setting("canva_access_token")
    if not token:
        raise CanvaConfigError("Canva не подключена. Подключите её в «Настройках».")
    expires_at = int(get_setting("canva_token_expires_at") or 0)
    if time.time() >= expires_at:
        return _refresh_access_token()
    return token


def disconnect() -> None:
    for key in ("canva_access_token", "canva_refresh_token", "canva_token_expires_at"):
        set_setting(key, "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_access_token()}"}


def create_design(title: str, width: int = 1080, height: int = 1080, asset_id: str | None = None) -> dict:
    """По умолчанию — квадрат 1080x1080: это основной размер поста, указанный
    почти во всех макетах в брендбуке «Карьерного юриста» (при желании можно
    передать другой размер под конкретный вид поста). Если передан asset_id —
    дизайн создаётся уже с этим изображением на холсте (например, чтобы
    доработать/улучшить его средствами самой Canva)."""
    body: dict = {"design_type": {"type": "custom", "width": width, "height": height}, "title": title[:50]}
    if asset_id:
        body["asset_id"] = asset_id
    try:
        resp = httpx.post(f"{API_BASE}/designs", headers=_headers(), json=body, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CanvaAPIError(f"Canva не смогла создать дизайн: {exc.response.text}") from exc

    design = resp.json()["design"]
    return {
        "design_id": design["id"],
        "edit_url": design["urls"]["edit_url"],
        "view_url": design["urls"].get("view_url"),
    }


def upload_asset(file_path: Path, name: str, max_wait_seconds: float = 30.0) -> str:
    """Загружает локальный файл в Canva как asset (для последующего использования
    как исходное изображение при создании дизайна). Возвращает asset_id."""
    metadata = _b64url(name.encode())
    try:
        resp = httpx.post(
            f"{API_BASE}/asset-uploads",
            headers={
                **_headers(),
                "Content-Type": "application/octet-stream",
                "Asset-Upload-Metadata": f'{{"name_base64":"{metadata}"}}',
            },
            content=file_path.read_bytes(),
            timeout=60,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CanvaAPIError(f"Canva не смогла принять файл: {exc.response.text}") from exc

    job_id = resp.json()["job"]["id"]
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        try:
            status_resp = httpx.get(f"{API_BASE}/asset-uploads/{job_id}", headers=_headers(), timeout=30)
            status_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CanvaAPIError(f"Не удалось проверить статус загрузки: {exc.response.text}") from exc
        job = status_resp.json()["job"]
        if job["status"] == "success":
            return job["asset"]["id"]
        if job["status"] == "failed":
            raise CanvaAPIError((job.get("error") or {}).get("message", "Загрузка файла в Canva не удалась"))
        time.sleep(1.5)
    raise CanvaAPIError("Загрузка файла в Canva заняла слишком много времени")


def start_export(design_id: str) -> str:
    try:
        resp = httpx.post(
            f"{API_BASE}/exports",
            headers=_headers(),
            json={"design_id": design_id, "format": {"type": "png"}},
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CanvaAPIError(f"Canva не смогла начать экспорт: {exc.response.text}") from exc
    return resp.json()["job"]["id"]


def _download_and_save(url: str) -> str:
    img_bytes = httpx.get(url, timeout=60).content
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    import uuid

    filename = f"{uuid.uuid4().hex}.png"
    (UPLOAD_DIR / filename).write_bytes(img_bytes)
    return f"/static/uploads/{filename}"


def check_export(job_id: str) -> dict:
    """Возвращает {"status": "in_progress"} | {"status": "success", "url": "..."} | {"status": "failed", "error": "..."}"""
    try:
        resp = httpx.get(f"{API_BASE}/exports/{job_id}", headers=_headers(), timeout=30)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CanvaAPIError(f"Не удалось проверить статус экспорта: {exc.response.text}") from exc

    job = resp.json()["job"]
    status = job["status"]
    if status == "success":
        urls = job.get("urls") or []
        if not urls:
            return {"status": "failed", "error": "Canva не вернула ссылку на файл"}
        local_url = _download_and_save(urls[0])
        return {"status": "success", "url": local_url}
    if status == "failed":
        error = (job.get("error") or {}).get("message", "неизвестная ошибка")
        return {"status": "failed", "error": error}
    return {"status": "in_progress"}


def export_and_wait(design_id: str, max_wait_seconds: float = 12.0) -> dict:
    """Запускает экспорт и недолго поллит — для UI, где пользователь ждёт прямо
    в модалке. Если не успело — возвращает job_id, чтобы фронтенд дождал через
    отдельный запрос (кнопка «Проверить готовность»)."""
    job_id = start_export(design_id)
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        result = check_export(job_id)
        if result["status"] != "in_progress":
            result["job_id"] = job_id
            return result
        time.sleep(1.5)
    return {"status": "in_progress", "job_id": job_id}

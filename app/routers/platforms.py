from __future__ import annotations

import json
import shutil
from io import BytesIO

import fitz  # PyMuPDF
import pytesseract
from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.database import db_cursor, load_json, row_to_dict
from app.platforms import PLATFORM_MAP
from app.schemas import PlatformConnect, PlatformSettingsUpdate

router = APIRouter(prefix="/api/platforms", tags=["platforms"])

MAX_BRIEF_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
OCR_MAX_PAGES = 20

# На macOS с Apple Silicon Homebrew ставит бинарники в /opt/homebrew, который не всегда
# попадает в PATH процесса (например, если main.py запущен не из нового терминала).
# Явно указываем путь, если сам tesseract не находится через PATH.
if not shutil.which("tesseract"):
    for candidate in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if shutil.which(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            break


def _ocr_pdf(data: bytes) -> str:
    """Рендерит страницы PDF в изображения и распознаёт текст через Tesseract OCR.

    Используется, когда обычное извлечение текста дало пустой результат —
    типичный случай для сканов и инфографики (например, экспорт из Canva),
    где текст — это картинка/векторные контуры, а не настоящий текстовый слой.
    """
    doc = fitz.open(stream=data, filetype="pdf")
    texts = []
    for page_index in range(min(len(doc), OCR_MAX_PAGES)):
        page = doc.load_page(page_index)
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        try:
            text = pytesseract.image_to_string(img, lang="rus+eng")
        except pytesseract.TesseractNotFoundError as exc:
            raise RuntimeError(
                "Tesseract OCR не установлен. Установите Homebrew (brew.sh), затем выполните: "
                "brew install tesseract tesseract-lang"
            ) from exc
        if text.strip():
            texts.append(text.strip())
    doc.close()
    return "\n\n".join(texts)


def _serialize(row) -> dict:
    d = row_to_dict(row)
    meta = PLATFORM_MAP[d["id"]]
    creds = load_json(d["credentials"])
    return {
        "id": meta.id,
        "name": meta.name,
        "color": meta.color,
        "credential_fields": list(meta.credential_fields),
        "connected": bool(d["connected"]),
        "credential_keys_set": list(creds.keys()),
        "brief": d.get("brief") or "",
        "channel_url": d.get("channel_url") or "",
    }


@router.get("")
def list_platforms():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM platforms")
        rows = cur.fetchall()
    return [_serialize(r) for r in rows]


@router.post("/{platform_id}/connect")
def connect_platform(platform_id: str, payload: PlatformConnect):
    if platform_id not in PLATFORM_MAP:
        raise HTTPException(404, "Неизвестная платформа")
    with db_cursor() as cur:
        cur.execute(
            "UPDATE platforms SET connected = 1, credentials = ? WHERE id = ?",
            (json.dumps(payload.credentials), platform_id),
        )
        cur.execute("SELECT * FROM platforms WHERE id = ?", (platform_id,))
        row = cur.fetchone()
    return _serialize(row)


@router.post("/{platform_id}/settings")
def update_platform_settings(platform_id: str, payload: PlatformSettingsUpdate):
    if platform_id not in PLATFORM_MAP:
        raise HTTPException(404, "Неизвестная платформа")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with db_cursor() as cur:
            cur.execute(
                f"UPDATE platforms SET {set_clause} WHERE id = ?",
                (*fields.values(), platform_id),
            )
    with db_cursor() as cur:
        cur.execute("SELECT * FROM platforms WHERE id = ?", (platform_id,))
        row = cur.fetchone()
    return _serialize(row)


@router.post("/extract-brief-text")
async def extract_brief_text(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    data = await file.read()
    if len(data) > MAX_BRIEF_FILE_SIZE:
        raise HTTPException(400, "Файл слишком большой (максимум 10 МБ)")

    if filename.endswith(".pdf") or file.content_type == "application/pdf":
        try:
            reader = PdfReader(BytesIO(data))
            pages_text = [page.extract_text() or "" for page in reader.pages]
        except PdfReadError as exc:
            raise HTTPException(400, f"Не удалось прочитать PDF: {exc}") from exc
        text = "\n\n".join(t.strip() for t in pages_text if t.strip())

        if not text:
            # Текстового слоя нет — вероятно, скан или инфографика (например, из Canva).
            # Пробуем распознать текст по изображению страниц через OCR.
            try:
                text = _ocr_pdf(data)
            except RuntimeError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not text:
                raise HTTPException(
                    400,
                    "Не удалось распознать текст ни обычным способом, ни через OCR "
                    "(возможно, страницы пустые или качество скана слишком низкое)",
                )
            return {"text": text, "via_ocr": True}

        return {"text": text}

    try:
        return {"text": data.decode("utf-8")}
    except UnicodeDecodeError:
        try:
            return {"text": data.decode("cp1251")}
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "Неподдерживаемый формат файла. Поддерживаются .txt и .pdf") from exc


@router.post("/{platform_id}/disconnect")
def disconnect_platform(platform_id: str):
    if platform_id not in PLATFORM_MAP:
        raise HTTPException(404, "Неизвестная платформа")
    with db_cursor() as cur:
        cur.execute(
            "UPDATE platforms SET connected = 0, credentials = '{}' WHERE id = ?",
            (platform_id,),
        )
        cur.execute("SELECT * FROM platforms WHERE id = ?", (platform_id,))
        row = cur.fetchone()
    return _serialize(row)

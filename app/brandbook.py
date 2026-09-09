"""Разбор брендбука (PDF) на структурированные данные: фирменные цвета, шрифты,
краткое описание визуального стиля/тона.

Цвета извлекаются напрямую регуляркой по HEX-кодам в тексте PDF — это надёжно
и не зависит от ИИ. Шрифты и описание стиля — через уже подключённый
ИИ-провайдер (см. app.ai), так как они формулируются свободным текстом и не
поддаются регулярному разбору для произвольного брендбука.

Извлечённые данные используются в двух местах:
- app.ai_tools (генерация изображений по тексту) — фирменные цвета и стиль
  подмешиваются в промпт для DALL-E;
- app.canva (создание дизайна в Canva) — используется корректный размер холста
  и упоминание палитры в подсказке пользователю (полноценное автоприменение
  брендинга в самой Canva возможно только через Brand Kit/Brand Templates,
  которые настраиваются в интерфейсе Canva вручную — публичный Connect API
  этого не даёт).
"""
from __future__ import annotations

import json
import re

import fitz  # PyMuPDF

from app.ai import AIGenerationError, _generate_text
from app.database import set_setting

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

_SYSTEM_PROMPT = (
    "Ты — ассистент, который извлекает структурированные данные из текста брендбука. "
    "Отвечаешь ТОЛЬКО валидным JSON, без markdown-обёртки и пояснений."
)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _extract_colors(text: str) -> list[str]:
    seen: list[str] = []
    for m in _HEX_RE.findall(text):
        hex_code = m.upper()
        if hex_code not in seen:
            seen.append(hex_code)
    return seen[:12]


def _extract_fonts_and_style(text: str, provider: str | None = None) -> dict:
    user_prompt = f"""Ниже — текст брендбука (может быть на русском). Извлеки из него:
- "fonts": список названий шрифтов, упомянутых как фирменные (обычно в разделе про шрифты/типографику)
- "style_summary": 1-2 предложения о визуальном стиле и тоне бренда (минимализм, настроение, для чего используется дизайн)

Если чего-то нет в тексте — верни пустой список / пустую строку, не придумывай.

Текст брендбука:
{text[:6000]}

Верни JSON-объект с полями "fonts" (массив строк) и "style_summary" (строка). Больше ничего."""

    try:
        raw = _generate_text(_SYSTEM_PROMPT, user_prompt, provider)
        raw = raw.strip()
        raw = re.sub(r"^```(json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
        fonts = [str(f).strip() for f in data.get("fonts", []) if str(f).strip()]
        style_summary = str(data.get("style_summary", "")).strip()
        return {"fonts": fonts, "style_summary": style_summary}
    except Exception:
        # ИИ недоступен/не настроен или вернул неразбираемый ответ — не блокируем
        # загрузку брендбука, просто без шрифтов и описания стиля (цвета уже
        # извлечены регуляркой и не зависят от ИИ).
        return {"fonts": [], "style_summary": ""}


def parse_and_store_brand_book(pdf_bytes: bytes, provider: str | None = None) -> dict:
    text = extract_pdf_text(pdf_bytes)
    colors = _extract_colors(text)
    extra = _extract_fonts_and_style(text, provider)

    set_setting("brand_colors", json.dumps(colors, ensure_ascii=False))
    set_setting("brand_fonts", json.dumps(extra["fonts"], ensure_ascii=False))
    set_setting("brand_visual_style", extra["style_summary"])
    set_setting("brand_book_uploaded", "1")

    return {
        "colors": colors,
        "fonts": extra["fonts"],
        "style_summary": extra["style_summary"],
    }


def get_brand_profile() -> dict:
    from app.database import get_setting

    def _load_json(key: str) -> list:
        raw = get_setting(key)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    return {
        "uploaded": (get_setting("brand_book_uploaded") or "0") == "1",
        "colors": _load_json("brand_colors"),
        "fonts": _load_json("brand_fonts"),
        "style_summary": get_setting("brand_visual_style") or "",
    }


def brand_visual_context() -> str:
    """Короткая текстовая сводка фирменного стиля — подмешивается в промпты
    (генерация изображений, при желании — и текста)."""
    profile = get_brand_profile()
    if not profile["uploaded"]:
        return ""
    parts = []
    if profile["colors"]:
        parts.append(f"Фирменные цвета: {', '.join(profile['colors'])}")
    if profile["fonts"]:
        parts.append(f"Фирменные шрифты: {', '.join(profile['fonts'])}")
    if profile["style_summary"]:
        parts.append(f"Визуальный стиль: {profile['style_summary']}")
    return " | ".join(parts)

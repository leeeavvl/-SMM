"""AI ассистент — набор готовых инструментов-карточек (аналог «AI ассистент» в SMM-планировщиках).

Каждый инструмент описан декларативно в TOOL_DEFS: набор полей ввода + как из них
собрать промпт. Большинство инструментов — обычная текстовая генерация через уже
подключённые провайдеры (Claude/ChatGPT/Gemini/Ollama, см. app.ai). Отдельно
обрабатываются:
- "random_number" — считается на сервере, без обращения к ИИ (это просто случайное
  число, а не то, что стоит доверять языковой модели);
- "image_generate" — генерация изображения по тексту (только через OpenAI, у
  остальных провайдеров в этом проекте нет image-API);
- "image_caption" — описание/текст по изображению (vision-модель OpenAI или Gemini).

Инструмент "Улучшить качество фото" помечен available=False: апскейл изображений —
отдельная задача (super-resolution), ни один из подключённых текстовых/чат-провайдеров
такого не умеет, а притворяться, что кнопка работает, не стоит.
"""
from __future__ import annotations

import base64
import mimetypes
import random
from pathlib import Path
from typing import Any

import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app import brandbook

from app.ai import (
    RUSSIAN_DESLOP_RULES,
    AIConfigError,
    AIGenerationError,
    _generate_text,
    _get_openai_api_key,
    _get_gemini_api_key,
    _strip_long_dashes,
    get_brand_context,
    get_date_context,
    get_openai_model,
    get_gemini_model,
    get_provider,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"

DEFAULT_SYSTEM_PROMPT = (
    "Ты — опытный SMM-специалист и копирайтер. Выполняешь конкретную задачу редактора "
    "по инструкции ниже. Отвечаешь на русском языке, сразу готовым к использованию "
    "текстом — без markdown-заголовков, без вступлений вроде «Вот результат:», без "
    "пояснений до или после.\n\n" + RUSSIAN_DESLOP_RULES
)


class ToolError(RuntimeError):
    """Ошибка валидации входных данных инструмента (не проблема ИИ-провайдера)."""


class ToolField:
    def __init__(self, key: str, label: str, type: str = "text", placeholder: str = "", required: bool = False):
        self.key = key
        self.label = label
        self.type = type  # "text" | "textarea" | "number" | "image"
        self.placeholder = placeholder
        self.required = required

    def to_public(self) -> dict:
        return {"key": self.key, "label": self.label, "type": self.type, "placeholder": self.placeholder, "required": self.required}


class Tool:
    def __init__(
        self,
        id: str,
        category: str,  # "text" | "photo"
        icon: str,
        title: str,
        description: str,
        fields: list[ToolField],
        kind: str = "prompt",  # "prompt" | "random_number" | "image_generate" | "image_caption"
        instruction: str = "",
        system_prompt: str | None = None,
        available: bool = True,
        unavailable_reason: str = "",
    ):
        self.id = id
        self.category = category
        self.icon = icon
        self.title = title
        self.description = description
        self.fields = fields
        self.kind = kind
        self.instruction = instruction
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.available = available
        self.unavailable_reason = unavailable_reason

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "icon": self.icon,
            "title": self.title,
            "description": self.description,
            "fields": [f.to_public() for f in self.fields],
            "kind": self.kind,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


def _f(key: str, label: str, type: str = "text", placeholder: str = "", required: bool = False) -> ToolField:
    return ToolField(key, label, type, placeholder, required)


TOOL_DEFS: list[Tool] = [
    Tool(
        "find_post_idea", "text", "💡", "Найти идею поста",
        "Выберите шаблон, укажите вашу нишу и получите идеи",
        [_f("niche", "Ваша ниша", required=True, placeholder="Например: юридические консультации")],
        instruction="Придумай 7 конкретных идей постов для ниши: {niche}. Для каждой идеи — короткая "
                    "формулировка темы и одна строка, о чём именно пост. Оформи нумерованным списком.",
    ),
    Tool(
        "write_post", "text", "✏️", "Написать пост",
        "Задайте тему поста, ключевые слова и выберите стиль",
        [
            _f("topic", "Тема поста", required=True, placeholder="Например: запуск новой коллекции"),
            _f("keywords", "Ключевые слова", placeholder="через запятую"),
            _f("style", "Стиль", placeholder="нейтральный / продающий / экспертный / дружелюбный"),
        ],
        instruction="Напиши полноценный, готовый к публикации пост на тему: {topic}.\n"
                    "Ключевые слова, которые нужно естественно использовать: {keywords}\n"
                    "Стиль: {style}",
    ),
    Tool(
        "content_plan", "text", "📋", "Создать контент-план",
        "Создайте план постов по определённой тематике",
        [
            _f("niche", "Тематика", required=True, placeholder="Например: карьера в праве"),
            _f("count", "Сколько тем", type="number", placeholder="7"),
        ],
        instruction="Составь контент-план из {count} тем постов для тематики «{niche}» на ближайшие дни. "
                    "Для каждого пункта: день, тема, формат (текст/карусель/видео и т.д.). Список по пунктам.",
    ),
    Tool(
        "chat", "text", "💬", "Чат с ассистентом",
        "Напиши свой запрос в свободной форме и получи уникальный ответ",
        [_f("prompt", "Ваш запрос", type="textarea", required=True)],
        instruction="{prompt}",
        system_prompt=(
            "Ты — полезный ассистент по SMM и контент-маркетингу. Отвечаешь на русском языке, "
            "по существу, без лишних вступлений.\n\n" + RUSSIAN_DESLOP_RULES
        ),
    ),
    Tool(
        "photo_upscale", "photo", "🖼️", "Улучшить качество фото",
        "Откроется в Canva — воспользуйтесь её инструментом Enhance/Upscale и заберите результат обратно",
        [_f("image", "Изображение", type="image", required=True)],
        kind="canva_edit",
        # Апскейл (увеличение без потери качества) — отдельная задача суперразрешения,
        # её не умеет ни один из подключённых текстовых провайдеров (Claude/ChatGPT/
        # Gemini) и её нет в публичном Canva Connect API. Но сама Canva умеет это
        # у себя в редакторе (Enhance photo) — поэтому вместо имитации собственной
        # генерации мы загружаем фото в Canva как asset и открываем редактор, где
        # пользователь применяет реальный апскейл Canva вручную, а мы забираем результат.
    ),
    Tool(
        "rewrite_text", "text", "❄️", "Переписать текст",
        "Выберите стиль рерайта и получите новый, уникальный текст",
        [
            _f("text", "Исходный текст", type="textarea", required=True),
            _f("style", "Стиль рерайта", placeholder="короче / длиннее / более формально / проще"),
        ],
        instruction="Перепиши текст ниже так, чтобы он звучал по-новому (уникально), сохранив смысл. "
                    "Стиль рерайта: {style}\n\nИсходный текст:\n{text}",
    ),
    Tool(
        "video_script", "text", "🖥️", "Сценарии для видео",
        "Получите готовый сценарий для Reels / Shorts / TikTok",
        [
            _f("topic", "Тема видео", required=True),
            _f("platform", "Платформа", placeholder="Reels / Shorts / TikTok"),
        ],
        instruction="Напиши короткий сценарий вертикального видео (15-60 сек) для {platform} на тему: "
                    "{topic}. Раскадровка по секундам: что говорим/показываем, с хуком в первые 2 секунды.",
    ),
    Tool(
        "video_ideas", "text", "▶️", "Идеи для видео",
        "Укажите основную тему и получите 10 идей для отдельных видео",
        [_f("topic", "Основная тема", required=True)],
        instruction="Придумай 10 идей коротких видео на тему: {topic}. Нумерованный список, "
                    "каждая идея — одна строка с сутью ролика.",
    ),
    Tool(
        "stories_games", "text", "🎮", "Идеи игр Stories",
        "Укажите вашу нишу и получите 5 идей для игр в Stories",
        [_f("niche", "Ваша ниша", required=True)],
        instruction="Придумай 5 идей интерактивных игр для Stories (опросы, слайдеры, "
                    "угадайки и т.д.) для ниши: {niche}. Формат: механика + пример вопроса.",
    ),
    Tool(
        "quiz", "text", "❓", "Викторина / квиз",
        "Создайте интересные опросы, чтобы проверить вашу аудиторию",
        [_f("topic", "Тема викторины", required=True)],
        instruction="Составь викторину из 5 вопросов с вариантами ответов на тему: {topic}. "
                    "Для каждого вопроса укажи правильный ответ.",
    ),
    Tool(
        "article_headline", "text", "🔤", "Заголовок статьи",
        "Создайте цепляющий заголовок из вашего черновика",
        [_f("draft", "Черновик или тема", type="textarea", required=True)],
        instruction="Придумай 5 вариантов цепляющего заголовка для статьи на основе: {draft}. "
                    "Нумерованный список, без пояснений.",
    ),
    Tool(
        "article_plan", "text", "🧩", "План статьи",
        "Укажите вашу тему и получите хорошо продуманный план",
        [_f("topic", "Тема статьи", required=True)],
        instruction="Составь структурированный план статьи на тему: {topic}. Заголовки разделов "
                    "и краткое описание, о чём каждый раздел.",
    ),
    Tool(
        "intro_paragraph", "text", "📄", "Вводный абзац",
        "Укажите вашу тему и получите вводный абзац",
        [_f("topic", "Тема", required=True)],
        instruction="Напиши вовлекающий вводный абзац (3-4 предложения) для текста на тему: {topic}.",
    ),
    Tool(
        "several_paragraphs", "text", "📝", "Несколько абзацев",
        "Укажите вашу тему и получите несколько абзацев",
        [
            _f("topic", "Тема", required=True),
            _f("count", "Сколько абзацев", type="number", placeholder="3"),
        ],
        instruction="Напиши {count} связных абзаца на тему: {topic}. Каждый абзац — законченная мысль.",
    ),
    Tool(
        "post_from_draft", "text", "📃", "Пост из черновика",
        "Добавьте ваш черновик и получите полноценную публикацию",
        [_f("draft", "Черновик", type="textarea", required=True)],
        instruction="Доработай черновик ниже до полноценного, готового к публикации поста: сохрани "
                    "основную мысль автора, улучши структуру и подачу.\n\nЧерновик:\n{draft}",
    ),
    Tool(
        "fix_errors", "text", "✅", "Исправить ошибки",
        "Исправьте ошибки в вашем тексте. Орфографические и грамматические",
        [_f("text", "Текст", type="textarea", required=True)],
        instruction="Исправь орфографические и грамматические ошибки в тексте ниже, не меняя стиль и "
                    "смысл. Верни только исправленный текст.\n\n{text}",
    ),
    Tool(
        "final_paragraph", "text", "📄", "Финальный абзац",
        "Завершите публикацию интересно",
        [_f("topic", "Тема/контекст публикации", required=True)],
        instruction="Напиши финальный абзац с призывом к действию для публикации на тему: {topic}.",
    ),
    Tool(
        "post_from_plan", "text", "🧭", "Публикация из плана",
        "Добавьте план и тему публикации, а мы напишем остальное",
        [
            _f("plan", "План публикации", type="textarea", required=True),
            _f("topic", "Тема"),
        ],
        instruction="Напиши полноценный пост на тему «{topic}», строго следуя плану ниже (раскрой "
                    "каждый пункт):\n\n{plan}",
    ),
    Tool(
        "text_takeaway", "text", "💧", "Вывод по тексту",
        "Выделите основную мысль из большого текста",
        [_f("text", "Текст", type="textarea", required=True)],
        instruction="Сформулируй главный вывод (2-3 предложения) из текста ниже.\n\n{text}",
    ),
    Tool(
        "cold_email", "text", "✉️", "Холодное письмо",
        "Получите хорошо написанное письмо, которое дочитают",
        [
            _f("recipient", "Кому / какой оффер", required=True, placeholder="Например: HR-директорам, приглашение на демо"),
            _f("goal", "Цель письма", placeholder="Например: назначить звонок"),
        ],
        instruction="Напиши короткое холодное письмо (email). Кому и что предлагаем: {recipient}. "
                    "Цель письма: {goal}. Тема письма + текст, без канцелярита, с конкретным призывом к действию.",
    ),
    Tool(
        "editor_advice", "text", "🪄", "Совет Главреда",
        "Найдёт слабые места в тексте и подскажет как их можно исправить",
        [_f("text", "Текст", type="textarea", required=True)],
        instruction="Как опытный главный редактор, разбери текст ниже: укажи 3-5 конкретных слабых "
                    "мест и для каждого дай короткую рекомендацию, как исправить.\n\n{text}",
    ),
    Tool(
        "twitter_thread", "text", "🐦", "Twitter тред",
        "Создайте интересную ветку обсуждения на заданную тему",
        [_f("topic", "Тема", required=True)],
        instruction="Напиши тред из 5-7 твитов на тему: {topic}. Каждый твит — до 280 символов, "
                    "пронумеруй их (1/, 2/, ...), первый твит — цепляющий хук.",
    ),
    Tool(
        "emoji_set", "text", "🙂", "Набор эмодзи",
        "Подберите идеально сочетающиеся эмодзи для вашего аккаунта",
        [_f("niche", "Тема / ниша", required=True)],
        instruction="Подбери набор из 10-15 эмодзи, идеально подходящих для тематики: {niche}. "
                    "Просто перечисли эмодзи через пробел, без пояснений.",
    ),
    Tool(
        "highlights_ideas", "text", "✨", "Идеи highlights",
        "Найдите интересные идеи для закреплённых Stories в IG",
        [_f("niche", "Ваша ниша", required=True)],
        instruction="Придумай 6-8 категорий для закреплённых Highlights в Instagram для ниши: {niche}. "
                    "Название категории + emoji-иконка для обложки.",
    ),
    Tool(
        "newsletter_ideas", "text", "📧", "Идеи рассылок",
        "Придумайте категории рассылок, которые обязательно откроет ваша аудитория",
        [_f("niche", "Ваша ниша", required=True)],
        instruction="Придумай 6 категорий email/telegram-рассылок для ниши «{niche}», которые "
                    "обязательно откроет аудитория. Название категории + одна строка сути.",
    ),
    Tool(
        "podcast_questions", "text", "🎙️", "Вопросы для подкаста",
        "Получите вопросы на основе темы и гостя вашего подкаста",
        [
            _f("topic", "Тема выпуска", required=True),
            _f("guest", "Гость", placeholder="Необязательно"),
        ],
        instruction="Составь 8 вопросов для интервью в подкасте. Тема выпуска: {topic}. Гость: {guest}. "
                    "Вопросы от общих к конкретным, последний — на будущее/планы.",
    ),
    Tool(
        "livestream_plan", "text", "📺", "План прямого эфира",
        "Получите продуманную структуру прямого эфира",
        [_f("topic", "Тема эфира", required=True)],
        instruction="Составь структуру прямого эфира на тему: {topic}. Разбей по блокам (разогрев, "
                    "основная часть, ответы на вопросы, призыв к действию) с примерным таймингом.",
    ),
    Tool(
        "tripwire_ideas", "text", "🛒", "Идеи трипваера",
        "Создайте полезный и дешёвый продукт для вашей аудитории, который увеличит продажи",
        [_f("audience", "Ниша / аудитория", required=True)],
        instruction="Придумай 5 идей трипваера (недорогой продукт-подводка к основному) для ниши/"
                    "аудитории: {audience}. Название + суть + примерная цена.",
    ),
    Tool(
        "random_number", "text", "🎲", "Случайное число",
        "Определите победителя с помощью рандомайзера",
        [
            _f("min", "От", type="number", placeholder="1"),
            _f("max", "До", type="number", placeholder="100"),
        ],
        kind="random_number",
    ),
    Tool(
        "ig_bio", "text", "👤", "Описание профиля IG",
        "Получите качественное описание профиля",
        [_f("niche", "Ниша / бренд", required=True)],
        instruction="Напиши описание профиля (bio) для Instagram, до 150 символов, для: {niche}. "
                    "Дай 3 варианта.",
    ),
    Tool(
        "poll_ideas", "text", "📊", "Идеи опросов",
        "Придумайте интересные опросы для аудитории",
        [_f("topic", "Тема", required=True)],
        instruction="Придумай 5 идей опросов для Stories/постов на тему: {topic}. Вопрос + варианты "
                    "ответов для каждого.",
    ),
    Tool(
        "translate_text", "text", "🌐", "Перевод текста",
        "Переведите текст на любой язык",
        [
            _f("text", "Текст", type="textarea", required=True),
            _f("target_lang", "На какой язык", required=True, placeholder="английский"),
        ],
        instruction="Переведи текст ниже на {target_lang}, сохраняя тон и стиль оригинала. Верни "
                    "только перевод.\n\n{text}",
        system_prompt=(
            "Ты — профессиональный переводчик. Переводишь точно, сохраняя тон и стиль оригинала, "
            "без пояснений и комментариев — только готовый перевод."
        ),
    ),
    Tool(
        "stories_warmup", "text", "🔥", "Прогрев для Stories",
        "Помогите вашей аудитории принять решение о покупке",
        [_f("offer", "Тема / оффер", required=True)],
        instruction="Составь последовательность прогрева в Stories (5-6 слайдов) перед предложением: "
                    "{offer}. Для каждого слайда — суть контента и цель (боль, экспертность, "
                    "социальное доказательство, снятие возражений, оффер, дедлайн).",
    ),
    Tool(
        "image_from_text", "photo", "🖼️", "Изображение по тексту",
        "Создайте изображение на основе текста",
        [_f("prompt", "Описание изображения", type="textarea", required=True)],
        kind="image_generate",
    ),
    Tool(
        "text_from_image", "photo", "🔎", "Текст по изображению",
        "Создайте текст на основе изображения",
        [_f("image", "Изображение", type="image", required=True)],
        kind="image_caption",
    ),
]

TOOLS_BY_ID: dict[str, Tool] = {t.id: t for t in TOOL_DEFS}


def list_tools_public() -> list[dict]:
    return [t.to_public() for t in TOOL_DEFS]


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _render(template: str, inputs: dict[str, Any]) -> str:
    return template.format_map(_SafeDict(**{k: (v if v is not None else "") for k, v in inputs.items()}))


def _require(inputs: dict[str, Any], tool: Tool) -> None:
    missing = [f.label for f in tool.fields if f.required and not str(inputs.get(f.key, "")).strip()]
    if missing:
        raise ToolError(f"Заполните обязательные поля: {', '.join(missing)}")


def _local_path_for_upload_url(url: str) -> Path:
    if not url.startswith("/static/uploads/"):
        raise ToolError("Некорректная ссылка на изображение — загрузите файл через форму.")
    path = UPLOAD_DIR / Path(url).name
    if not path.is_file():
        raise ToolError("Изображение не найдено — попробуйте загрузить его заново.")
    return path


def _generate_image_openai(prompt: str) -> dict:
    client = openai.OpenAI(api_key=_get_openai_api_key())
    try:
        response = client.images.generate(model="dall-e-3", prompt=prompt, size="1024x1024", n=1)
    except openai.AuthenticationError as exc:
        raise AIConfigError("API-ключ OpenAI недействителен. Проверьте его в разделе «Настройки».") from exc
    except openai.APIError as exc:
        raise AIGenerationError(f"Ошибка обращения к OpenAI API: {exc}") from exc

    image_url = response.data[0].url
    if not image_url:
        raise AIGenerationError("OpenAI не вернул изображение.")

    # Скачиваем и сохраняем локально, чтобы ссылка не протухала и результат
    # можно было сразу использовать как медиа поста.
    import httpx as _httpx

    img_bytes = _httpx.get(image_url, timeout=60).content
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    import uuid

    filename = f"{uuid.uuid4().hex}.png"
    (UPLOAD_DIR / filename).write_bytes(img_bytes)
    return {"image_url": f"/static/uploads/{filename}"}


def _caption_image_openai(image_path: Path, prompt: str) -> str:
    client = openai.OpenAI(api_key=_get_openai_api_key())
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    try:
        response = client.chat.completions.create(
            model=get_openai_model(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
        )
    except openai.AuthenticationError as exc:
        raise AIConfigError("API-ключ OpenAI недействителен. Проверьте его в разделе «Настройки».") from exc
    except openai.APIError as exc:
        raise AIGenerationError(f"Ошибка обращения к OpenAI API: {exc}") from exc
    return response.choices[0].message.content or ""


def _caption_image_gemini(image_path: Path, prompt: str) -> str:
    client = genai.Client(api_key=_get_gemini_api_key())
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    try:
        response = client.models.generate_content(
            model=get_gemini_model(),
            contents=[genai_types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime), prompt],
        )
    except genai_errors.APIError as exc:
        if exc.code in (401, 403) or "API key not valid" in str(exc) or "API_KEY_INVALID" in str(exc):
            raise AIConfigError("API-ключ Google Gemini недействителен. Проверьте его в разделе «Настройки».") from exc
        raise AIGenerationError(f"Ошибка обращения к Gemini API: {exc}") from exc
    return response.text or ""


def run_tool(tool_id: str, inputs: dict[str, Any], provider: str | None = None) -> dict:
    tool = TOOLS_BY_ID.get(tool_id)
    if not tool:
        raise ToolError(f"Неизвестный инструмент: {tool_id}")
    if not tool.available:
        raise ToolError(tool.unavailable_reason or "Инструмент временно недоступен.")

    _require(inputs, tool)

    if tool.kind == "random_number":
        try:
            lo = int(str(inputs.get("min") or 1))
            hi = int(str(inputs.get("max") or 100))
        except ValueError as exc:
            raise ToolError("«От» и «До» должны быть целыми числами.") from exc
        if lo > hi:
            lo, hi = hi, lo
        return {"result": str(random.randint(lo, hi))}

    if tool.kind == "image_generate":
        prompt = inputs.get("prompt") or _render(tool.instruction, inputs)
        visual_context = brandbook.brand_visual_context()
        if visual_context:
            prompt = f"{prompt}\n\nСоблюдай фирменный стиль бренда: {visual_context}"
        return _generate_image_openai(prompt)

    if tool.kind == "canva_edit":
        raise ToolError(
            "Этот инструмент открывается в Canva — используйте кнопку «Открыть в Canva» "
            "в карточке инструмента, а не обычную генерацию."
        )

    if tool.kind == "image_caption":
        image_path = _local_path_for_upload_url(str(inputs.get("image", "")))
        prompt = (
            "Опиши это изображение и предложи готовый текст поста для соцсетей на его основе. "
            "Ответ на русском языке."
        )
        use_gemini = provider == "gemini" or (not provider and get_provider() == "gemini")
        text = _caption_image_gemini(image_path, prompt) if use_gemini else _caption_image_openai(image_path, prompt)
        return {"result": _strip_long_dashes(text.strip())}

    # kind == "prompt"
    user_prompt = f"{get_brand_context()}\n\n{get_date_context()}\n\n" + _render(tool.instruction, inputs)
    text = _generate_text(tool.system_prompt, user_prompt, provider)
    if not text:
        raise AIGenerationError("Модель не вернула ответ.")
    return {"result": _strip_long_dashes(text.strip())}

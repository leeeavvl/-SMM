"""Генерация постов по теме и техническому заданию.

Поддерживает пять провайдеров:
- anthropic — Claude API (платно, нужен ключ);
- openai — ChatGPT / OpenAI API (платно, нужен ключ; недоступен из РФ);
- gemini — Google Gemini API (бесплатный уровень; недоступен из РФ);
- gigachat — GigaChat API от Сбера (доступен из РФ, есть бесплатный лимит
  токенов для физлиц-разработчиков);
- ollama — локальная модель через Ollama (бесплатно, работает на компьютере пользователя).

Если выбрано несколько платформ, для каждой из них делается отдельный запрос к модели,
со своим ТЗ и ссылкой на канал — посты не смешиваются между платформами.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import uuid

import anthropic
import httpx
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.database import DEFAULT_BRAND_DESCRIPTION, DEFAULT_BRAND_NAME, db_cursor, get_setting, set_setting
from app.platforms import PLATFORM_MAP

ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_GIGACHAT_MODEL = "GigaChat"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_BASE = "https://gigachat.devices.sberbank.ru/api/v1"


class AIConfigError(RuntimeError):
    pass


class AIGenerationError(RuntimeError):
    pass


def get_brand_context() -> str:
    name = get_setting("brand_name") or DEFAULT_BRAND_NAME
    description = get_setting("brand_description") or DEFAULT_BRAND_DESCRIPTION
    return f"О компании (учитывай это в КАЖДОМ посте, независимо от темы и платформы): {name} — {description}"


def get_date_context() -> str:
    today = dt.date.today()
    return (
        f"Сегодняшняя дата: {today.isoformat()} (год {today.year}). "
        f"Если упоминаешь год, дату или временной контекст ('в этом году', 'недавно', 'сейчас') — "
        f"ориентируйся ИМЕННО на {today.year} год, а не на более ранние годы."
    )


def get_knowledge_context(limit: int = 25, max_chars_each: int = 800) -> str:
    """Собирает загруженные материалы и накопленные идеи улучшения из «Базы» —
    подмешивается в промпт генерации, чтобы контент со временем становился лучше."""
    with db_cursor() as cur:
        cur.execute("SELECT title, content, source FROM knowledge_base ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
    if not rows:
        return ""
    lines = []
    for r in rows:
        label = "Идея по улучшению" if r["source"] == "idea" else "Материал базы знаний"
        title = f" «{r['title']}»" if r["title"] else ""
        text = r["content"][:max_chars_each]
        lines.append(f"- {label}{title}: {text}")
    return (
        "Дополнительные материалы и накопленные идеи по улучшению контента "
        "(учитывай при написании, если релевантно теме):\n" + "\n".join(lines)
    )


RUSSIAN_DESLOP_RULES = """ПРАВИЛА СТИЛЯ ДЛЯ РУССКОГО ТЕКСТА (обязательны для КАЖДОГО поста — убирают «ИИ-душок»):

СТОП-СЛОВА И ОБОРОТЫ — вырезать всегда, без исключений:
в современном мире, в наши дни, в эпоху цифровизации, не секрет что, важно отметить,
стоит отметить, следует подчеркнуть, нельзя не сказать, давайте разберёмся, давайте посмотрим,
погрузимся в, окунёмся в, играет важную роль, является неотъемлемой частью, представляет собой,
ключевой, решающий, значимый, уникальный, инновационный, эффективный, таким образом, более того,
помимо этого, следовательно, итак, в заключение, в целом можно сказать, подводя итог,
надеюсь, эта статья/пост была полезна.

СТРУКТУРЫ — запрещены полностью:
1. Конструкция «не X, а Y» в любом виде, включая «это не X. это Y.».
2. Ряды коротких рубленых предложений подряд ради драматичности.
3. Длинное и среднее тире (—, –) — используй только короткое тире, двоеточие, запятую или точку.
4. Канцелярские отглагольные существительные там, где есть простой глагол:
   не «осуществить проверку», а «проверить»; не «принять решение», а «решить».
5. Пассив с «является»/«осуществляется» — пиши «это» и живой глагол с реальным субъектом действия.
6. Три однородных члена подряд ради ритма, если третий не добавляет смысла.

ЧТО ДЕЛАТЬ ВМЕСТО ЭТОГО:
- Активный залог: у каждого предложения — живой субъект, который что-то делает.
- Конкретика вместо общих слов (не «показатели выросли», а на сколько именно).
- Разная длина предложений, без искусственной рубки на короткие обрубки.
- Никаких новых фактов, цифр или имён, которых не было в исходном ТЗ/теме — не додумывай.

ЧТО СОХРАНЯТЬ (это НЕ признак ИИ-текста для русского языка, не убирай):
кавычки-ёлочки («»), наречия в нормальном количестве, длинные сложные предложения,
если они читаются на одном дыхании.

СТРУКТУРА ПОСТА — соблюдай для каждого текста:
1. Первая строка — хук, который цепляет за 1-2 секунды: конкретный вопрос к читателю,
   неожиданный факт, узнаваемая боль или прямое обращение. Никогда не начинай с темы
   дословно («Сегодня поговорим о...») и не начинай с общих фраз о важности темы.
2. Основная часть разбита на короткие смысловые блоки (2-4 строки), между ними —
   пустая строка: текст должен легко читаться с телефона, не сплошной стеной.
3. Внутри блоков — конкретика: цифры, примеры, шаги, имена, а не общие рассуждения
   «в целом» и «как правило».
4. Финал — явный, конкретный призыв к действию, соответствующий цели поста (написать
   в директ, оставить заявку, ответить в комментариях и т.д.), а не шаблонное
   «надеюсь, было полезно» или «делитесь своим мнением в комментариях» без причины.

ЖИВОЙ ГОЛОС:
- Обращайся к читателю напрямую («вы» или «ты» — по тону бренда), как к конкретному
  человеку, а не к абстрактной аудитории.
- Один-два разговорных элемента на пост уместны: риторический вопрос, лёгкая ирония,
  личный опыт или наблюдение от лица бренда — если подходит по тону.
- Не бойся начинать предложение с «И», «Но», «А» — так говорят живые люди.
- Пост должен звучать как живая реплика человека, которому есть что сказать, а не
  как обобщённая справка или лекция.

Перед тем как вернуть текст — мысленно проверь: нет ли в нём длинного/среднего тире,
нет ли стоп-слов из списка выше, не появилось ли фактов, которых не было в задании,
цепляет ли первая строка, разбит ли текст на читаемые блоки, и есть ли конкретный
призыв к действию в конце."""


def get_provider() -> str:
    return get_setting("ai_provider") or "anthropic"


def get_openai_model() -> str:
    return get_setting("openai_model") or DEFAULT_OPENAI_MODEL


def get_gemini_model() -> str:
    return get_setting("gemini_model") or DEFAULT_GEMINI_MODEL


def get_gigachat_model() -> str:
    return get_setting("gigachat_model") or DEFAULT_GIGACHAT_MODEL


def get_ollama_model() -> str:
    return get_setting("ollama_model") or DEFAULT_OLLAMA_MODEL


def get_ollama_base_url() -> str:
    return (get_setting("ollama_base_url") or DEFAULT_OLLAMA_URL).rstrip("/")


def _get_anthropic_api_key() -> str:
    key = get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AIConfigError(
            "API-ключ Anthropic не настроен. Добавьте его в разделе «Настройки» "
            "или переключитесь на локальную генерацию через Ollama."
        )
    return key


def _get_openai_api_key() -> str:
    key = get_setting("openai_api_key") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise AIConfigError(
            "API-ключ OpenAI не настроен. Добавьте его в разделе «Настройки» "
            "или переключитесь на другого провайдера."
        )
    return key


def _get_gemini_api_key() -> str:
    key = get_setting("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise AIConfigError(
            "API-ключ Google Gemini не настроен. Получите бесплатный ключ на ai.google.dev "
            "и добавьте его в разделе «Настройки»."
        )
    return key


def _get_gigachat_auth_key() -> str:
    key = get_setting("gigachat_auth_key") or os.environ.get("GIGACHAT_AUTH_KEY")
    if not key:
        raise AIConfigError(
            "Authorization key GigaChat не настроен. Получите его на developers.sber.ru "
            "(создать проект → GigaChat API → получить Authorization key) и добавьте "
            "в разделе «Настройки»."
        )
    return key


def _get_gigachat_token() -> str:
    """GigaChat выдаёт Bearer-токен максимум на 30 минут по Authorization key
    (Basic-заголовок) — кэшируем токен в settings и обновляем только когда истёк."""
    cached_token = get_setting("gigachat_access_token")
    expires_at = int(get_setting("gigachat_token_expires_at") or 0)
    if cached_token and time.time() < expires_at:
        return cached_token

    auth_key = _get_gigachat_auth_key()
    try:
        resp = httpx.post(
            GIGACHAT_OAUTH_URL,
            headers={
                "Authorization": f"Basic {auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"scope": "GIGACHAT_API_PERS"},
            timeout=30,
            # У GigaChat самоподписанный сертификат от Минцифры РФ, который
            # обычно не входит в системное хранилище доверенных корневых
            # сертификатов — без этого запрос падает с SSL-ошибкой.
            verify=False,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise AIConfigError(
                "Authorization key GigaChat недействителен. Проверьте его в разделе «Настройки»."
            ) from exc
        raise AIGenerationError(f"GigaChat: не удалось получить токен: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise AIGenerationError(f"GigaChat: ошибка соединения: {exc}") from exc

    data = resp.json()
    token = data["access_token"]
    # expires_at в ответе GigaChat — unix-время в миллисекундах.
    expires_at_ms = int(data.get("expires_at", 0))
    set_setting("gigachat_access_token", token)
    set_setting("gigachat_token_expires_at", str(expires_at_ms // 1000 - 60 if expires_at_ms else int(time.time()) + 1500))
    return token


def _generate_gigachat(system_prompt: str, user_prompt: str) -> str:
    token = _get_gigachat_token()
    model = get_gigachat_model()
    try:
        resp = httpx.post(
            f"{GIGACHAT_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=120,
            verify=False,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            # Токен мог протухнуть раньше срока — сбрасываем кэш, чтобы
            # следующий запрос запросил новый.
            set_setting("gigachat_access_token", "")
            raise AIConfigError(
                "Сессия GigaChat истекла или ключ недействителен. Проверьте Authorization key в «Настройках»."
            ) from exc
        raise AIGenerationError(f"Ошибка обращения к GigaChat API: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise AIGenerationError(f"Ошибка соединения с GigaChat: {exc}") from exc

    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    return text.strip()


def _extract_json(text: str) -> list[dict]:
    """Извлекает JSON-МАССИВ из ответа модели (для случаев, где ожидается список объектов)."""
    text = _strip_code_fence(text)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise AIGenerationError("Модель вернула данные в неожиданном формате")
    return data


def _extract_json_object(text: str) -> dict:
    """Извлекает JSON-ОБЪЕКТ из ответа модели (для случаев, где ожидается один объект,
    даже если внутри него есть свои массивы — в отличие от _extract_json не хватает
    вложенный массив по ошибке)."""
    text = _strip_code_fence(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise AIGenerationError("Модель вернула данные в неожиданном формате")
    return data


def _get_platform_context(platform_id: str) -> dict:
    with db_cursor() as cur:
        cur.execute("SELECT brief, channel_url FROM platforms WHERE id = ?", (platform_id,))
        row = cur.fetchone()
    return {
        "id": platform_id,
        "name": PLATFORM_MAP[platform_id].name,
        "brief": (row["brief"] if row else "") or "",
        "channel_url": (row["channel_url"] if row else "") or "",
    }


def _build_prompts_for_platform(
    topic: str,
    brief: str,
    tone: str,
    platform: dict | None,
    variants: int,
    length: int | None = None,
) -> tuple[str, str]:
    system_prompt = (
        "Ты — опытный контент-маркетолог и SMM-копирайтер. Пишешь посты на русском языке "
        "строго по заданной теме и техническому заданию. Отвечаешь ТОЛЬКО валидным JSON без "
        "markdown-обёртки, без пояснений до или после.\n\n" + RUSSIAN_DESLOP_RULES
    )

    if platform:
        platform_block = f"Целевая платформа: {platform['name']}\n"
        if platform["channel_url"]:
            platform_block += f"Канал/страница публикации: {platform['channel_url']}\n"
        if platform["brief"]:
            platform_block += (
                f"Индивидуальное техническое задание для платформы «{platform['name']}» "
                f"(строго следуй ему — формат, длина, тон, структура, что избегать):\n{platform['brief']}\n"
            )
        else:
            platform_block += (
                "Отдельного ТЗ для платформы не задано — ориентируйся на её типичный формат "
                "(ограничения по длине, стиль подачи, уместность хэштегов и эмодзи).\n"
            )
    else:
        platform_block = "Платформа не указана — пиши универсальный пост без привязки к конкретной сети.\n"

    if length:
        length_instruction = (
            f"Объём текста поста ('body'): строго около {length} знаков (допустимо отклонение ±10%). "
            f"Это требование по длине задано пользователем вручную и ИМЕЕТ ПРИОРИТЕТ над любыми "
            f"рекомендациями по длине из ТЗ платформы или общих правил формата — ориентируйся на "
            f"остальные указания ТЗ (тон, структура, стиль), но объём соблюдай строго {length} знаков."
        )
    else:
        length_instruction = "Объём текста — на усмотрение, ориентируйся на рекомендации ТЗ платформы и формат сети."

    user_prompt = f"""{get_brand_context()}

{get_date_context()}

{get_knowledge_context()}

Тема поста: {topic}

Общее техническое задание (бриф): {brief or "не указано, ориентируйся только на тему"}

Тон коммуникации: {tone}

{platform_block}
{length_instruction}

Сгенерируй {variants} вариант(а) поста именно для этой платформы, строго следуя её
индивидуальному ТЗ выше (если оно задано) — оно имеет приоритет над общими рекомендациями,
за исключением объёма текста, если он задан явно выше.

Придумай сильный хук для первой строки именно под эту тему и аудиторию, разбей текст
на читаемые блоки с пустыми строками между ними и закончи конкретным призывом к действию —
это обязательные требования из правил стиля выше, а не пожелание.

Верни JSON-массив из {variants} объектов. Каждый объект должен иметь поля:
- "title": короткий заголовок/тема поста
- "body": полный текст поста, готовый к публикации
- "tags": релевантные теги через запятую (без символа #)

Верни только JSON-массив, ничего больше."""

    return system_prompt, user_prompt


def _strip_long_dashes(text: str) -> str:
    """Механически заменяет длинное (—) и среднее (–) тире на короткое (-).

    Модели, особенно небольшие локальные, не всегда соблюдают запрет на длинное
    тире из RUSSIAN_DESLOP_RULES — эта замена гарантирует результат независимо
    от того, насколько хорошо модель следует инструкции.
    """
    return text.replace("—", "-").replace("–", "-")


def _parse_variants(text: str) -> list[dict]:
    if not text:
        raise AIGenerationError("Модель не вернула текстовый ответ")
    try:
        items = _extract_json(text)
    except json.JSONDecodeError as exc:
        raise AIGenerationError(f"Не удалось разобрать ответ модели: {exc}") from exc

    results = []
    for item in items:
        results.append(
            {
                "title": _strip_long_dashes(str(item.get("title", "")).strip()),
                "body": _strip_long_dashes(str(item.get("body", "")).strip()),
                "tags": str(item.get("tags", "")).strip(),
            }
        )
    return results


def _generate_anthropic(system_prompt: str, user_prompt: str) -> str:
    client = anthropic.Anthropic(api_key=_get_anthropic_api_key())
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=3000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIError as exc:
        raise AIGenerationError(f"Ошибка обращения к Anthropic API: {exc}") from exc

    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")


def _generate_openai(system_prompt: str, user_prompt: str) -> str:
    client = openai.OpenAI(api_key=_get_openai_api_key())
    model = get_openai_model()
    try:
        # Без response_format: некоторые запросы (варианты постов) ожидают JSON-МАССИВ
        # верхнего уровня, а json_object режим OpenAI гарантирует только JSON-объект —
        # полагаемся на явную инструкцию в system_prompt, как и для Anthropic/Ollama.
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except openai.AuthenticationError as exc:
        raise AIConfigError(
            "API-ключ OpenAI недействителен. Проверьте его в разделе «Настройки»."
        ) from exc
    except openai.NotFoundError as exc:
        raise AIConfigError(
            f"Модель «{model}» недоступна для вашего аккаунта OpenAI. Укажите другую модель в «Настройках»."
        ) from exc
    except openai.APIError as exc:
        raise AIGenerationError(f"Ошибка обращения к OpenAI API: {exc}") from exc

    return response.choices[0].message.content or ""


def _generate_gemini(system_prompt: str, user_prompt: str) -> str:
    client = genai.Client(api_key=_get_gemini_api_key())
    model_name = get_gemini_model()
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=user_prompt,
            config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
        )
    except genai_errors.APIError as exc:
        # Google возвращает невалидный ключ как 400 INVALID_ARGUMENT (а не 401/403),
        # поэтому дополнительно проверяем текст ошибки на упоминание API-ключа.
        if exc.code in (401, 403) or "API key not valid" in str(exc) or "API_KEY_INVALID" in str(exc):
            raise AIConfigError(
                "API-ключ Google Gemini недействителен. Проверьте его в разделе «Настройки»."
            ) from exc
        if exc.code == 404:
            raise AIConfigError(
                f"Модель «{model_name}» недоступна. Укажите другую модель Gemini в «Настройках»."
            ) from exc
        if exc.code == 429:
            raise AIGenerationError(
                "Превышен бесплатный лимит запросов Gemini на сегодня. Попробуйте позже "
                "или переключитесь на другого провайдера."
            ) from exc
        raise AIGenerationError(f"Ошибка обращения к Gemini API: {exc}") from exc

    return response.text or ""


def _generate_ollama(system_prompt: str, user_prompt: str) -> str:
    base_url = get_ollama_base_url()
    model = get_ollama_model()
    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": "json",
            },
            timeout=180,
        )
        resp.raise_for_status()
    except httpx.ConnectError as exc:
        raise AIConfigError(
            f"Не удалось подключиться к Ollama на {base_url}. Установите Ollama (ollama.com), "
            f"запустите её и выполните `ollama pull {model}`."
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise AIConfigError(
                f"Модель «{model}» не найдена в Ollama. Выполните `ollama pull {model}` и повторите."
            ) from exc
        raise AIGenerationError(f"Ошибка обращения к Ollama: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise AIGenerationError("Ollama слишком долго отвечает (таймаут). Попробуйте модель поменьше.") from exc

    data = resp.json()
    return (data.get("message") or {}).get("content", "")


VALID_PROVIDERS = {"anthropic", "openai", "gemini", "gigachat", "ollama"}


def _generate_text(system_prompt: str, user_prompt: str, provider: str | None = None) -> str:
    provider = provider if provider in VALID_PROVIDERS else get_provider()
    if provider == "ollama":
        return _generate_ollama(system_prompt, user_prompt)
    if provider == "openai":
        return _generate_openai(system_prompt, user_prompt)
    if provider == "gemini":
        return _generate_gemini(system_prompt, user_prompt)
    if provider == "gigachat":
        return _generate_gigachat(system_prompt, user_prompt)
    return _generate_anthropic(system_prompt, user_prompt)


def generate_posts(
    topic: str,
    brief: str,
    tone: str,
    platform_ids: list[str],
    variants: int = 1,
    length: int | None = None,
    provider: str | None = None,
) -> tuple[list[dict], list[str]]:
    variants = max(1, min(variants, 3))

    if not platform_ids:
        system_prompt, user_prompt = _build_prompts_for_platform(topic, brief, tone, None, variants, length)
        return _parse_variants(_generate_text(system_prompt, user_prompt, provider)), []

    results: list[dict] = []
    errors: list[str] = []
    for platform_id in platform_ids:
        if platform_id not in PLATFORM_MAP:
            continue
        platform = _get_platform_context(platform_id)
        system_prompt, user_prompt = _build_prompts_for_platform(topic, brief, tone, platform, variants, length)
        try:
            text = _generate_text(system_prompt, user_prompt, provider)
            for item in _parse_variants(text):
                item["platform_id"] = platform_id
                item["platform_name"] = platform["name"]
                results.append(item)
        except AIGenerationError as exc:
            errors.append(f"{platform['name']}: {exc}")

    if not results and errors:
        raise AIGenerationError("; ".join(errors))
    return results, errors


_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힣]")
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")
_JUNK_MARKERS_RE = re.compile(r"```|\{|\}|\bjson\b", re.IGNORECASE)


def _is_valid_topic(topic: str) -> bool:
    """Отбраковывает мусорный ответ модели: смешение с китайскими/корейскими/японскими
    символами, обрывки JSON/markdown-разметки, слишком длинный "рамблинг" вместо
    короткой темы, или текст без преобладания кириллицы."""
    if not topic:
        return False
    if len(topic) > 200:
        return False
    if _CJK_RE.search(topic):
        return False
    if _JUNK_MARKERS_RE.search(topic):
        return False
    cyrillic_count = len(_CYRILLIC_RE.findall(topic))
    latin_count = len(_LATIN_RE.findall(topic))
    # Тема должна быть преимущественно на русском — латиница допустима только
    # для отдельных терминов/аббревиатур, но не должна преобладать.
    if cyrillic_count == 0:
        return False
    if latin_count > cyrillic_count:
        return False
    return True


def _extract_plan_items(text: str) -> list[dict]:
    if not text:
        raise AIGenerationError("Модель не вернула текстовый ответ")
    try:
        item = _extract_json_object(text)
    except json.JSONDecodeError as exc:
        raise AIGenerationError(f"Не удалось разобрать ответ модели: {exc}") from exc
    try:
        day_offset = int(item.get("day_offset", 0))
    except (TypeError, ValueError):
        day_offset = 0
    topic = str(item.get("topic", "")).strip()
    if not _is_valid_topic(topic):
        raise AIGenerationError("Модель вернула некорректную (нечитаемую или не на русском) тему")
    results = [
        {
            "day_offset": max(0, day_offset),
            "topic": topic,
            "format_hint": str(item.get("format_hint", "")).strip(),
        }
    ]
    return results


_PLAN_SYSTEM_PROMPT = (
    "Ты — контент-стратег и SMM-планировщик. Придумываешь темы постов на русском языке. "
    "Отвечаешь ТОЛЬКО валидным JSON без markdown-обёртки и пояснений."
)


def _plan_row_prompt(
    tone: str,
    platform: dict,
    brief_block: str,
    direction: str,
    content_type: str,
    fmt: str,
    used_topics: list[str],
) -> str:
    used_block = (
        "Уже запланированные темы этой же комбинации направление/контент/формат "
        "(НЕ повторяй их и не пиши близкие по смыслу):\n" + "\n".join(f"- {t}" for t in used_topics)
        if used_topics
        else "Тем в этой комбинации ещё не было — это первый пост такого типа."
    )
    return f"""{get_brand_context()}

{get_date_context()}

{get_knowledge_context()}

Платформа: {platform['name']}
{brief_block}
Тон коммуникации: {tone}

Параметры этого поста (заданы пользователем, обязательны к соблюдению):
- Направление/рубрика: {direction}
- Тип контента: {content_type}
- Формат публикации: {fmt}

{used_block}

Придумай ОДНУ конкретную тему поста, которая точно соответствует направлению «{direction}»,
типу контента «{content_type}» и формату «{fmt}» — не общими словами, а готовую формулировку,
которую можно сразу использовать как тему для написания текста.

Верни ОДИН JSON-объект (не массив) с полем:
- "topic": тема поста

Верни только JSON-объект, ничего больше."""


def generate_plan(
    platform_id: str,
    rows: list[dict],
    period_days: int,
    tone: str,
) -> tuple[list[dict], list[str]]:
    """rows: [{"direction","content_type","format","quantity"}, ...]

    Гарантирует, что КАЖДЫЙ день периода получит хотя бы один пост: сначала
    строки распределяются по своему заданному количеству (равномерно на весь
    период, включая последний день), а любые оставшиеся пустые дни
    дозаполняются циклическим перебором тех же строк.
    """
    period_days = max(1, min(period_days, 60))

    if platform_id not in PLATFORM_MAP:
        raise AIGenerationError(f"Неизвестная платформа: {platform_id}")
    if not rows:
        raise AIGenerationError("Не задано ни одной строки плана")

    platform = _get_platform_context(platform_id)
    brief_block = (
        f"Техническое задание платформы «{platform['name']}» (используй его рубрики/форматы "
        f"как основу для тем):\n{platform['brief']}\n"
        if platform["brief"]
        else f"Отдельного ТЗ для платформы «{platform['name']}» не задано — придумай темы, "
        f"уместные для формата этой площадки.\n"
    )

    normalized_rows = [
        {
            "direction": r["direction"],
            "content_type": r["content_type"],
            "format": r["format"],
            "quantity": max(1, min(r["quantity"], 30)),
        }
        for r in rows
    ]

    # Шаг 1: раскладываем каждую строку по её количеству равномерно по всему периоду.
    day_plan: dict[int, list[dict]] = {d: [] for d in range(period_days)}
    for row in normalized_rows:
        q = row["quantity"]
        if q > period_days:
            offsets = [i % period_days for i in range(q)]
        elif q == 1:
            offsets = [0]
        else:
            offsets = [round(i * (period_days - 1) / (q - 1)) for i in range(q)]
        for d in offsets:
            day_plan[d].append(row)

    # Шаг 2: дозаполняем пустые дни, циклически перебирая заданные строки.
    empty_days = [d for d in range(period_days) if not day_plan[d]]
    for i, d in enumerate(empty_days):
        day_plan[d].append(normalized_rows[i % len(normalized_rows)])

    # Шаг 3: генерируем по одной теме на каждое (день, строка)-назначение по порядку дней.
    results: list[dict] = []
    errors: list[str] = []
    used_topics_by_combo: dict[tuple[str, str, str], list[str]] = {}

    for day_offset in range(period_days):
        for row in day_plan[day_offset]:
            combo_key = (row["direction"], row["content_type"], row["format"])
            used_topics = used_topics_by_combo.setdefault(combo_key, [])
            user_prompt = _plan_row_prompt(
                tone, platform, brief_block, row["direction"], row["content_type"], row["format"], used_topics
            )
            # До 3 попыток: небольшие локальные модели иногда возвращают "рамблинг"
            # вместо чистой темы (смесь языков, обрывки JSON) — такой ответ отбраковывается
            # в _extract_plan_items, и мы просто пробуем сгенерировать заново, а не
            # сохраняем мусор как есть.
            last_exc: AIGenerationError | None = None
            topic = None
            for attempt in range(3):
                try:
                    text = _generate_text(_PLAN_SYSTEM_PROMPT, user_prompt)
                    items = _extract_plan_items(text)
                    if not items:
                        raise AIGenerationError("Модель не вернула тему")
                    topic = items[0]["topic"]
                    last_exc = None
                    break
                except AIGenerationError as exc:
                    last_exc = exc
                    continue

            if topic:
                results.append(
                    {
                        "day_offset": day_offset,
                        "topic": topic,
                        "platform_id": platform_id,
                        "platform_name": platform["name"],
                        "direction": row["direction"],
                        "content_type": row["content_type"],
                        "format": row["format"],
                    }
                )
                used_topics.append(topic)
            elif last_exc:
                errors.append(f"{combo_key[0]}/{combo_key[1]}/{combo_key[2]} (день {day_offset}): {last_exc}")

    if not results and errors:
        raise AIGenerationError("; ".join(errors))
    return results, errors


_ANALYSIS_SYSTEM_PROMPT = (
    "Ты — опытный редактор и SMM-критик. Оцениваешь готовые посты и даёшь честную, "
    "конкретную обратную связь на русском языке. Отвечаешь ТОЛЬКО валидным JSON без "
    "markdown-обёртки и пояснений."
)


def analyze_post(
    title: str,
    body: str,
    platform_id: str | None = None,
    stats: list[dict] | None = None,
) -> dict:
    """Оценивает готовый пост: балл релевантности (0-100), краткий отзыв и идеи по улучшению.

    stats: список {"platform_id","platform_name","views","likes","comments"} по каждой
    публикации этого поста, для которой есть реальные метрики. Если есть хотя бы одна
    запись — оценка строится в первую очередь на РЕАЛЬНОМ результате, а не только на
    тексте.
    """
    platform_block = ""
    if platform_id and platform_id in PLATFORM_MAP:
        platform = _get_platform_context(platform_id)
        platform_block = f"Платформа: {platform['name']}\n"
        if platform["brief"]:
            platform_block += f"ТЗ платформы (оценивай соответствие ему):\n{platform['brief']}\n"

    stats = [s for s in (stats or []) if s.get("views") is not None or s.get("likes") is not None or s.get("comments") is not None]
    if stats:
        stats_lines = []
        for s in stats:
            parts = []
            if s.get("views") is not None:
                parts.append(f"просмотры: {s['views']}")
            if s.get("likes") is not None:
                parts.append(f"лайки: {s['likes']}")
            if s.get("comments") is not None:
                parts.append(f"комментарии: {s['comments']}")
            stats_lines.append(f"- {s.get('platform_name', s.get('platform_id', ''))}: " + ", ".join(parts))
        stats_block = (
            "РЕАЛЬНЫЕ МЕТРИКИ ЭФФЕКТИВНОСТИ этого поста после публикации:\n"
            + "\n".join(stats_lines)
            + "\n\nЭТО ГЛАВНЫЙ КРИТЕРИЙ ОЦЕНКИ. Балл должен в первую очередь отражать реальный результат "
            "(много просмотров/лайков/комментариев — высокий балл, даже если текст не идеален; мало "
            "просмотров/вовлечения — низкий балл, даже если текст хорошо написан). Качество текста — "
            "второстепенный фактор, объясняющий ПОЧЕМУ результат такой. В обратной связи явно свяжи "
            "оценку с цифрами (например: «низкий CTR/вовлечение при таком-то заголовке» или «высокая "
            "вовлечённость благодаря конкретному приёму»), а идеи по улучшению должны быть направлены "
            "на рост просмотров/лайков/комментариев в будущих постах."
        )
    else:
        stats_block = (
            "Реальных метрик по этому посту пока нет (не опубликован или статистика ещё не подтянута). "
            "Оценивай ТОЛЬКО на основе качества текста: релевантность теме/бренду/аудитории, соответствие "
            "ТЗ платформы, структура, сила заголовка/хука, наличие и конкретность призыва к действию, "
            "оригинальность (не общие фразы), наличие «ИИ-штампов». В обратной связи явно укажи, что оценка "
            "основана на содержании, а не на реальных результатах.\n\n"
            "ВАЖНО ПРО ШКАЛУ ОЦЕНКИ: не ставь большинству постов «безопасную» оценку в диапазоне 80-90 — "
            "используй по-настоящему всю шкалу 0-100 и будь строгим критиком, а не вежливым помощником. "
            "Ориентируйся на диапазоны:\n"
            "- 90-100: исключительный пост, почти не к чему придраться, готов к публикации как есть\n"
            "- 70-89: хороший пост, но есть 2-3 конкретных, реально значимых недостатка\n"
            "- 50-69: средний пост — слабый хук, шаблонная структура, размытый призыв к действию или "
            "заметные ИИ-штампы\n"
            "- 30-49: посредственный пост с серьёзными проблемами (нерелевантен теме/бренду, нет структуры, "
            "не соответствует ТЗ платформы)\n"
            "- 0-29: пост не выполняет свою задачу\n"
            "Если два поста получают в целом одинаковый разбор — они не должны получать одинаковый балл: "
            "различай их по мелким, но реальным деталям текста."
        )

    user_prompt = f"""{get_brand_context()}

{platform_block}
Заголовок поста: {title}

Текст поста:
{body}

{stats_block}

Дополнительно проверь текст на «ИИ-штампы» по этому чек-листу и, если найдёшь нарушения,
явно укажи их в обратной связи и добавь конкретную идею-исправление:
{RUSSIAN_DESLOP_RULES}

Дай краткую обратную связь (2-4 предложения) и 2-4 конкретные, применимые идеи по улучшению
(не общие фразы вроде «пишите лучше», а конкретные советы, применимые к ЭТОМУ посту или
к похожим постам в будущем — если есть реальные метрики, идеи должны объяснять, как повысить
просмотры/вовлечение).

Верни JSON-объект с полями:
- "score": число от 0 до 100
- "feedback": краткий отзыв
- "suggestions": массив строк — конкретные идеи по улучшению

Верни только JSON-объект, ничего больше."""

    text = _generate_text(_ANALYSIS_SYSTEM_PROMPT, user_prompt)
    if not text:
        raise AIGenerationError("Модель не вернула текстовый ответ")
    try:
        item = _extract_json_object(text)
    except json.JSONDecodeError as exc:
        raise AIGenerationError(f"Не удалось разобрать ответ модели: {exc}") from exc

    try:
        score = int(item.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    feedback = str(item.get("feedback", "")).strip()
    suggestions_raw = item.get("suggestions", [])
    if isinstance(suggestions_raw, str):
        suggestions = [s.strip("-• ").strip() for s in suggestions_raw.splitlines() if s.strip()]
    else:
        suggestions = [str(s).strip() for s in suggestions_raw if str(s).strip()]

    return {"score": score, "feedback": feedback, "suggestions": suggestions}

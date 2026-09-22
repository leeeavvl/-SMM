"""Генерация постов по теме и техническому заданию.

Поддерживает шесть провайдеров:
- anthropic — Claude API (платно, нужен ключ);
- openai — ChatGPT / OpenAI API (платно, нужен ключ; недоступен из РФ);
- gemini — Google Gemini API (бесплатный уровень; недоступен из РФ);
- gigachat — GigaChat API от Сбера (доступен из РФ, есть бесплатный лимит
  токенов для физлиц-разработчиков);
- deepseek — DeepSeek API (китайский провайдер, платно но недорого, доступен
  из РФ — не подпадает под западные санкционные ограничения);
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
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_BASE = "https://gigachat.devices.sberbank.ru/api/v1"
DEEPSEEK_API_BASE = "https://api.deepseek.com"


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


_SALES_ENTRY_RE = re.compile(r"продающ|воронк[аи] продаж", re.IGNORECASE)


def get_knowledge_context(limit: int = 100, max_chars_each: int = 600, include_sales: bool = True) -> str:
    """Собирает загруженные материалы и накопленные идеи улучшения из «Базы» —
    подмешивается в промпт генерации, чтобы контент со временем становился лучше.

    include_sales=False отфильтровывает материалы про схемы/формулы продающих
    постов (AIDA, PAS и т.п.) и воронку продаж — без этого они подмешивались
    в АБСОЛЮТНО ЛЮБОЙ пост (в т.ч. информационный, развлекательный), из-за чего
    даже нейтральные посты получались навязчиво-рекламными."""
    with db_cursor() as cur:
        cur.execute("SELECT title, content, source FROM knowledge_base ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
    if not rows:
        return ""
    lines = []
    for r in rows:
        if not include_sales and _SALES_ENTRY_RE.search(r["title"] or ""):
            continue
        label = "Идея по улучшению" if r["source"] == "idea" else "Материал базы знаний"
        title = f" «{r['title']}»" if r["title"] else ""
        text = r["content"][:max_chars_each]
        lines.append(f"- {label}{title}: {text}")
    if not lines:
        return ""
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

ЗАПРЕЩЁННЫЕ ОТКРЫВАЮЩИЕ ФРАЗЫ — этот пост НЕ должен начинаться ни с одной из них
(и ни с чего похожего по смыслу и конструкции), даже если тема про студента/карьеру:
«представь», «представьте», «представь себе», «представь, что ты», «вообрази», «вообразите»,
«а что если бы», «ты студент/выпускник и мечтаешь о...». Это САМЫЙ заезженный шаблон у ИИ —
если тема про студента-юриста или начало карьеры, ищи другой заход (см. пункт 1 ниже).

СТРУКТУРЫ — запрещены полностью:
1. Конструкция «не X, а Y» в любом виде, включая «это не X. это Y.».
2. Ряды коротких рубленых предложений подряд ради драматичности.
3. Длинное и среднее тире (—, –) — используй только короткое тире, двоеточие, запятую или точку.
4. Канцелярские отглагольные существительные там, где есть простой глагол:
   не «осуществить проверку», а «проверить»; не «принять решение», а «решить».
5. Пассив с «является»/«осуществляется» — пиши «это» и живой глагол с реальным субъектом действия.
6. Три однородных члена подряд ради ритма, если третий не добавляет смысла.
7. Markdown-разметка внутри текста поста: **жирный**, _курсив_, `код`, ## заголовки.
   Большинство соцсетей (VK, Instagram, обычные Telegram-посты) не рендерят markdown —
   читатель увидит буквальные звёздочки и решётки вместо форматирования. Для выделения
   используй заглавные буквы в начале мысли, эмодзи-маркер или просто хорошую разбивку
   на абзацы — не звёздочки и не решётки.

ЧТО ДЕЛАТЬ ВМЕСТО ЭТОГО:
- Активный залог: у каждого предложения — живой субъект, который что-то делает.
- Конкретика вместо общих слов (не «показатели выросли», а на сколько именно).
- Разная длина предложений, без искусственной рубки на короткие обрубки.
- Никаких новых фактов, цифр или имён, которых не было в исходном ТЗ/теме — не додумывай.

ТОЧНОСТЬ И ЧЕСТНОСТЬ УТВЕРЖДЕНИЙ (частая причина, почему пост выглядит непрофессионально
для экспертной аудитории):
- Не делай категоричных обобщений там, где тема на самом деле спорная или у неё есть
  обратная сторона. Плохо: «скоро никто не будет учить нормы наизусть», «работодатели
  ищут сотрудников с активным присутствием в сети» (без оговорок) — это выдаёт спорное
  личное мнение за общепринятый факт, и знающий тему читатель сразу это заметит.
  Хорошо: смягчи формулировку («у многих», «не всем это нужно, но...») или честно покажи
  обе стороны, если это уместно по объёму поста.
- Если перечисляешь N пунктов под общим заголовком (например «3 навыка», «5 качеств»,
  «4 ошибки») — каждый пункт должен реально относиться к этой категории. Не подменяй
  понятия: «удалённая работа и гибкий график» — это формат/условия работы, а не навык;
  если заголовок обещает «навыки», не вставляй туда формат работы или общие условия.
- Заголовок/тема поста задаёт рамку — КАЖДЫЙ пункт списка и каждый абзац должны реально
  раскрывать именно эту тему, а не быть общим карьерным советом «вообще». Пример ошибки:
  тема «Тренды в условиях цифровизации», а сигналы №2-4 — про удалёнку, узкую
  специализацию и личный бренд, которые к цифровизации отношения не имеют. Перед
  финальным ответом проверь: если убрать заголовок, по тексту всё равно должно быть
  понятно, что пост именно про заявленную тему, а не подборка советов на все случаи.
- ЗАПРЕЩЕНО выдумывать конкретных людей, клиентов, резидентов сообщества или кейсы
  с именем и подробностями, которых не было в теме/брифе/базе знаний, и подавать их как
  реальный факт («Анна, выпускница юрфака, познакомилась с экспертом и получила
  стажировку...» — если это не взято из реальных материалов, такого не было и читатель
  вправе считать это правдой о конкретном человеке).
- ЗАПРЕЩЕНО выдумывать конкретные подробности о БУДУЩЕМ мероприятии, встрече, вебинаре
  или анонсе бренда (формат, дату, кто выступает, что будут обсуждать, что принести с
  собой, как зарегистрироваться), если этой информации не было в теме/брифе/базе знаний —
  ты не знаешь, что реально запланировано, и такое описание читатель воспримет как честный
  анонс. Пример ошибки: «Следующее мероприятие пройдёт в формате закрытого митапа, где
  опытные адвокаты обсудят реальные кейсы... принесите с собой актуальные документы» — это
  придуманные детали о несуществующем событии. Можно писать о форматах мероприятий бренда
  в целом (см. список ниже про мероприятия), но НЕЛЬЗЯ подавать это как конкретное
  предстоящее событие с конкретной программой, спикерами или инструкциями для участников.
- ПОЛНОСТЬЮ ЗАПРЕЩЁН приём «представим ситуацию» / «давайте представим» / «вообразите
  ситуацию» — где угодно в посте, не только в первой строке. Не создавай вымышленного
  героя с именем и придуманным сюжетом ни под каким предлогом, даже если это честно
  обозначено как гипотетический пример — читателю всё равно, что пример гипотетический,
  выглядит это как искусственная история «под копирку». Вместо этого говори прямо:
  конкретный совет, факт, статистика или наблюдение без выдуманного персонажа-примера.

ЧТО СОХРАНЯТЬ (это НЕ признак ИИ-текста для русского языка, не убирай):
кавычки-ёлочки («»), наречия в нормальном количестве, длинные сложные предложения,
если они читаются на одном дыхании.

СТРУКТУРА ПОСТА — соблюдай для каждого текста:
1. Первая строка — хук, который цепляет за 1-2 секунды. Каждый раз выбирай РАЗНЫЙ тип
   хука, подходящий именно этой теме, а не один и тот же приём из раза в раз — например:
   - конкретная цифра или статистика прямо в первой строке;
   - неожиданный или спорный факт;
   - прямой вопрос к читателю про его реальную ситуацию (без «представь себе» — читатель
     и так в этой ситуации, обращайся к ней напрямую: «Не знаешь, с чего начать...»);
   - короткая история/кейс с именем, началом сразу с сути, без «представь»;
   - смелое утверждение или мини-миф, который пост дальше опровергает;
   - прямое обращение по факту («Студентам-юристам: ...», «Если ты ищешь первую стажировку...»).
   Никогда не начинай с темы дословно («Сегодня поговорим о...»), с общих фраз о важности
   темы и НЕ начинай с «представь» / «представьте» / «вообрази» в любом виде — см. список
   запрещённых фраз выше.
2. Основная часть разбита на короткие смысловые блоки (2-4 строки), между ними —
   пустая строка: текст должен легко читаться с телефона, не сплошной стеной.
3. Внутри блоков — конкретика: цифры, примеры, шаги, имена, а не общие рассуждения
   «в целом» и «как правило».
4. Финал — конкретный призыв к действию, а не шаблонное «надеюсь, было полезно» или
   «делитесь своим мнением в комментариях» без причины. Каким именно должен быть
   призыв (коммерческий или мягкое вовлечение) — определяется тоном поста, смотри
   отдельное указание по тону ниже, не делай пост рекламным без явной причины.
   Призыв к действию/вовлекающий вопрос — ТОЛЬКО ОДИН и ТОЛЬКО В САМОМ КОНЦЕ поста,
   после того как мысль полностью раскрыта. НЕ вставляй вопрос к читателю или призыв
   в середину поста между пунктами списка/блоками — например, если список из шести
   пунктов, вопрос «а что вы думаете?» после третьего пункта выглядит как случайная
   вставка, обрывающая мысль, а не как естественная часть текста. Сначала полностью
   закончи раскрывать тему (все пункты списка, весь аргумент), и только потом — один
   финальный призыв/вопрос.
5. НЕ приклеивай в конце формальный блок вроде «💡 Совет:», «Рекомендация:», «Лайфхак:»,
   если он просто повторяет уже сказанное в посте или даёт банальный общий совет не по
   существу темы («составьте план», «будьте активны»). Такой блок — частый признак
   ИИ-текста, написанного «для галочки». Если после основной мысли действительно есть что
   добавить — впиши это органично в текст без ярлыка-заголовка, а не как отдельный довесок.
6. НИКОГДА не пиши в самом посте служебные названия частей текста как видимый
   заголовок/лейбл — «Призыв к действию:», «Хук:», «Вывод:», «Тезис:» и т.п. Это термины
   из инструкции для тебя, а не то, что должен увидеть читатель поста. Нужен просто сам
   призыв к действию («Напишите в комментариях...»), без слов «призыв к действию» перед
   ним.
7. Пост — ОДНО связное целое с ОДНОЙ линией мысли от хука до финала, а НЕ несколько
   отдельных мини-постов, склеенных подряд. Частая ошибка: два (или больше) отдельных
   подзаголовка в духе самостоятельных постов, каждый со своим вступлением и своим
   отдельным призывом к действию в середине текста («Хотите узнать больше? Скачайте
   гайд!»), а затем ещё один новый заголовок с новой мыслью и ещё один вывод в конце —
   читателю кажется, что это два случайно склеенных текста, а не один. Если тема ёмкая —
   выбери ОДИН угол и раскрой его полностью до конца в рамках одной структуры (хук →
   раскрытие по пунктам/блокам → один финальный вывод и один призыв к действию), а не
   пытайся уместить несколько разных заходов на тему в одном посте.

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


def get_deepseek_model() -> str:
    return get_setting("deepseek_model") or DEFAULT_DEEPSEEK_MODEL


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


def _get_deepseek_api_key() -> str:
    key = get_setting("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise AIConfigError(
            "API-ключ DeepSeek не настроен. Получите его на platform.deepseek.com "
            "(раздел API keys) и добавьте в разделе «Настройки»."
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
    # strict=False: менее строгие модели (GigaChat и т.п.) иногда вставляют в значения строк
    # буквальные переносы строк вместо экранированных \n — по умолчанию json их не пропускает.
    data = json.loads(text, strict=False)
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
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = None
        if match:
            try:
                data = json.loads(match.group(0), strict=False)
            except json.JSONDecodeError:
                data = None
        if data is None:
            # Некоторые модели (замечено у GigaChat) иногда отдают содержимое объекта
            # без внешних фигурных скобок, например `"body": "..."` — оборачиваем и
            # пробуем разобрать ещё раз, прежде чем сдаться.
            try:
                data = json.loads("{" + text.strip().rstrip(",") + "}", strict=False)
            except json.JSONDecodeError:
                raise
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise AIGenerationError("Модель вернула данные в неожиданном формате")
    return data


def tone_from_content_type(content_type: str) -> str:
    """Тон коммуникации теперь всегда выводится из типа контента, а не выбирается
    отдельно — раньше это были два независимых поля, которые могли противоречить
    друг другу (например тип «Продающий», но тон «дружелюбный»). Единственный тип
    контента, требующий явно продающего тона — «Продающий»; для любого другого тон
    нейтральный (остальные стилистические нюансы задаются самим ТЗ платформы)."""
    return "продающий" if (content_type or "").strip().lower() == "продающий" else "нейтральный"


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
                f"(строго следуй ему — формат, длина, тон, структура, что избегать):\n{platform['brief']}\n\n"
                f"Если в ТЗ выше описана конкретная структура публикации (например, разбивка "
                f"на слайды/карточки карусели, нумерованные части, отдельные короткие посты) — "
                f"ОБЯЗАТЕЛЬНО воспроизведи её буквально в тексте поста с явными метками "
                f"(например «Слайд 1:», «Слайд 2:» и т.д. — каждая с новой строки, с пустой "
                f"строкой между слайдами), а не пиши сплошным текстом одним абзацем.\n"
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

    is_sales_tone = tone.strip().lower() == "продающий"
    if is_sales_tone:
        cta_instruction = (
            "Тон продающий — в конце уместен прямой коммерческий призыв к действию "
            "(оставить заявку, записаться, написать в директ/бот и т.п.)."
        )
    else:
        cta_instruction = (
            "Тон НЕ продающий — пост НЕ должен читаться как реклама. Не заканчивай его "
            "коммерческим призывом («оставьте заявку», «запишитесь», «купите», «получите скидку», "
            "ссылкой на бота/консультацию), если сама тема прямо не об этом. Вместо этого заверши "
            "мягким вовлечением: вопрос к читателю, приглашение поделиться мнением или опытом в "
            "комментариях, — без ощущения продажи."
        )

    user_prompt = f"""{get_brand_context()}

{get_date_context()}

{get_knowledge_context(include_sales=is_sales_tone)}

Тема поста: {topic}

Общее техническое задание (бриф): {brief or "не указано, ориентируйся только на тему"}

Тон коммуникации: {tone}

{platform_block}
{length_instruction}

{cta_instruction}

Сгенерируй {variants} вариант(а) поста именно для этой платформы, строго следуя её
индивидуальному ТЗ выше (если оно задано) — оно имеет приоритет над общими рекомендациями,
за исключением объёма текста, если он задан явно выше.

Придумай сильный хук для первой строки именно под эту тему и аудиторию, разбей текст
на читаемые блоки с пустыми строками между ними и закончи призывом к действию, соответствующим
тону (см. указание выше) — это обязательные требования из правил стиля, а не пожелание.

НЕ добавляй хештеги (#слово) нигде — ни в тексте поста, ни отдельным списком тегов.
Хештеги и теги для публикации человек расставляет сам, полностью самостоятельно.

Верни JSON-массив из {variants} объектов. Каждый объект должен иметь поля:
- "title": короткий заголовок/тема поста
- "body": полный текст поста, готовый к публикации, БЕЗ хештегов

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
                # Функция тегов/хештегов от ИИ полностью убрана по просьбе пользователя —
                # теги он расставляет сам. Даже если модель всё равно пришлёт поле "tags"
                # (промпт больше его не запрашивает, но не все модели это соблюдают),
                # игнорируем его, а не передаём дальше.
                "tags": "",
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


def _generate_deepseek(system_prompt: str, user_prompt: str) -> str:
    # DeepSeek API OpenAI-совместим — переиспользуем клиент openai с другим base_url,
    # отдельная библиотека не нужна.
    client = openai.OpenAI(api_key=_get_deepseek_api_key(), base_url=DEEPSEEK_API_BASE)
    model = get_deepseek_model()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except openai.AuthenticationError as exc:
        raise AIConfigError(
            "API-ключ DeepSeek недействителен. Проверьте его в разделе «Настройки»."
        ) from exc
    except openai.NotFoundError as exc:
        raise AIConfigError(
            f"Модель «{model}» недоступна в DeepSeek. Укажите другую модель в «Настройках» "
            "(например deepseek-chat или deepseek-reasoner)."
        ) from exc
    except openai.APIError as exc:
        raise AIGenerationError(f"Ошибка обращения к DeepSeek API: {exc}") from exc

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


VALID_PROVIDERS = {"anthropic", "openai", "gemini", "gigachat", "deepseek", "ollama"}


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
    if provider == "deepseek":
        return _generate_deepseek(system_prompt, user_prompt)
    return _generate_anthropic(system_prompt, user_prompt)


def _generate_and_parse_variants(
    system_prompt: str,
    user_prompt: str,
    provider: str | None,
    attempts: int = 3,
    length: int | None = None,
    require_slides: bool = False,
) -> list[dict]:
    """Некоторые провайдеры (GigaChat, локальные модели через Ollama) не так
    строго следуют инструкции «верни только JSON», как Claude/GPT/Gemini, и
    иногда возвращают слегка невалидный JSON. Вместо того чтобы сразу
    показывать пользователю ошибку разбора — тихо пробуем ещё раз."""
    last_exc: AIGenerationError | None = None
    for _ in range(max(1, attempts)):
        text = _generate_text(system_prompt, user_prompt, provider)
        try:
            variants = _parse_variants(text)
            for v in variants:
                body = _clean_body_artifacts(v.get("body", ""))
                body = _strip_meta_labels(body)
                body = _strip_markdown_formatting(body)
                body = _strip_html_tags(body)
                body = _strip_hashtags(body)
                body = _strip_trailing_tip_block(body)
                # Сначала чиним хук и фактические ошибки про бренд (эти правки переписывают
                # текст целиком и могут случайно ужать его), и только потом добиваем длину —
                # чтобы финальный шаг проверки объёма был последним и решающим.
                body = _ensure_no_banned_hook(body, provider)
                body = _ensure_no_imagine_scenario(body, provider)
                body = _ensure_no_forbidden_event_terms(body, provider)
                body = _ensure_no_fabricated_person(body, provider)
                body = _ensure_no_fabricated_event_details(body, provider)
                if require_slides:
                    body = _ensure_slide_structure(body, provider)
                if length:
                    body = _ensure_length(body, length, provider)
                v["body"] = body
            return variants
        except AIGenerationError as exc:
            last_exc = exc
    raise last_exc


def _continue_body(body: str, target_length: int, provider: str | None) -> str:
    """Просит модель дописать НОВЫЙ кусок текста в продолжение уже готового черновика.

    Менее строгие модели (особенно GigaChat) на просьбу «дополни/перепиши длиннее» часто
    просто возвращают текст почти той же длины — задача «дописать целиком» их не мотивирует
    реально нарастить объём. Задача «допиши только продолжение» работает надёжнее."""
    missing = max(target_length - len(body), 0)
    system_prompt = (
        "Ты — опытный контент-маркетолог и SMM-копирайтер. Пишешь продолжение уже начатого "
        "поста на русском языке, сохраняя его тон и стиль. Отвечаешь ТОЛЬКО валидным JSON "
        "без markdown-обёртки, без пояснений до или после.\n\n" + RUSSIAN_DESLOP_RULES
    )
    user_prompt = f"""Вот начало поста (уже готово, НЕ переписывай и не повторяй его — только продолжи):

---
{body}
---

Текущая длина черновика — {len(body)} знаков. Нужно дописать ещё примерно {missing} знаков
НОВОГО текста, чтобы общий объём поста вышел на {target_length} знаков. Развей тему глубже:
добавь конкретный пример, разбери ещё один аспект, приведи аргумент, дай практический совет —
что-то по существу, а не «вода». Продолжение должно логично идти следом за концом черновика,
в том же тоне и стиле, без повтора уже сказанного и без отдельного вступления.

Верни JSON-объект с одним полем:
- "continuation": только новый текст-продолжение (без черновика выше)

Верни только JSON-объект, ничего больше."""
    text = _generate_text(system_prompt, user_prompt, provider)
    continuation = _extract_text_field(text, "continuation")
    if continuation and not _looks_malformed(continuation):
        return body.rstrip() + "\n\n" + continuation
    return body


def _extract_text_field(text: str, field_name: str) -> str | None:
    """Извлекает значение текстового поля из ответа модели по имени поля — устойчиво
    к неполному JSON. GigaChat на подобных «однополевых» ответах иногда отдаёт:
    валидный объект {"field": "..."}; объект без внешних скобок: "field": "...";
    или вовсе значение без кавычек: "field": голый текст (даже не строка). Сначала
    пробуем честный JSON, затем откатываемся на регулярку — берём всё после
    `"field":` и снимаем внешние кавычки, если они есть."""
    stripped = _strip_code_fence(text)
    try:
        data = _extract_json_object(stripped)
        value = data.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass

    match = re.search(rf'"{re.escape(field_name)}"\s*:\s*(.*)', stripped, re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip().rstrip(",").strip()
    # Снимаем внешние кавычки и/или фигурные скобки — GigaChat иногда путает их
    # между собой как обёртку строкового значения (например `{"текст"}` вместо
    # `"текст"`), максимум пара слоёв обёртки.
    for _ in range(2):
        if len(value) >= 2 and value[0] in "{\"" and value[-1] in "}\"":
            value = value[1:-1].strip()
        else:
            break
    return value.strip() or None


_BANNED_HOOK_RE = re.compile(
    # [\W\d] — любые не-буквенные символы (эмодзи, пробелы, знаки препинания, цифры)
    # перед хуком: посты часто начинаются с эмодзи-приветствия перед самим текстом.
    r"^[\W\d]{0,15}(представь(те)?\b|вообрази(те)?\b)",
    re.IGNORECASE | re.UNICODE,
)


def _has_banned_hook(body: str) -> bool:
    """«Представь себе...» — самый заезженный шаблон хука у слабых моделей (GigaChat
    его выдаёт почти всегда, несмотря на прямой запрет в системном промпте). Проверяем
    результат программно и, если нужно, отдельно просим переписать только первую строку."""
    return bool(_BANNED_HOOK_RE.match((body or "").strip()))


def _split_hook(body: str) -> tuple[str, str]:
    """Отделяет первую строку/фразу-хук от остального текста поста."""
    first_line, sep, rest = body.partition("\n")
    if sep and first_line.strip():
        return first_line, rest
    # Нет явного переноса строки в начале — берём первое предложение как хук.
    match = re.match(r"(.+?[.!?])\s+(.*)", body, re.DOTALL)
    if match:
        return match.group(1), match.group(2)
    return body, ""


def _rewrite_banned_hook(body: str, provider: str | None) -> str:
    """Просит модель придумать только НОВУЮ первую строку и склеивает её с остальным
    текстом программно. Просить модель переписать весь пост целиком ненадёжно —
    GigaChat при этом часто заодно сильно ужимает остальной текст."""
    hook, rest = _split_hook(body)
    system_prompt = (
        "Ты — опытный контент-маркетолог и SMM-копирайтер. Придумываешь только первую "
        "строку (хук) поста на русском языке. Отвечаешь ТОЛЬКО валидным JSON без "
        "markdown-обёртки.\n\n" + RUSSIAN_DESLOP_RULES
    )
    user_prompt = f"""Первая строка (хук) поста нарушает правило: начинается с «представь» /
«представьте» / «вообрази» в любом виде — самого заезженного и запрещённого приёма.

Текущий (запрещённый) хук: {hook}

Остальной текст поста — только для контекста темы и тона, НЕ переписывай его:
{rest[:500]}

Придумай ОДНУ новую первую строку взамен — используй другой приём: конкретную цифру
или факт, прямой вопрос к читателю про его реальную ситуацию, короткий кейс с именем,
смелое утверждение или прямое обращение по факту. Не начинай с «представь» ни в каком
виде. Строка должна логично продолжаться остальным текстом поста.

Верни JSON-объект с одним полем:
- "hook": новая первая строка (только она, без остального текста)

Верни только JSON-объект, ничего больше."""
    text = _generate_text(system_prompt, user_prompt, provider)
    new_hook = _extract_text_field(text, "hook")
    if new_hook and not _has_banned_hook(new_hook) and not _looks_malformed(new_hook):
        return f"{new_hook.strip()}\n{rest}" if rest else new_hook.strip()
    return body


_MALFORMED_PREFIX_RE = re.compile(r'^\s*[^\n":{}]{0,40}"\s*:\s*"?')


def _looks_malformed(text: str) -> bool:
    """Грубая проверка на то, что текст — обрывок JSON, а не читаемый пост: начинается
    со служебных символов JSON (иногда с прилипшим обрывком ключа вроде `0":"...`),
    которые не должны попадать в текст поста."""
    if not text:
        return True
    if text[:1] in "{\":,":
        return True
    return bool(_MALFORMED_PREFIX_RE.match(text))


def _clean_body_artifacts(text: str) -> str:
    """Иногда модель вставляет в само значение строки обрывок JSON-ключа/обёртки
    (например `0":"Текст...` или `{"Текст...`) — подчищаем такие артефакты в начале,
    не трогая остальной текст."""
    cleaned = text or ""
    for _ in range(2):
        match = _MALFORMED_PREFIX_RE.match(cleaned)
        if match and match.end() > 0:
            cleaned = cleaned[match.end():].lstrip()
        elif cleaned[:1] in "{\":,":
            cleaned = cleaned[1:].lstrip()
        else:
            break
    return cleaned


# Метки, которые могут появиться ТОЛЬКО как эхо служебной инструкции для модели —
# ни один реальный пост не начинает строку словами «Призыв к действию:» или «Хук:».
# В отличие от «Совет:»/«Рекомендация:» (которые в редких случаях бывают уместным
# реальным текстом поста), эти вырезаем детерминированно, без лишнего вызова ИИ.
_META_LABEL_RE = re.compile(
    r"^[^\w\n]{0,10}(призыв\s+к\s+действию|хук|тезис)\s*:[ \t]*\n?",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_meta_labels(text: str) -> str:
    if not text:
        return text
    return _META_LABEL_RE.sub("", text).strip()


# **жирный** и ## заголовки не рендерятся на большинстве площадок (VK, Instagram-подписи,
# обычные Telegram-посты без специального parse_mode) — читатель видит буквальные
# звёздочки/решётки. Промпт-правило соблюдается не всегда, поэтому чистим детерминированно.
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def _strip_markdown_formatting(text: str) -> str:
    if not text:
        return text
    text = _MARKDOWN_BOLD_RE.sub(r"\1", text)
    text = _MARKDOWN_HEADING_RE.sub("", text)
    return text


# Модель иногда (замечено на реальной генерации) отдаёт текст с HTML-разметкой
# (<p>, <ul><li>, <a href="...">) вместо обычного текста поста — большинство площадок
# (обычный Telegram-пост без parse_mode=HTML, VK, Instagram-подписи) покажут эти теги
# буквально, читатель увидит мусорные <p> прямо в тексте. Промпт этого не просит,
# но раз случается — чистим детерминированно, превращая разметку в обычные переносы
# строк/буллеты вместо простого выпиливания тегов (иначе абзацы слипнутся в один).
_HTML_A_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HTML_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_BLOCK_CLOSE_RE = re.compile(r"</(p|div|ul|ol)\s*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
}


def _strip_html_tags(text: str) -> str:
    if not text or "<" not in text:
        return text
    cleaned = _HTML_A_RE.sub(r"\1", text)  # ссылка -> просто видимый текст, без href
    cleaned = _HTML_LI_RE.sub(r"• \1\n", cleaned)  # пункт списка -> буллет с новой строки
    cleaned = _HTML_BR_RE.sub("\n", cleaned)
    cleaned = _HTML_BLOCK_CLOSE_RE.sub("\n\n", cleaned)  # закрывающий блочный тег -> абзац
    cleaned = _HTML_TAG_RE.sub("", cleaned)  # остальные теги (<p>, <ul>, <strong> и т.д.) — просто убрать
    for entity, replacement in _HTML_ENTITIES.items():
        cleaned = cleaned.replace(entity, replacement)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# Пользователь расставляет хештеги сам — они не должны попадать в текст поста вообще
# (отдельно от этого модель может предложить теги в поле "tags", которое не показывается
# в самом тексте). Промпт-инструкция соблюдается не всегда, поэтому чистим детерминированно.
_HASHTAG_RE = re.compile(r"#[^\s#]+")


def _strip_hashtags(text: str) -> str:
    if not text or "#" not in text:
        return text
    cleaned = _HASHTAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# По прямой просьбе пользователя: приклеенный блок «Совет:»/«Рекомендация:»/«Лайфхак:»
# В КОНЦЕ поста неуместен вообще, даже если содержит реальный совет по теме — не только
# когда он банален. В отличие от служебных меток («Призыв к действию:») тут убираем не
# только ярлык, а весь последний абзац целиком.
_TRAILING_TIP_RE = re.compile(
    r"^[^\w\n]{0,10}(?:[а-яёa-z]{2,20}\s+){0,2}(совет\w*|рекомендаци\w*|лайфхак\w*)[^\n:]{0,15}:",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_trailing_tip_block(text: str) -> str:
    """Убирает приклеенный блок «Совет:»/«Рекомендация:»/«Лайфхак:» В КОНЦЕ поста.

    Раньше делилось по "\\n\\n" (двойной перенос строки), но если модель поставила
    только одинарный перенос перед таким блоком (реальный случай), проверка на
    начало последнего абзаца его не ловила. Теперь ищем последнее вхождение метки
    в начале СТРОКИ (не обязательно абзаца) и, если оно есть, отрезаем весь хвост
    текста начиная с этой строки и до конца — по формулировке пользователя блок
    всегда именно «в конце», так что берём последнее совпадение."""
    if not text:
        return text
    matches = list(_TRAILING_TIP_RE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    remaining = text[: last.start()].strip()
    # Защита от ложного обрезания: сам приклеенный блок иногда длиннее, чем текст до
    # него, поэтому проверять долю от общей длины ненадёжно (см. историю фикса). Вместо
    # этого требуем, чтобы ДО метки оставался хоть сколько-то настоящего текста поста —
    # если метка оказалась в самом начале (совпадение почти сразу же), значит это не
    # «приклеенный блок в конце», а что-то другое — не трогаем, чтобы не выбросить почти
    # весь пост.
    if len(remaining) < 20:
        return text
    return remaining


# Последний рубеж защиты от «Представь:»/«Вообрази:» в начале поста — на случай, если
# ни одна из попыток переписать хук через ИИ не удалась (GigaChat иногда упорно не
# отдаёт валидную замену за все раунды). Вместо того чтобы показать пользователю
# текст с самым заезженным шаблоном хука, детерминированно вырезаем сам оборот
# («Представь:», «Вообрази себе,», «Представьте, что») и с большой буквы продолжаем
# тем, что шло дальше. Стилистически это скромнее творческого нового хука, но
# гарантированно убирает запрещённую фразу.
_BANNED_HOOK_PREFIX_STRIP_RE = re.compile(
    r"^([\W\d]{0,15})(представь(те)?|вообрази(те)?)\b(\s*себе\b)?[,:]?\s*(что\b\s*)?",
    re.IGNORECASE | re.UNICODE,
)


def _strip_banned_hook_prefix(body: str) -> str:
    if not body:
        return body
    stripped = body.strip()
    match = _BANNED_HOOK_PREFIX_STRIP_RE.match(stripped)
    if not match:
        return body
    leading = match.group(1) or ""
    rest = stripped[match.end():].lstrip()
    if rest:
        rest = rest[0].upper() + rest[1:]
    result = f"{leading}{rest}" if leading.strip() else rest
    return result.strip() or body


def _ensure_no_banned_hook(body: str, provider: str | None, max_rounds: int = 3) -> str:
    current = body
    for _ in range(max_rounds):
        if not _has_banned_hook(current):
            break
        new_current = current
        # Как и при добивании длины — GigaChat не всегда с первого раза отдаёт валидный
        # JSON на этот запрос, даём пару попыток прежде чем сдаться в этом раунде.
        for _attempt in range(2):
            try:
                candidate = _rewrite_banned_hook(current, provider)
            except Exception:
                continue
            # Задача — заменить только первую строку, остальной текст должен остаться
            # тем же по объёму. Если модель вместо этого пересказала пост заметно короче
            # (случается у GigaChat), это явный брак — не принимаем такую правку.
            if (
                candidate != current
                and not _looks_malformed(candidate)
                and len(candidate) >= len(current) * 0.85
            ):
                new_current = candidate
                break
        if new_current == current:
            # Не удалось нормально переписать хук через ИИ ни в одном раунде — прежде
            # чем сдаться, пробуем детерминированную зачистку самого оборота.
            break
        current = new_current
    if _has_banned_hook(current):
        current = _strip_banned_hook_prefix(current)
    return current


# Форматы мероприятий, которые этот бренд реально НЕ проводит (см. brand_description
# в настройках) — модель периодически упоминает их «по умолчанию», несмотря на прямой
# запрет в тексте промпта. Инструкции одной не хватает — нужна программная проверка.
_FORBIDDEN_EVENT_TERMS_RE = re.compile(r"конференц\w*|муткорт\w*|олимпиад\w*", re.IGNORECASE)


def _has_forbidden_event_terms(body: str) -> bool:
    return bool(_FORBIDDEN_EVENT_TERMS_RE.search(body or ""))


def _rewrite_forbidden_event_terms(body: str, provider: str | None) -> str:
    system_prompt = (
        "Ты — опытный редактор. Исправляешь фактическую ошибку в готовом посте на "
        "русском языке, не трогая остальной текст. Отвечаешь ТОЛЬКО валидным JSON без "
        "markdown-обёртки.\n\n" + RUSSIAN_DESLOP_RULES
    )
    user_prompt = f"""В этом посте упомянуты форматы мероприятий, которые бренд на самом деле
НЕ проводит (конференции, муткорты и/или олимпиады) — это фактическая ошибка про сам бренд,
а не стилистическая придирка.

---
{body}
---

Перепиши текст, заменив каждое упоминание конференций/муткортов/олимпиад на реальные форматы
мероприятий бренда: вебинары и встречи с практикующими юристами, бизнес-завтраки, фотосъёмки,
встречи с психологом, тематические мероприятия (выбери из них то, что по смыслу подходит на
замену, необязательно все сразу). Остальной текст — структуру, длину, стиль, эмодзи, хук,
призыв к действию — сохрани как есть, поменяй только эти конкретные упоминания.

Верни JSON-объект с одним полем:
- "body": весь пост целиком, с исправленными форматами мероприятий

Верни только JSON-объект, ничего больше."""
    text = _generate_text(system_prompt, user_prompt, provider)
    new_body = _extract_text_field(text, "body")
    if new_body and not _looks_malformed(new_body) and len(new_body) >= len(body) * 0.85:
        return new_body
    return body


def _ensure_no_forbidden_event_terms(body: str, provider: str | None, max_rounds: int = 2) -> str:
    current = body
    for _ in range(max_rounds):
        if not _has_forbidden_event_terms(current):
            break
        try:
            candidate = _rewrite_forbidden_event_terms(current, provider)
        except Exception:
            break
        if candidate == current or _has_forbidden_event_terms(candidate):
            # Правка не помогла — не зацикливаемся, лучше оставить как есть, чем
            # рисковать ещё одной порчей текста.
            break
        current = candidate
    return current


# Живой пример из реальной генерации: «...на нашем вебинаре «Юрфак в эпоху ИИ» 29 августа
# в 11:00!» — модель выдумала конкретную дату/время предстоящего мероприятия, которых не
# было в теме/брифе. Правило «не выдумывай подробности о будущих мероприятиях» уже есть в
# RUSSIAN_DESLOP_RULES, но конкретную дату/время легко детерминированно поймать: сочетание
# календарной даты или времени ЧЧ:ММ рядом со словом-мероприятием — почти всегда либо
# фабрикация, либо требует проверки (в отличие от общего упоминания формата мероприятия).
_EVENT_MONTH_RE = r"(?:январ[яе]|феврал[яе]|март[а]?|апрел[яе]|ма[яе]|июн[яе]|июл[яе]|август[а]?|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе])"
_EVENT_DATE_RE = re.compile(rf"\d{{1,2}}\s+{_EVENT_MONTH_RE}(?:\s+\d{{4}}(?:\s*года?)?)?", re.IGNORECASE)
_EVENT_TIME_RE = re.compile(r"\bв\s+\d{1,2}[:.]\d{2}\b(?:\s*(?:мск|msk|по\s+мск))?", re.IGNORECASE)
_EVENT_WORD_RE = re.compile(
    r"вебинар\w*|мастер-класс\w*|встреч\w*|мероприяти\w*|бизнес-завтрак\w*|конференци\w*|созвон\w*|эфир\w*|стрим\w*",
    re.IGNORECASE,
)


def _has_fabricated_event_details(body: str) -> bool:
    if not body:
        return False
    has_date_or_time = bool(_EVENT_DATE_RE.search(body) or _EVENT_TIME_RE.search(body))
    return has_date_or_time and bool(_EVENT_WORD_RE.search(body))


def _strip_event_date_time(text: str) -> str:
    """Последний рубеж: если AI-переписывание не сработало ни за один раунд, вырезаем
    сами дату/время детерминированно (а не весь абзац — здесь это обычно короткая
    встроенная фраза, а не отдельный смысловой блок)."""
    if not text:
        return text
    cleaned = _EVENT_TIME_RE.sub("", text)
    cleaned = _EVENT_DATE_RE.sub("", cleaned)
    # прибираем осиротевшие "в ", пунктуацию и двойные пробелы, оставшиеся после вырезания
    cleaned = re.sub(r"\bв\s+(?=[,!.\n]|$)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,!.?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _rewrite_fabricated_event_details(body: str, provider: str | None) -> str:
    system_prompt = (
        "Ты — опытный редактор. Убираешь выдуманные подробности о мероприятии из готового "
        "поста на русском языке, не трогая остальной текст. Отвечаешь ТОЛЬКО валидным JSON "
        "без markdown-обёртки.\n\n" + RUSSIAN_DESLOP_RULES
    )
    user_prompt = f"""В этом посте указана конкретная дата и/или время предстоящего мероприятия
(вебинара, встречи и т.п.), которых не было в теме/брифе — то есть модель их выдумала и подала
как реальный факт. Это запрещено.

---
{body}
---

Перепиши соответствующее место: убери конкретную выдуманную дату/время, замени на общую
формулировку без точных цифр (например «на ближайшем вебинаре» вместо «29 августа в 11:00»,
«скоро объявим дату» вместо конкретной даты). Остальной текст (структуру, длину, стиль, эмодзи,
хук, призыв к действию) сохрани как есть. Если внимательно присмотревшись ты не находишь в
тексте выдуманной даты/времени мероприятия — верни текст без изменений.

Верни JSON-объект с одним полем:
- "body": весь пост целиком, без выдуманной даты/времени

Верни только JSON-объект, ничего больше."""
    text = _generate_text(system_prompt, user_prompt, provider)
    new_body = _extract_text_field(text, "body")
    if new_body and not _looks_malformed(new_body) and len(new_body) >= len(body) * 0.85:
        return new_body
    return body


def _ensure_no_fabricated_event_details(body: str, provider: str | None, max_rounds: int = 3) -> str:
    current = body
    for _ in range(max_rounds):
        if not _has_fabricated_event_details(current):
            break
        new_current = current
        for _attempt in range(3):
            try:
                candidate = _rewrite_fabricated_event_details(current, provider)
            except Exception:
                continue
            if candidate != current and not _has_fabricated_event_details(candidate):
                new_current = candidate
                break
        if new_current == current:
            break
        current = new_current
    if _has_fabricated_event_details(current):
        current = _strip_event_date_time(current)
    return current


# «Представим ситуацию» и её варианты — запрещённый приём (см. ЗАПРЕЩЁННЫЕ ОТКРЫВАЮЩИЕ
# ФРАЗЫ выше), но модель иногда вставляет его не в первую строку, а в середину поста —
# такое _has_banned_hook (проверка только начала текста) не ловит, нужна проверка по
# всему тексту.
_IMAGINE_SCENARIO_RE = re.compile(
    r"представ[ьи]\w*\s*(себе\s+)?(,\s*)?(что\b|ситуаци\w*)|вообрази\w*\s*(себе\s+)?(ситуаци\w*)?",
    re.IGNORECASE,
)


def _has_imagine_scenario_anywhere(body: str) -> bool:
    return bool(_IMAGINE_SCENARIO_RE.search(body or ""))


def _rewrite_imagine_scenario(body: str, provider: str | None) -> str:
    system_prompt = (
        "Ты — опытный редактор. Убираешь запрещённый приём из готового поста на русском "
        "языке, не трогая остальной текст. Отвечаешь ТОЛЬКО валидным JSON без "
        "markdown-обёртки.\n\n" + RUSSIAN_DESLOP_RULES
    )
    user_prompt = f"""В этом посте (где-то, не обязательно в начале) использован запрещённый
приём «представим ситуацию» / «вообразите» с придуманным гипотетическим примером или героем.

---
{body}
---

Перепиши абзац с этим приёмом: убери фразу «представим ситуацию»/«вообразите» и вымышленного
героя, замени на прямое изложение — конкретный совет, факт, наблюдение или практическую
рекомендацию по той же мысли, без придуманного сюжета и персонажа. Остальной текст (другие
абзацы, структуру, длину, стиль, эмодзи, хук, призыв к действию) сохрани как есть.

Верни JSON-объект с одним полем:
- "body": весь пост целиком, с исправленным абзацем

Верни только JSON-объект, ничего больше."""
    text = _generate_text(system_prompt, user_prompt, provider)
    new_body = _extract_text_field(text, "body")
    if new_body and not _looks_malformed(new_body) and len(new_body) >= len(body) * 0.85:
        return new_body
    return body


def _ensure_no_imagine_scenario(body: str, provider: str | None, max_rounds: int = 3) -> str:
    current = body
    for _ in range(max_rounds):
        if not _has_imagine_scenario_anywhere(current):
            break
        new_current = current
        # Как и с остальными правками: GigaChat не всегда с первого раза отдаёт валидный
        # JSON на этот запрос — даём несколько попыток в рамках раунда, а не сдаёмся сразу.
        for _attempt in range(3):
            try:
                candidate = _rewrite_imagine_scenario(current, provider)
            except Exception:
                continue
            if candidate != current and not _has_imagine_scenario_anywhere(candidate):
                new_current = candidate
                break
        if new_current == current:
            break
        current = new_current
    return current


# Для платформ, чьё ТЗ явно описывает карусельный/карточный формат (Instagram и т.п.),
# промпт-инструкции («воспроизведи структуру с метками «Слайд N:») недостаточно —
# GigaChat нередко присылает обычный список/абзацы без явных меток слайдов, из-за чего
# пост невозможно быстро разнести по отдельным карточкам дизайна. Проверяем результат
# программно и, если меток нет, отдельно просим модель переразметить готовый текст.
_SLIDE_MARKER_RE = re.compile(r"(?:^|\n)\s*(слайд|карточк\w*)\s*\d+", re.IGNORECASE)


def _brief_requires_slides(brief_text: str) -> bool:
    return bool(re.search(r"карусел|\bслайд|карточ", brief_text or "", re.IGNORECASE))


def _has_slide_structure(body: str) -> bool:
    # Одно случайное упоминание слова «слайд» в тексте не считается разметкой —
    # для реальной карусели нужно минимум 2 промаркированные карточки.
    return len(_SLIDE_MARKER_RE.findall(body or "")) >= 2


def _rewrite_into_slides(body: str, provider: str | None) -> str:
    system_prompt = (
        "Ты — опытный SMM-редактор. Переразмечаешь уже готовый текст поста под формат "
        "карусели, не меняя смысл и почти не меняя объём. Отвечаешь ТОЛЬКО валидным JSON "
        "без markdown-обёртки.\n\n" + RUSSIAN_DESLOP_RULES
    )
    user_prompt = f"""Вот готовый текст поста для карусели (Instagram и т.п.), но в нём НЕТ
явных меток слайдов — это просто сплошной текст или нумерованный список без разметки.

---
{body}
---

Разбей этот текст на слайды карусели и промаркируй каждый явной меткой на отдельной
строке: «Слайд 1 (обложка): ...», «Слайд 2: ...», «Слайд 3: ...» и т.д., с пустой строкой
между слайдами. НЕ добавляй новый контент и не сокращай — используй ту же мысль/структуру,
что уже есть в тексте (если там уже есть нумерованный список из пунктов — каждый пункт
становится отдельным слайдом). Последний слайд — с призывом к действию, если он уже есть в
тексте. Если в конце текста есть отдельная короткая подпись поста (не относящаяся к
конкретному слайду) — оставь её последним абзацем БЕЗ метки «Слайд».

Верни JSON-объект с одним полем:
- "body": весь пост целиком, с явной разметкой по слайдам

Верни только JSON-объект, ничего больше."""
    text = _generate_text(system_prompt, user_prompt, provider)
    new_body = _extract_text_field(text, "body")
    if new_body and not _looks_malformed(new_body) and len(new_body) >= len(body) * 0.85:
        return new_body
    return body


def _ensure_slide_structure(body: str, provider: str | None, max_rounds: int = 2) -> str:
    current = body
    for _ in range(max_rounds):
        if _has_slide_structure(current):
            break
        new_current = current
        for _attempt in range(2):
            try:
                candidate = _rewrite_into_slides(current, provider)
            except Exception:
                continue
            if candidate != current and _has_slide_structure(candidate) and not _looks_malformed(candidate):
                new_current = candidate
                break
        if new_current == current:
            # Не удалось переразметить через ИИ — оставляем исходный текст без меток,
            # это лучше, чем показать пользователю обрывок JSON.
            break
        current = new_current
    return current


# Правило «не выдумывай конкретных людей/кейсы с именем» уже прописано в RUSSIAN_DESLOP_RULES,
# но модель иногда всё равно вставляет придуманного «Студента Анну» или «Ивана, выпускника...»
# как социальное доказательство — особенно в репутационных/сторителлинг постах. Одной
# промпт-инструкции недостаточно, нужна программная проверка. Ловим два самых частых
# паттерна: «роль + Имя» («Студентка Анна», «Выпускник Иван») и «Имя, роль» («Анна,
# выпускница юрфака,») — по имени как таковому не проверяем (список имён слишком велик
# и ненадёжен), а по типичной грамматической конструкции представления персонажа.
_FABRICATED_PERSON_RE = re.compile(
    # (?i: ...) — регистронезависимость только для слова-роли (студент/выпускник/...),
    # а не для всего паттерна: имя всё равно должно начинаться с заглавной буквы,
    # иначе сработает на любом обычном существительном после запятой.
    r"\b(?i:студент\w*|выпускник\w*|выпускниц\w*|резидент\w*|стажёр\w*|стажер\w*|клиент\w*)\s+(?P<name1>[А-ЯЁ][а-яё]{2,})\b"
    # до двух слов-определений между запятой и словом-ролью: «Алексей, наш вчерашний выпускник, ...»
    r"|\b(?P<name2>[А-ЯЁ][а-яё]{2,}),\s*(?:[а-яё]+\s+){0,2}(?i:студент\w*|выпускник\w*|выпускниц\w*|резидент\w*|стажёр\w*|стажер\w*|клиент\w*)\b",
    re.UNICODE,
)

# Второй, более широкий паттерн: голое Имя сразу перед глаголом «личной истории»
# («Мария нашла стажировку...», «Иван получил должность...») — самый частый вид
# выдумки, без слова-роли рядом (см. _FABRICATED_PERSON_RE выше). Ловим по глаголу,
# а не по конкретным именам (список имён слишком велик и ненадёжен), но такое Имя
# легко спутать с обычным словом в начале предложения («Мы», «Это», «Каждый») —
# поэтому отфильтровываем через стоп-лист самых частых капитализированных не-имён.
_NARRATIVE_VERB_RE = re.compile(
    r"\b([А-ЯЁ][а-яё]{2,})\s+(?:[а-яё]+\s+)?"  # допускаем одно наречие между именем и глаголом («Анна успешно прошла...»)
    r"(нашл[а-я]*|получил[а-я]*|поступил[а-я]*|устроил[а-я]*|прошл[а-я]*|заверш[а-я]*"
    r"|начал[а-я]*|решил[а-я]*|поделил[а-я]*|рассказал[а-я]*|справил[а-я]*|добил[а-я]*с[а-я]*"
    r"|стал[а-я]*|отправил[а-я]*|написал[а-я]*|ответил[а-я]*|выбрал[а-я]*|изменил[а-я]*"
    r"|перестроил[а-я]*|сменил[а-я]*|прошёл|прошла)\b",
    re.UNICODE,
)
_NAME_STOPWORDS = {
    "мы", "он", "она", "оно", "они", "это", "наша", "наш", "наше", "наши",
    "вот", "если", "как", "что", "все", "всё", "каждый", "каждая", "каждое",
    "многие", "некоторые", "один", "одна", "команда", "компания", "агентство",
    "сообщество", "карьерный", "юрист", "после", "затем", "далее", "кто",
    "такие", "такой", "такая", "результате", "благодаря", "многие", "большинство",
}


def _has_fabricated_person(body: str) -> bool:
    if not body:
        return False
    if _FABRICATED_PERSON_RE.search(body):
        return True
    for match in _NARRATIVE_VERB_RE.finditer(body):
        if match.group(1).lower() not in _NAME_STOPWORDS:
            return True
    return False


def _extract_fabricated_names(body: str) -> set[str]:
    """Возвращает конкретные слова-имена, пойманные как выдуманный персонаж — чтобы
    потом отследить ПОВТОРНЫЕ упоминания того же имени в других абзацах текста, даже
    там, где само по себе предложение не подходит ни под один из паттернов выше
    (например «Алексей воспользовался...» — глагол «воспользовался» не в списке
    глаголов личной истории, но имя Алексей уже засветилось раньше как выдуманное)."""
    if not body:
        return set()
    names: set[str] = set()
    for m in _FABRICATED_PERSON_RE.finditer(body):
        name = m.group("name1") or m.group("name2")
        if name:
            names.add(name)
    for m in _NARRATIVE_VERB_RE.finditer(body):
        if m.group(1).lower() not in _NAME_STOPWORDS:
            names.add(m.group(1))
    return names


def _rewrite_fabricated_person(body: str, provider: str | None) -> str:
    system_prompt = (
        "Ты — опытный редактор. Убираешь выдуманного персонажа из готового поста на русском "
        "языке, не трогая остальной текст. Отвечаешь ТОЛЬКО валидным JSON без "
        "markdown-обёртки.\n\n" + RUSSIAN_DESLOP_RULES
    )
    user_prompt = f"""В этом посте упомянут конкретный человек с именем (студент/выпускник/резидент/
клиент по имени), которого не было в теме/брифе/базе знаний — то есть модель его выдумала и
подала как реальный факт. Это запрещено.

---
{body}
---

Если такой выдуманный человек с именем действительно есть в тексте — перепиши соответствующий
абзац: убери имя и придуманные подробности о конкретном человеке, замени на обобщённое
утверждение без персонажа (например «Многие резиденты сообщества находят стажировку благодаря
вебинарам» вместо «Анна нашла стажировку благодаря вебинару»). Остальной текст (другие абзацы,
структуру, длину, стиль, эмодзи, хук, призыв к действию) сохрани как есть.
Если внимательно присмотревшись ты не находишь в тексте выдуманного человека с именем (например,
это было реальное упоминание бренда или общая фраза) — верни текст без изменений.

Верни JSON-объект с одним полем:
- "body": весь пост целиком, без выдуманного персонажа

Верни только JSON-объект, ничего больше."""
    text = _generate_text(system_prompt, user_prompt, provider)
    new_body = _extract_text_field(text, "body")
    # Порог ниже, чем у остальных rewrite-функций (0.85) — здесь убирается не одна строка
    # или фраза, а целый абзац с придуманным человеком и подробностями его "истории",
    # поэтому пост закономерно может ощутимо сократиться даже при честном исправлении.
    if new_body and not _looks_malformed(new_body) and len(new_body) >= len(body) * 0.7:
        return new_body
    return body


def _strip_fabricated_person_paragraphs(text: str) -> str:
    """Последний рубеж защиты: если ни один раунд AI-переписывания не убрал
    выдуманного персонажа (проверено на реальной генерации под давлением
    промпта «истории успеха» — модель может упорно возвращать фабрикацию все
    попытки подряд), просто выбрасываем целиком абзацы, где сработал
    _has_fabricated_person. Результат может звучать чуть менее гладко, чем
    аккуратная AI-правка, но гарантированно не содержит выдуманного человека —
    это то, что пользователь явно просил превыше всего остального.

    Заодно вычищаем абзацы, где выдуманное имя упоминается ПОВТОРНО другим глаголом,
    не входящим в _NARRATIVE_VERB_RE (например «Алексей воспользовался встречей...» —
    сам по себе этот абзац не матчится ни одним паттерном, но имя «Алексей» уже
    опознано как выдуманное в другом абзаце этого же текста)."""
    if not text:
        return text
    paragraphs = text.split("\n\n")
    fabricated_names = set()
    for p in paragraphs:
        fabricated_names |= _extract_fabricated_names(p)
    kept = []
    for p in paragraphs:
        if _has_fabricated_person(p):
            continue
        if fabricated_names and any(re.search(rf"\b{re.escape(name)}\b", p) for name in fabricated_names):
            continue
        kept.append(p)
    cleaned = "\n\n".join(kept).strip()
    return cleaned or text


def _ensure_no_fabricated_person(body: str, provider: str | None, max_rounds: int = 3) -> str:
    current = body
    for _ in range(max_rounds):
        if not _has_fabricated_person(current):
            break
        new_current = current
        # Как и с остальными правками: даём несколько попыток в рамках раунда, а не
        # сдаёмся сразу на первой же невалидной попытке.
        for _attempt in range(3):
            try:
                candidate = _rewrite_fabricated_person(current, provider)
            except Exception:
                continue
            if candidate != current and not _has_fabricated_person(candidate):
                new_current = candidate
                break
        if new_current == current:
            # Не удалось убрать через ИИ ни за один раунд — вместо того чтобы
            # показать пользователю текст с фабрикацией, вырезаем абзацы с ней
            # детерминированно (см. _strip_fabricated_person_paragraphs).
            break
        current = new_current
    if _has_fabricated_person(current):
        current = _strip_fabricated_person_paragraphs(current)
    return current


def _ensure_length(body: str, target_length: int, provider: str | None, max_rounds: int = 3) -> str:
    """Некоторые модели (особенно GigaChat) сильно недооценивают заданную длину текста в
    инструкции промпта. Вместо того чтобы полагаться только на просьбу в промпте, после
    генерации проверяем фактическую длину и, если она заметно меньше запрошенной,
    дополнительными раундами дописываем текст продолжениями — пока не наберём хотя бы ~80%
    от цели или не закончатся попытки."""
    current = body
    for _ in range(max_rounds):
        if not current or len(current) >= target_length * 0.8:
            break
        new_current = current
        # GigaChat иногда отдаёт слегка невалидный JSON (как и при основной генерации) —
        # даём этому шагу несколько попыток, а не сдаёмся сразу на первой же ошибке разбора.
        for _attempt in range(3):
            try:
                new_current = _continue_body(current, target_length, provider)
                break
            except Exception:
                continue
        if len(new_current) <= len(current):
            break  # модель не смогла добавить текст ни с одной попытки — не зацикливаемся
        current = new_current
    return current


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
        return _generate_and_parse_variants(system_prompt, user_prompt, provider, length=length), []

    results: list[dict] = []
    errors: list[str] = []
    for platform_id in platform_ids:
        if platform_id not in PLATFORM_MAP:
            continue
        platform = _get_platform_context(platform_id)
        system_prompt, user_prompt = _build_prompts_for_platform(topic, brief, tone, platform, variants, length)
        require_slides = _brief_requires_slides(platform["brief"])
        try:
            for item in _generate_and_parse_variants(
                system_prompt, user_prompt, provider, length=length, require_slides=require_slides
            ):
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


# Промпт-инструкции «не повторяй тему» недостаточно — GigaChat легко выдаёт формально разные
# строки, которые на деле повторяют один и тот же шаблон вопроса («Какие карьерные вызовы
# ждут...» / «Какие карьерные компетенции нужны...»), просто заменяя одно слово. Ловим это
# дёшево: сравниваем первое значимое слово темы (после вопросительного слова) — если оно
# совпадает с уже использованной в этой же комбинации темой, считаем шаблон повторённым.
_PLAN_TOPIC_QUESTION_WORDS = {
    "какой", "какая", "какие", "каково", "какова", "каков", "как", "что", "чем",
    "почему", "зачем", "ли", "куда", "откуда",
}


def _topic_signature(topic: str) -> str:
    words = re.findall(r"[а-яёa-z]+", (topic or "").lower())
    significant = [w for w in words if w not in _PLAN_TOPIC_QUESTION_WORDS]
    return significant[0] if significant else ""


def _plan_row_prompt(
    tone: str,
    platform: dict,
    brief_block: str,
    direction: str,
    content_type: str,
    fmt: str,
    used_topics: list[str],
    all_topics: list[str] | None = None,
) -> str:
    used_block = (
        "Уже запланированные темы этой же комбинации направление/контент/формат "
        "(НЕ повторяй их и не пиши близкие по смыслу — это касается не только слов, но и "
        "самого шаблона вопроса: «Какие карьерные вызовы ждут...» и «Какие карьерные "
        "компетенции нужны...» — это по сути ОДНА тема с заменённым словом, а не две "
        "разные; так же не годится просто менять существительное — «портрет/качества/"
        "препятствия/вызовы» — в одном и том же вопросе про идеального юриста будущего):\n"
        + "\n".join(f"- {t}" for t in used_topics)
        if used_topics
        else "Тем в этой комбинации ещё не было — это первый пост такого типа."
    )
    other_topics = [t for t in (all_topics or []) if t not in used_topics]
    diversity_block = (
        "\n\nТемы, уже использованные в ДРУГИХ комбинациях направление/контент/формат этого же "
        "плана (весь план не должен крутиться вокруг одной и той же истории вроде "
        "«стажировка/резюме/собеседование» — если тема ниже не про трудоустройство напрямую, "
        "не своди её к этому; используй как ориентир, чтобы выбрать по-настоящему другой "
        "угол, а не близкую по смыслу тему):\n" + "\n".join(f"- {t}" for t in other_topics[-15:])
        if other_topics
        else ""
    )
    format_line = (
        f"- Формат публикации: {fmt}"
        if fmt
        else "- Формат публикации: не задан пользователем — выбери сам наиболее подходящий "
        "формат для этой темы и типа контента."
    )
    format_clause = f"формату «{fmt}»" if fmt else "формату, который сам для неё выберешь"
    is_sales = content_type.strip().lower() == "продающий" or tone.strip().lower() == "продающий"
    return f"""{get_brand_context()}

{get_date_context()}

{get_knowledge_context(include_sales=is_sales)}

Платформа: {platform['name']}
{brief_block}
Тон коммуникации: {tone}

Параметры этого поста (заданы пользователем, обязательны к соблюдению):
- Направление/рубрика: {direction}
- Тип контента: {content_type}
{format_line}

{used_block}{diversity_block}

Придумай ОДНУ конкретную тему поста, которая точно соответствует направлению «{direction}»,
типу контента «{content_type}» и {format_clause} — не общими словами, а готовую формулировку,
которую можно сразу использовать как тему для написания текста. Тема должна реально
раскрывать направление «{direction}» своим собственным сюжетом, а не быть очередной вариацией
на тему поиска работы/резюме/собеседования, если направление не об этом напрямую.

Если список уже использованных тем выше не пуст — выбери тему с ДРУГИМ углом подачи, а не
просто другой синоним того же вопроса. Примеры разных углов: конкретный процесс/этапы
(«как проходит...»), статистика или данные, разбор частой ошибки, сравнение «было/стало»,
спорное мнение или миф, инструмент/технология, чек-лист действий, прямой вопрос к аудитории
о её опыте. Не начинай тему с того же вопросительного оборота («Какие качества/навыки/
компетенции нужны...», «Какие вызовы ждут...») второй раз подряд в этой комбинации.

Верни ОДИН JSON-объект (не массив) с полем:
- "topic": тема поста

Верни только JSON-объект, ничего больше."""


def generate_plan(
    platform_id: str,
    rows: list[dict],
    period_days: int,
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
    all_topics: list[str] = []  # темы по ВСЕМУ плану — для разнообразия между комбинациями

    for day_offset in range(period_days):
        for row in day_plan[day_offset]:
            combo_key = (row["direction"], row["content_type"], row["format"])
            used_topics = used_topics_by_combo.setdefault(combo_key, [])
            row_tone = tone_from_content_type(row["content_type"])
            user_prompt = _plan_row_prompt(
                row_tone, platform, brief_block, row["direction"], row["content_type"], row["format"],
                used_topics, all_topics,
            )
            # До 3 попыток: небольшие локальные модели иногда возвращают "рамблинг"
            # вместо чистой темы (смесь языков, обрывки JSON) — такой ответ отбраковывается
            # в _extract_plan_items, и мы просто пробуем сгенерировать заново, а не
            # сохраняем мусор как есть. Заодно тем же циклом попыток добиваемся темы,
            # которая не повторяет шаблон уже использованной в этой комбинации (см.
            # _topic_signature) — промпт-инструкции одной не хватает.
            used_signatures = {_topic_signature(t) for t in used_topics}
            last_exc: AIGenerationError | None = None
            topic = None
            last_duplicate_topic: str | None = None
            for attempt in range(3):
                try:
                    text = _generate_text(_PLAN_SYSTEM_PROMPT, user_prompt)
                    items = _extract_plan_items(text)
                    if not items:
                        raise AIGenerationError("Модель не вернула тему")
                    candidate = items[0]["topic"]
                    last_exc = None
                    if _topic_signature(candidate) in used_signatures:
                        # Похоже на уже использованную тему этой же комбинации (тот же
                        # шаблон вопроса) — пробуем ещё раз, а не принимаем как есть.
                        last_duplicate_topic = candidate
                        continue
                    topic = candidate
                    break
                except AIGenerationError as exc:
                    last_exc = exc
                    continue

            if not topic and last_duplicate_topic:
                # Все попытки дали похожую тему — берём последнюю, чтобы не оставить
                # день плана пустым: похожая тема лучше, чем дыра в плане.
                topic = last_duplicate_topic

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
                all_topics.append(topic)
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

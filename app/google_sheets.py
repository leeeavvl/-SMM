"""Синхронизация контент-плана с Google Таблицей через сервисный аккаунт.

Особенности структуры таблицы пользователя:
- Колонки определяются автоматически по заголовкам первой строки листа
  (регистронезависимо, по ключевым словам).
- Платформа (соцсеть) определяется не колонкой, а ВКЛАДКОЙ (листом) —
  например лист «тг» для Telegram, «инст» для Instagram. Строка плана
  дописывается на лист, соответствующий её platform_id.
"""
from __future__ import annotations

import json
import re

import gspread
from google.oauth2.service_account import Credentials

from app.database import get_setting

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER_KEYWORDS: dict[str, list[str]] = {
    "date": ["дата", "date"],
    "direction": ["направлен"],
    "content_type": ["контент", "тип контент"],
    "format": ["формат"],
    "topic": ["название поста", "тема", "заголовок", "название"],
    "status": ["статус", "status"],
    "text": ["текст", "text", "body"],
}

# Подсказки для сопоставления вкладки листа с платформой по её названию.
PLATFORM_TAB_HINTS: dict[str, list[str]] = {
    "telegram": ["тг", "telegram", "телеграм"],
    "instagram": ["инст", "instagram", "инста"],
    "threads": ["тредс", "threads", "трэдс"],
    "vk": ["вк", " vk", "vk "],
    "dzen": ["дзен", "zen"],
    "zakon_ru": ["zakon", "закон"],
    "youtube": ["ютуб", "youtube", "юту"],
    "tiktok": ["тикток", "tiktok", "тт"],
    "pinterest": ["пинтерест", "pinterest", "пин"],
    "tzh": ["т-ж", "т—ж", "тж"],
    "facebook": ["facebook", "фб", "фейсбук"],
    "x": ["твиттер", "twitter", " x ", "икс"],
    "linkedin": ["linkedin", "линкедин"],
    "avito": ["авито", "avito"],
}


class SheetsConfigError(RuntimeError):
    pass


class SheetsSyncError(RuntimeError):
    pass


def _column_letter(index_1based: int) -> str:
    """1 -> A, 2 -> B, ..., 27 -> AA."""
    letters = ""
    n = index_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _extract_spreadsheet_id(url_or_id: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
    return match.group(1) if match else url_or_id.strip()


def get_client() -> gspread.Client:
    # Загруженный через форму JSON-ключ (хранится в базе целиком) имеет приоритет —
    # он работает и там, где нет доступа к локальной файловой системе (облачный
    # хостинг вроде Railway). Путь к файлу на диске — старый способ, для локального
    # запуска на своём компьютере, оставлен для обратной совместимости.
    key_json = get_setting("google_service_account_json")
    if key_json:
        try:
            info = json.loads(key_json)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        except (ValueError, KeyError) as exc:
            raise SheetsConfigError(f"Некорректный загруженный JSON-ключ: {exc}") from exc
        return gspread.authorize(creds)

    key_path = get_setting("google_service_account_path")
    if not key_path:
        raise SheetsConfigError(
            "Не задан JSON-ключ сервисного аккаунта Google — загрузите файл ключа в «Настройках»."
        )
    try:
        creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    except FileNotFoundError as exc:
        raise SheetsConfigError(f"Файл ключа не найден по пути: {key_path}") from exc
    except (ValueError, KeyError) as exc:
        raise SheetsConfigError(f"Некорректный JSON-ключ: {exc}") from exc
    return gspread.authorize(creds)


def get_spreadsheet() -> gspread.Spreadsheet:
    sheet_url = get_setting("google_sheet_url")
    if not sheet_url:
        raise SheetsConfigError("Не указана ссылка на Google Таблицу.")
    client = get_client()
    try:
        return client.open_by_key(_extract_spreadsheet_id(sheet_url))
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise SheetsConfigError(
            "Таблица не найдена или у сервисного аккаунта нет к ней доступа. "
            "Откройте доступ на редактирование для email из JSON-ключа."
        ) from exc
    except gspread.exceptions.APIError as exc:
        raise SheetsConfigError(f"Ошибка доступа к Google Sheets API: {exc}") from exc


def get_worksheet() -> gspread.Worksheet:
    """Первый/настроенный по умолчанию лист — используется для быстрой проверки подключения."""
    spreadsheet = get_spreadsheet()
    worksheet_name = get_setting("google_sheet_worksheet") or None
    if worksheet_name:
        try:
            return spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound as exc:
            raise SheetsConfigError(f"Лист «{worksheet_name}» не найден в таблице.") from exc
    return spreadsheet.sheet1


def resolve_worksheet_for_platform(spreadsheet: gspread.Spreadsheet, platform_id: str) -> gspread.Worksheet | None:
    hints = PLATFORM_TAB_HINTS.get(platform_id, [])
    for ws in spreadsheet.worksheets():
        title_lower = ws.title.strip().lower()
        if any(hint in title_lower for hint in hints):
            return ws
    return None


def _detect_columns(header_row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(header_row):
        header_lower = (header or "").strip().lower()
        if not header_lower:
            continue
        for field, keywords in HEADER_KEYWORDS.items():
            if field in mapping:
                continue
            if any(kw in header_lower for kw in keywords):
                mapping[field] = idx
    return mapping


def test_connection() -> dict:
    ws = get_worksheet()
    header_row = ws.row_values(1)
    columns = _detect_columns(header_row)
    return {
        "ok": True,
        "spreadsheet_title": ws.spreadsheet.title,
        "worksheet_title": ws.title,
        "header_row": header_row,
        "detected_columns": columns,
        "available_tabs": [w.title for w in ws.spreadsheet.worksheets()],
    }


def _row_values(columns: dict[str, int], width: int, item: dict) -> list[str]:
    row = [""] * width
    values = {
        "date": item.get("plan_date", ""),
        "direction": item.get("direction", ""),
        "content_type": item.get("content_type", ""),
        "format": item.get("format", ""),
        "topic": item.get("topic", ""),
        "status": item.get("status", ""),
        "text": item.get("body", ""),
    }
    for field, col_idx in columns.items():
        row[col_idx] = values.get(field, "")
    return row


def append_plan_items(items: list[dict]) -> tuple[list[tuple[int, str, int]], list[str]]:
    """Дописывает строки на вкладки по платформам.

    Возвращает (placements, errors), где placements — список
    (content_plan_id, worksheet_title, row_number) для последующего апдейта ячеек.
    """
    if not items:
        return [], []

    spreadsheet = get_spreadsheet()
    placements: list[tuple[int, str, int]] = []
    errors: list[str] = []

    by_platform: dict[str, list[dict]] = {}
    for item in items:
        by_platform.setdefault(item["platform_id"], []).append(item)

    ws_cache: dict[str, gspread.Worksheet] = {}
    for platform_id, platform_items in by_platform.items():
        ws = ws_cache.get(platform_id)
        if ws is None:
            ws = resolve_worksheet_for_platform(spreadsheet, platform_id)
            if ws is None:
                errors.append(
                    f"Не найдена вкладка для платформы «{platform_id}» — добавьте лист с названием, "
                    f"содержащим её имя (например «тг» для Telegram)."
                )
                continue
            ws_cache[platform_id] = ws

        header_row = ws.row_values(1)
        columns = _detect_columns(header_row)
        if not columns:
            errors.append(f"Лист «{ws.title}»: не удалось распознать колонки по заголовкам.")
            continue

        width = max(columns.values()) + 1
        existing_row_count = len(ws.get_all_values())
        rows_to_write = [_row_values(columns, width, item) for item in platform_items]

        start_row = existing_row_count + 1
        end_row = start_row + len(rows_to_write) - 1
        cell_range = f"A{start_row}:{_column_letter(width)}{end_row}"

        try:
            ws.update(range_name=cell_range, values=rows_to_write, value_input_option="USER_ENTERED")
        except gspread.exceptions.APIError as exc:
            errors.append(f"Лист «{ws.title}»: ошибка записи — {exc}")
            continue

        for offset, item in enumerate(platform_items):
            row_number = start_row + offset
            placements.append((item["id"], ws.title, row_number))

    return placements, errors


def update_cell_text(sheet_tab: str, sheet_row: int, text: str, status: str | None = None) -> None:
    spreadsheet = get_spreadsheet()
    try:
        ws = spreadsheet.worksheet(sheet_tab)
    except gspread.exceptions.WorksheetNotFound as exc:
        raise SheetsSyncError(f"Лист «{sheet_tab}» не найден.") from exc

    header_row = ws.row_values(1)
    columns = _detect_columns(header_row)

    try:
        if "text" in columns:
            ws.update_cell(sheet_row, columns["text"] + 1, text)
        if status is not None and "status" in columns:
            ws.update_cell(sheet_row, columns["status"] + 1, status)
    except gspread.exceptions.APIError as exc:
        raise SheetsSyncError(f"Ошибка обновления ячейки: {exc}") from exc

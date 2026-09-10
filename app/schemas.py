from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ContentCreate(BaseModel):
    title: str = Field(min_length=1)
    body: str = ""
    media_url: Optional[str] = None
    tags: str = ""


class ContentUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    media_url: Optional[str] = None
    tags: Optional[str] = None
    status: Optional[str] = None


class PublishRequest(BaseModel):
    platform_ids: list[str]
    scheduled_at: Optional[str] = None  # ISO datetime string; None = publish now


class PlatformConnect(BaseModel):
    credentials: dict[str, str]


class PlatformSettingsUpdate(BaseModel):
    brief: Optional[str] = None
    channel_url: Optional[str] = None


class SaleCreate(BaseModel):
    platform_id: str
    date: Optional[str] = None
    orders: int = 0
    revenue: float = 0
    notes: str = ""


class SettingsUpdate(BaseModel):
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    ai_provider: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_base_url: Optional[str] = None
    brand_name: Optional[str] = None
    brand_description: Optional[str] = None
    google_sheet_url: Optional[str] = None
    google_sheet_worksheet: Optional[str] = None
    google_service_account_path: Optional[str] = None
    google_sheets_auto_sync: Optional[bool] = None
    auto_learning_enabled: Optional[bool] = None


class AssistantRunRequest(BaseModel):
    tool_id: str = Field(min_length=1)
    inputs: dict[str, str] = {}
    provider: Optional[str] = None


class AIGenerateRequest(BaseModel):
    topic: str = Field(min_length=1)
    brief: str = ""
    tone: str = "нейтральный"
    platform_ids: list[str] = []
    variants: int = 1
    length: Optional[int] = Field(default=None, gt=0, le=20000)
    provider: Optional[str] = None  # "anthropic" | "ollama" — переопределяет провайдера из настроек для этой генерации


class PlanRowSpec(BaseModel):
    direction: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    format: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=30)


class PlanGenerateRequest(BaseModel):
    platform_id: str = Field(min_length=1)
    rows: list[PlanRowSpec] = Field(min_length=1)
    start_date: str = Field(min_length=1)  # YYYY-MM-DD
    end_date: str = Field(min_length=1)  # YYYY-MM-DD
    tone: str = "нейтральный"


class PlanWriteRequest(BaseModel):
    length: Optional[int] = Field(default=None, gt=0, le=20000)


class PlanLinkContentRequest(BaseModel):
    content_id: int


class ManualStatsUpdate(BaseModel):
    views: Optional[int] = Field(default=None, ge=0)
    likes: Optional[int] = Field(default=None, ge=0)
    comments: Optional[int] = Field(default=None, ge=0)

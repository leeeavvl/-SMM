from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.ai import VALID_PROVIDERS, AIConfigError, AIGenerationError, generate_posts
from app.schemas import AIGenerateRequest

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/generate")
def ai_generate(payload: AIGenerateRequest):
    if payload.provider and payload.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Неизвестная нейросеть: {payload.provider}")
    try:
        variants, errors = generate_posts(
            topic=payload.topic,
            brief=payload.brief,
            tone=payload.tone,
            platform_ids=payload.platform_ids,
            variants=payload.variants,
            length=payload.length,
            provider=payload.provider,
        )
    except AIConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except AIGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"variants": variants, "errors": errors}

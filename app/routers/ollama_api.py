"""Stub for ollama_api router — local dev fallback."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/ollama", tags=["ollama"])


@router.get("/health")
async def health():
    return {"status": "stub"}

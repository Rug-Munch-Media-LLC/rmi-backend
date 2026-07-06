import logging

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger("protection_router")


class HealthResponse(BaseModel):
    status: str = "ok"
    error: str = None
    rag: dict = None
    blocklist: dict = None
    scanner: dict = None
    wallet: dict = None
    entity: dict = None


@router.get("/health", response_model=HealthResponse)
async def get_health():
    return HealthResponse(status="ok")

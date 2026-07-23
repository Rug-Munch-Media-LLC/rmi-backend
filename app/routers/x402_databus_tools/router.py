"""
Router definition for x402 DataBus Tools.
Imported by __init__.py to combine all sub-routers.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/x402-databus", tags=["x402-databus-tools"])

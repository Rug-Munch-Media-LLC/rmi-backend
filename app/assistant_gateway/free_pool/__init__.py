"""Free chat pool: declarative providers, health, quota, fail-closed selection.

External request/token-quota APIs only. Local llama.cpp, HF shims, time-capped
lanes and paid models are NOT representable here (see ``pool.json`` guards and
``tests/assistant_gateway/test_free_pool.py``).
"""

from __future__ import annotations

from app.assistant_gateway.free_pool.probe import probe_pool, probe_provider
from app.assistant_gateway.free_pool.select import FreePoolExhausted, pick_model
from app.assistant_gateway.free_pool.spec import PoolSpec, ProviderSpec, load_pool
from app.assistant_gateway.free_pool.state import PoolState

__all__ = [
    "FreePoolExhausted",
    "PoolSpec",
    "PoolState",
    "ProviderSpec",
    "load_pool",
    "pick_model",
    "probe_pool",
    "probe_provider",
]

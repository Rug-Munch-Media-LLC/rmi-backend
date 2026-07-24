"""Source adapters for the federated label API.

Each adapter implements `query(address, chain) -> list[Label]`.

To add a new source:
  1. Create a new module in this package (e.g., `my_source.py`)
  2. Implement `async def query_my_source(address: str, chain: str) -> list[Label]`
  3. Register it in LabelSource enum (models.py)
  4. Wire it in federated.py:_query_source dispatcher

All adapters follow these conventions:
  - Return [] on no match (not None)
  - Raise exceptions on infrastructure errors (handled by caller)
  - All addresses are normalized to lowercase before query
  - Each label has source=LabelSource.MY_SOURCE
"""
from __future__ import annotations

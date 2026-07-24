"""RMI v5 §T11 - Federated wallet labels domain.

Aggregates wallet labels from 6 sources in parallel:
  1. eth-labels.db        - 115K EVM labels (local SQLite)
  2. Etherscan CSV        - 51K EVM labels (local CSV via DuckDB)
  3. Ethereum labels CSV  - 51K EVM labels (local CSV via DuckDB)
  4. Solana labels CSV    - 103K SOL labels (local CSV via DuckDB)
  5. ClickHouse           - wallet_memory.wallet_labels (live API)
  6. MetaSleuth           - live API at aml.blocksec.com (works)

Each source has its own adapter in app/domain/labels/sources/.
The FederatedLabelAPI orchestrator queries all sources concurrently
with asyncio.gather(return_exceptions=True), deduplicates by
(address, chain, label_type), and tags each label with provenance.

Graceful degradation: if a source is unreachable, we log + skip it
and return labels from whatever did work.

Per RMIV5 v4.0 §T28 (P1): biggest moat - 5K internal labels → 200K+
via federation.
"""

from __future__ import annotations
from app.domains.labels.federated import (
    FederatedLabelAPI,
    get_federated_api,
)
from app.domains.labels.models import Label, LabelSource

__all__ = [
    "FederatedLabelAPI",
    "Label",
    "LabelSource",
    "get_federated_api",
]

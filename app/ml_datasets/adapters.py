"""Adapter: map a labeled address to a ``FraudGNNDetector`` fingerprint.

The fraud model (:class:`app.fraud_gnn.FraudGNNDetector`) consumes a
17-feature wallet fingerprint (see ``FRAUD_FEATURES``), not a raw address.
Building a real fingerprint requires on-chain transaction history (RPC /
DataBus enrichment), which is heavy and network-dependent.  This module
therefore ships a deterministic, dependency-light adapter:

* Features the caller already has (Real-CATS transaction profiles via
  ``load_realcats(with_features=True)``, or a fixture embedding a fingerprint)
  are passed through when their keys match ``FRAUD_FEATURES``.
* Every missing feature is synthesised deterministically from the address
  (sha256 -> plausible per-feature range), so the benchmark is fully offline
  and reproducible.

FIXME(issue): replace the hash-synthesis stub with real on-chain
enrichment once that pipeline is wired up.  Production currently passes a
placeholder ``{"address": w, "tx_count": 1}`` fingerprint (see
``app.routers.wallet_clustering_router``); until a real
``address -> FRAUD_FEATURES`` builder exists, metrics on real datasets
measure the hash baseline, not model quality.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.fraud_gnn import FRAUD_FEATURES

# Plausible value ranges per feature, aligned with the fallback Random Forest
# training data in ``app.fraud_gnn``: (lo, hi, is_integer).
_FEATURE_RANGES: dict[str, tuple[float, float, bool]] = {
    "tx_count": (0.0, 2000.0, True),
    "avg_tx_value": (0.0, 50.0, False),
    "tx_value_std": (0.0, 100.0, False),
    "unique_counterparties": (0.0, 1000.0, True),
    "received_vs_sent_ratio": (0.0, 3.0, False),
    "avg_time_between_txs": (0.0, 72.0, False),
    "contract_interactions": (0.0, 500.0, True),
    "mixer_interactions": (0.0, 2.0, True),
    "dex_interactions": (0.0, 100.0, True),
    "cex_deposits": (0.0, 50.0, True),
    "age_days": (0.0, 2000.0, True),
    "active_days": (0.0, 800.0, True),
    "eth_balance": (0.0, 100.0, False),
    "token_diversity": (0.0, 50.0, True),
    "gas_price_avg": (0.0, 500.0, False),
    "gas_price_std": (0.0, 200.0, False),
    "failed_tx_ratio": (0.0, 1.0, False),
}
_DEFAULT_RANGE: tuple[float, float, bool] = (0.0, 1.0, False)


def _normalise_key(key: str) -> str:
    """Normalise a feature key so Real-CATS style headers can match."""
    return key.strip().lower().replace(" ", "_")


def _uniform(seed: str, lo: float, hi: float) -> float:
    """Deterministic uniform value in ``[lo, hi)`` derived from ``seed``."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()[:8]
    scaled = int.from_bytes(digest, "big") / float(2**64)
    return lo + scaled * (hi - lo)


def _synthesise(name: str, address: str, chain: str) -> float | int:
    """Deterministic stub value for one fingerprint feature."""
    lo, hi, is_int = _FEATURE_RANGES.get(name, _DEFAULT_RANGE)
    value = _uniform(f"{chain}:{address}:{name}", lo, hi)
    return int(value) if is_int else round(value, 4)


def address_to_fingerprint(
    address: str,
    *,
    chain: str = "ethereum",
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fingerprint consumable by :meth:`FraudGNNDetector.predict`.

    Provided ``features`` win over the hash stub for every key that matches a
    ``FRAUD_FEATURES`` name (keys are normalised); the rest are synthesised
    deterministically from ``address`` so the result is reproducible offline.
    """
    provided: dict[str, Any] = {}
    if features:
        provided = {_normalise_key(str(k)): v for k, v in features.items()}

    fingerprint: dict[str, Any] = {"address": address, "chain": chain}
    for name in FRAUD_FEATURES:
        if name in provided:
            try:
                fingerprint[name] = float(provided[name])
            except (TypeError, ValueError):
                fingerprint[name] = _synthesise(name, address, chain)
        else:
            fingerprint[name] = _synthesise(name, address, chain)
    return fingerprint

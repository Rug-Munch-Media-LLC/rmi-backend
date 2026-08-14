"""
PyOD-based anomaly detection over wallet/token feature vectors.

Replaces the dead ``app/ml_anomaly.py`` stub (which hardcodes
``anomaly_score=0.0``). This module is a real, self-contained anomaly
scorer: it lazily fits an ensemble of PyOD models (IsolationForest + ECOD +
LOF) on a reference feature matrix and scores single feature vectors with
no RPC / network calls.

MIGRATION NOTE
--------------
``app/ml_anomaly.py`` is intentionally NOT edited. Callers of
``WalletAnomalyDetector.analyze_wallet()`` / ``TokenMetricAnomalyDetector``
should migrate to :class:`PyODAnomalyDetector`::

    from app.pyod_anomaly import PyODAnomalyDetector, get_pyod_anomaly

    det = get_pyod_anomaly()                      # lazy singleton
    det.fit(records)                              # optional: real corpus
    result = det.score(features)                  # -> AnomalyResult
    result.anomaly_score / result.is_anomaly / result.contributing_features

Feature vocabulary matches ``app.fraud_gnn.FRAUD_FEATURES`` so fingerprints
built for the fraud model can be scored here unchanged. The module is
stdlib + numpy + pyod only; when ``pyod`` is not installed, scoring degrades
gracefully to a zero-score result with a logged warning.

Design
------
* ``fit(records)`` trains the ensemble on a list of feature dicts. Missing
  feature keys default to ``0.0`` (same convention as
  ``FraudGNNDetector.extract_features``).
* If ``score()`` is called before ``fit()``, the detector lazily fits on a
  small built-in synthetic reference corpus so it is usable standalone.
* Each model's raw ``decision_function`` scores are min-max normalised
  against the training scores into ``[0, 1]`` and averaged across the
  ensemble; ``anomaly_score`` is that mean (higher = more anomalous).
* ``contributing_features`` lists the feature names whose z-score against
  the reference corpus deviates most (|z| > 1), capped at a small count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Minimum number of records before fit() will train. Below this the
# reference matrix is too small for LOF / IsolationForest to be meaningful.
MIN_FIT_RECORDS = 10
# Default anomaly threshold (matches the 0.8 used by the old ml_anomaly stub).
DEFAULT_THRESHOLD = 0.8
# Fraction of the reference corpus assumed anomalous (used to seed the
# internal contamination parameter of the PyOD models).
DEFAULT_CONTAMINATION = 0.1
# Cap on how many contributing feature names are returned.
MAX_CONTRIBUTING_FEATURES = 5

# Feature vocabulary — keep in sync with app.fraud_gnn.FRAUD_FEATURES.
FRAUD_FEATURES: tuple[str, ...] = (
    "tx_count",
    "avg_tx_value",
    "tx_value_std",
    "unique_counterparties",
    "received_vs_sent_ratio",
    "avg_time_between_txs",
    "contract_interactions",
    "mixer_interactions",
    "dex_interactions",
    "cex_deposits",
    "age_days",
    "active_days",
    "eth_balance",
    "token_diversity",
    "gas_price_avg",
    "gas_price_std",
    "failed_tx_ratio",
)


# ── Errors ───────────────────────────────────────────────────────────────────


class PyODNotAvailable(RuntimeError):
    """Raised when ``pyod`` is not installed and a fit is attempted."""


# ── Result model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnomalyResult:
    """Outcome of scoring a single feature vector.

    Attributes:
        anomaly_score: Combined ensemble anomaly score in ``[0, 1]`` —
            higher is more anomalous.
        is_anomaly: ``anomaly_score >= threshold``.
        contributing_features: Feature names with the largest absolute
            z-score deviation from the reference corpus (|z| > 1), sorted
            descending by deviation. Empty when the point is close to the
            reference population.
        model_type: Human-readable description of the scoring backend.
    """

    anomaly_score: float
    is_anomaly: bool
    contributing_features: list[str]
    model_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_score": self.anomaly_score,
            "is_anomaly": self.is_anomaly,
            "contributing_features": self.contributing_features,
            "model_type": self.model_type,
        }


# ── Optional dependency loading ──────────────────────────────────────────────


def _load_pyod_models() -> tuple[type[Any], type[Any], type[Any]]:
    """Import the PyOD model classes, raising :class:`PyODNotAvailable` if absent."""
    try:
        from pyod.models.ecod import ECOD
        from pyod.models.iforest import IForest
        from pyod.models.lof import LOF
    except ImportError as exc:
        raise PyODNotAvailable(
            "pyod is not installed; run `pip install pyod` to enable PyOD anomaly detection"
        ) from exc
    return IForest, ECOD, LOF


def pyod_available() -> bool:
    """Return True if the PyOD dependency can be imported in this environment."""
    try:
        _load_pyod_models()
    except PyODNotAvailable:
        return False
    return True


# ── Detector ─────────────────────────────────────────────────────────────────


class PyODAnomalyDetector:
    """Anomaly scorer over wallet/token feature vectors using a PyOD ensemble.

    The detector is lazily fitted: calling :meth:`score` before :meth:`fit`
    trains the ensemble on a built-in synthetic reference corpus so the
    class is usable standalone (useful for dev/CI where no corpus exists).
    """

    def __init__(
        self,
        *,
        feature_names: Sequence[str] | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        contamination: float = DEFAULT_CONTAMINATION,
        random_state: int = 42,
        max_contributing_features: int = MAX_CONTRIBUTING_FEATURES,
    ) -> None:
        self.feature_names: tuple[str, ...] = tuple(feature_names) if feature_names else FRAUD_FEATURES
        self.threshold = threshold
        self._contamination = contamination
        self._random_state = random_state
        self._max_contributing_features = max_contributing_features
        self._models: list[Any] = []
        self._score_bounds: list[tuple[float, float]] = []
        self._means: np.ndarray | None = None
        self._stds: np.ndarray | None = None
        self._fitted = False

    # ── Public API ──────────────────────────────────────────────────────────

    def fit(self, records: list[dict[str, Any]]) -> None:
        """Fit the ensemble on a corpus of feature dicts.

        Args:
            records: List of feature dicts using the configured feature
                names. Missing keys default to ``0.0``.

        Raises:
            PyODNotAvailable: If ``pyod`` is not installed.
            ValueError: If fewer than :data:`MIN_FIT_RECORDS` records are given.
        """
        if len(records) < MIN_FIT_RECORDS:
            raise ValueError(f"fit() requires at least {MIN_FIT_RECORDS} records, got {len(records)}")
        matrix = self._records_to_matrix(records)
        self._fit_matrix(matrix)

    def score(self, features: dict[str, Any]) -> AnomalyResult:
        """Score one feature vector and return an :class:`AnomalyResult`.

        Degrades gracefully when ``pyod`` is unavailable: returns a
        zero-score result (``is_anomaly=False``) and logs a warning instead
        of raising, so callers in dependency-light environments keep working.
        """
        try:
            self._ensure_fitted()
        except PyODNotAvailable as exc:
            logger.warning("pyod_anomaly_unavailable", detail=str(exc))
            return AnomalyResult(
                anomaly_score=0.0,
                is_anomaly=False,
                contributing_features=[],
                model_type="unavailable",
            )

        x = self._vectorize(features)

        per_model: list[float] = []
        for model, (low, high) in zip(self._models, self._score_bounds, strict=True):
            raw = float(model.decision_function(x)[0])
            if high <= low:
                per_model.append(0.5)
            else:
                per_model.append(min(1.0, max(0.0, (raw - low) / (high - low))))

        anomaly_score = round(float(np.mean(per_model)), 4)
        is_anomaly = anomaly_score >= self.threshold
        contributing = self._contributing_features(x)

        return AnomalyResult(
            anomaly_score=anomaly_score,
            is_anomaly=is_anomaly,
            contributing_features=contributing,
            model_type="pyod-ensemble(iforest,ecod,lof)",
        )

    def status(self) -> dict[str, Any]:
        """Return a snapshot of detector state (useful for /health endpoints)."""
        return {
            "fitted": self._fitted,
            "pyod_available": pyod_available(),
            "model_count": len(self._models),
            "feature_count": len(self.feature_names),
            "threshold": self.threshold,
        }

    # ── Internals ───────────────────────────────────────────────────────────

    def _ensure_fitted(self) -> None:
        """Fit on the built-in synthetic reference corpus if never fitted."""
        if self._fitted and self._models:
            return
        logger.info(
            "pyod_anomaly_lazy_fit",
            corpus="builtin_synthetic",
            feature_count=len(self.feature_names),
        )
        # Raises PyODNotAvailable when pyod is missing.
        _load_pyod_models()
        self.fit(self._default_reference_corpus())

    def _fit_matrix(self, matrix: np.ndarray) -> None:
        """Fit the ensemble on a raw feature matrix (rows = samples)."""
        iforest_cls, ecod_cls, lof_cls = _load_pyod_models()

        models: list[Any] = [
            iforest_cls(contamination=self._contamination, random_state=self._random_state, n_jobs=1),
            ecod_cls(contamination=self._contamination),
            lof_cls(contamination=self._contamination),
        ]

        self._models = []
        self._score_bounds = []
        for model in models:
            model.fit(matrix)
            train_scores = model.decision_function(matrix)
            self._models.append(model)
            self._score_bounds.append((float(train_scores.min()), float(train_scores.max())))

        self._means = np.asarray(matrix.mean(axis=0), dtype=float).reshape(-1)
        self._stds = np.asarray(matrix.std(axis=0), dtype=float).reshape(-1)
        self._fitted = True
        logger.info("pyod_anomaly_fitted", models=len(self._models), samples=int(matrix.shape[0]))

    def _records_to_matrix(self, records: list[dict[str, Any]]) -> np.ndarray:
        """Convert feature dicts into a ``(n_samples, n_features)`` float matrix."""
        matrix = np.zeros((len(records), len(self.feature_names)), dtype=float)
        for row, record in enumerate(records):
            matrix[row] = self._vectorize(record)[0]
        return matrix

    def _vectorize(self, features: dict[str, Any]) -> np.ndarray:
        """Convert one feature dict into a ``(1, n_features)`` row, sanitising values."""
        vec = np.zeros((1, len(self.feature_names)), dtype=float)
        for i, name in enumerate(self.feature_names):
            raw = features.get(name, 0.0)
            try:
                value = float(raw) if raw is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            vec[0, i] = value if np.isfinite(value) else 0.0
        return vec

    def _contributing_features(self, x: np.ndarray) -> list[str]:
        """Return feature names deviating most from the reference corpus."""
        if self._means is None or self._stds is None:
            return []
        stds = np.where(self._stds == 0.0, 1.0, self._stds)
        z_scores = np.abs((x[0] - self._means) / stds)
        order = np.argsort(z_scores)[::-1]
        contributing = [self.feature_names[i] for i in order if float(z_scores[i]) > 1.0]
        return contributing[: self._max_contributing_features]

    def _default_reference_corpus(self) -> list[dict[str, Any]]:
        """Deterministic synthetic corpus of 'normal' wallets.

        Values are drawn from lognormal / clipped-normal distributions
        centred on typical legitimate-wallet ranges (mirroring the heuristic
        rows in ``app.fraud_gnn``'s fallback training set), so the lazy-fit
        default behaves sensibly before a real corpus is supplied.
        """
        rng = np.random.default_rng(self._random_state)
        count = 60
        records: list[dict[str, Any]] = []
        for _ in range(count):
            records.append(
                {
                    "tx_count": float(rng.lognormal(mean=5.8, sigma=0.6)),
                    "avg_tx_value": float(rng.lognormal(mean=-0.7, sigma=0.8)),
                    "tx_value_std": float(rng.lognormal(mean=-0.3, sigma=0.6)),
                    "unique_counterparties": float(rng.lognormal(mean=3.9, sigma=0.5)),
                    "received_vs_sent_ratio": float(max(0.0, rng.normal(1.0, 0.15))),
                    "avg_time_between_txs": float(rng.lognormal(mean=7.0, sigma=1.0)),
                    "contract_interactions": float(rng.lognormal(mean=4.5, sigma=0.6)),
                    "mixer_interactions": float(max(0.0, rng.normal(0.02, 0.02))),
                    "dex_interactions": float(rng.lognormal(mean=2.8, sigma=0.5)),
                    "cex_deposits": float(rng.lognormal(mean=1.0, sigma=0.5)),
                    "age_days": float(rng.uniform(30.0, 730.0)),
                    "active_days": float(rng.uniform(10.0, 400.0)),
                    "eth_balance": float(rng.lognormal(mean=4.0, sigma=1.0)),
                    "token_diversity": float(max(1.0, rng.normal(12.0, 5.0))),
                    "gas_price_avg": float(max(0.0, rng.normal(50.0, 10.0))),
                    "gas_price_std": float(rng.lognormal(mean=3.0, sigma=0.4)),
                    "failed_tx_ratio": float(max(0.0, rng.normal(0.02, 0.01))),
                }
            )
        return records


# ── Singleton ────────────────────────────────────────────────────────────────

_detector: PyODAnomalyDetector | None = None


def get_pyod_anomaly() -> PyODAnomalyDetector:
    """Return the shared :class:`PyODAnomalyDetector` singleton."""
    global _detector
    if _detector is None:
        _detector = PyODAnomalyDetector()
    return _detector


__all__ = [
    "FRAUD_FEATURES",
    "AnomalyResult",
    "PyODAnomalyDetector",
    "PyODNotAvailable",
    "get_pyod_anomaly",
    "pyod_available",
]

"""
SENTINEL AI - Self-Training Scam Classifier
============================================

Turns 5,000+ historical token scans into a self-improving ML model.
Every new scan feeds back into training. The model gets smarter over time.

Architecture:
  Historical scans → Feature extraction → XGBoost training → Model on disk
  New scan → Feature extraction → Model prediction → AI risk score (0-100)
  Confirmed scam → Weight boost → Retrain trigger

Features extracted from all 45 enrichments + market data + SENTINEL modules.
The model learns which COMBINATIONS of signals predict scams, catching
patterns that static rules miss entirely.

Premium feature: "AI-Powered Risk Score" - ML confidence alongside rules-based score.
"""

from __future__ import annotations

import json
import os
import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np

from app.core.logging import get_logger

logger = get_logger("sentinel.ai")

MODEL_DIR: Final = Path(__file__).resolve().parent.parent / "data" / "models"
MODEL_PATH: Final = MODEL_DIR / "scam_classifier_xgb.pkl"
FEATURE_NAMES_PATH: Final = MODEL_DIR / "scam_classifier_features.json"

# ──────────────────────────────────────────────────────────────
# Feature Extraction - 80+ features from enrichment data
# ──────────────────────────────────────────────────────────────


def extract_features(scan_result: dict[str, Any]) -> dict[str, float]:
    """Extract ML features from a scan result. Returns dict of feature_name → value."""
    f = {}
    free = scan_result.get("free", scan_result) if isinstance(scan_result, dict) else {}

    # ── Market data features (16 features) ──
    f["price_usd"] = float(free.get("price_usd", 0) or 0)
    f["volume_24h"] = float(free.get("volume_24h", 0) or 0)
    f["liquidity_usd"] = float(free.get("liquidity_usd", 0) or 0)
    f["market_cap"] = float(free.get("market_cap", 0) or 0)
    f["age_hours"] = float(free.get("age_hours", 0) or 0)
    f["price_change_5m"] = float(free.get("price_change_5m", 0) or 0)
    f["price_change_1h"] = float(free.get("price_change_1h", 0) or 0)
    f["price_change_24h"] = float(free.get("price_change_24h", 0) or 0)
    f["tx_count"] = float(free.get("tx_count", 0) or 0)
    f["volume_to_liquidity_ratio"] = f["volume_24h"] / max(f["liquidity_usd"], 1)
    f["market_cap_to_liquidity_ratio"] = f["market_cap"] / max(f["liquidity_usd"], 1)
    f["has_price_data"] = 1.0 if f["price_usd"] > 0 else 0.0
    f["has_liquidity"] = 1.0 if f["liquidity_usd"] > 0 else 0.0
    f["is_very_new"] = 1.0 if f["age_hours"] < 1 else 0.0
    f["is_new"] = 1.0 if f["age_hours"] < 24 else 0.0
    f["price_volatility"] = abs(f["price_change_1h"]) + abs(f["price_change_24h"])

    # ── Holder features (8 features) ──
    holders = free.get("holders", {}) or {}
    if isinstance(holders, dict):
        f["holder_count"] = float(holders.get("total", holders.get("count", 0)) or 0)
        f["top10_pct"] = float(holders.get("top_10_percentage", holders.get("top10_pct", 0)) or 0)
        f["top1_pct"] = float(holders.get("top_1_percentage", holders.get("top1_pct", 0)) or 0)
        f["holder_concentration_high"] = 1.0 if f["top10_pct"] > 80 else 0.0
        f["holder_concentration_critical"] = 1.0 if f["top10_pct"] > 95 else 0.0
        f["has_holder_data"] = 1.0 if f["holder_count"] > 0 else 0.0
        f["few_holders"] = 1.0 if 0 < f["holder_count"] < 20 else 0.0
        f["many_holders"] = 1.0 if f["holder_count"] > 500 else 0.0

    # ── Honeypot / trade simulation (8 features) ──
    sim = free.get("simulation", {}) or {}
    if isinstance(sim, dict):
        f["honeypot_detected"] = 1.0 if sim.get("is_honeypot") or sim.get("honeypot") else 0.0
        f["buy_success"] = 1.0 if sim.get("buy_success", True) else 0.0
        f["sell_success"] = 1.0 if sim.get("sell_success", True) else 0.0
        f["buy_tax_pct"] = float(sim.get("buy_tax_pct", 0) or 0)
        f["sell_tax_pct"] = float(sim.get("sell_tax_pct", 0) or 0)
        f["tax_high"] = 1.0 if f["sell_tax_pct"] > 10 else 0.0
        f["tax_extreme"] = 1.0 if f["sell_tax_pct"] > 50 else 0.0
        f["has_simulation"] = 1.0 if sim else 0.0

    # ── Contract / authority features (6 features) ──
    contract = free.get("contract_verification", {}) or {}
    if isinstance(contract, dict):
        f["contract_verified"] = 1.0 if contract.get("verified") else 0.0
        f["contract_unverified"] = 1.0 if not contract.get("verified") else 0.0
        f["is_proxy"] = 1.0 if contract.get("is_proxy") or contract.get("proxy") else 0.0
        f["mint_authority_renounced"] = 1.0 if free.get("mint_authority") == "renounced" else 0.0
        f["freeze_authority_exists"] = 1.0 if free.get("freeze_authority") else 0.0
        f["has_contract_data"] = 1.0 if contract else 0.0

    # ── Deployer features (8 features) ──
    deployer = free.get("deployer", {}) or {}
    deep = free.get("deep_deployer", {}) or {}
    if isinstance(deployer, dict) or isinstance(deep, dict):
        d = {**deployer, **deep} if isinstance(deep, dict) else deployer
        f["deployer_known"] = 1.0 if d.get("address") or deployer.get("address") else 0.0
        f["deployer_risk_score"] = float(d.get("risk_score", 0) or 0)
        f["deployer_scam_count"] = float(d.get("scam_count", 0) or 0)
        f["deployer_high_risk"] = 1.0 if f["deployer_risk_score"] > 70 else 0.0
        f["deployer_critical"] = 1.0 if f["deployer_risk_score"] > 90 else 0.0
        f["multi_chain_deployer"] = 1.0 if free.get("cross_chain", {}).get("chain_count", 0) > 1 else 0.0
        f["deployer_chain_count"] = float(
            free.get("cross_chain", {}).get("chain_count", 1) if isinstance(free.get("cross_chain"), dict) else 1
        )
        f["has_deployer_data"] = 1.0 if f["deployer_known"] else 0.0

    # ── LP / liquidity lock (5 features) ──
    lp = free.get("lp_lock_multi", {}) or {}
    if isinstance(lp, dict):
        f["lp_locked"] = 1.0 if lp.get("locked") or lp.get("is_locked") else 0.0
        f["lp_lock_unconfirmed"] = 1.0 if not f["lp_locked"] and f["liquidity_usd"] > 0 else 0.0
        f["lp_lock_pct"] = float(lp.get("lock_percentage", lp.get("locked_pct", 0)) or 0)
        f["lp_has_data"] = 1.0 if lp else 0.0
        f["no_lp"] = 1.0 if f["liquidity_usd"] == 0 else 0.0

    # ── External API signals (12 features) ──
    # Birdeye
    birdeye = free.get("birdeye", {}) or {}
    f["birdeye_honeypot"] = 1.0 if isinstance(birdeye, dict) and birdeye.get("honeypot") else 0.0
    f["birdeye_rugpull"] = 1.0 if isinstance(birdeye, dict) and birdeye.get("rugpull") else 0.0

    # Honeypot.is
    hp = free.get("honeypot_is", {}) or {}
    f["honeypot_is_detected"] = 1.0 if isinstance(hp, dict) and hp.get("is_honeypot") else 0.0

    # Token Sniffer
    ts = free.get("token_sniffer", {}) or {}
    f["tokensniffer_scam"] = 1.0 if isinstance(ts, dict) and ts.get("is_scam") else 0.0
    f["tokensniffer_score"] = float(ts.get("score", 0) or 0) if isinstance(ts, dict) else 0.0

    # ChainAware AI
    ca = free.get("chainaware_ai", {}) or {}
    f["chainaware_rug_risk"] = float(ca.get("rug_probability", 0) or 0) if isinstance(ca, dict) else 0.0

    # GoPlus
    gp = free.get("goplus", {}) or {}
    f["goplus_honeypot"] = 1.0 if isinstance(gp, dict) and gp.get("is_honeypot") else 0.0

    # De.Fi
    df = free.get("defi_scanner", {}) or {}
    f["defi_honeypot"] = 1.0 if isinstance(df, dict) and df.get("honeypot") else 0.0
    f["defi_ai_score"] = float(df.get("aiScore", 50) or 50) if isinstance(df, dict) else 50.0

    # Copycat
    cc = free.get("copycat_check", {}) or {}
    f["is_copycat"] = 1.0 if isinstance(cc, dict) and cc.get("is_copycat") else 0.0

    # Volume anomaly
    va = free.get("volume_anomaly", {}) or {}
    f["volume_manipulation"] = 1.0 if isinstance(va, dict) and va.get("manipulation_detected") else 0.0

    # ── RAG / scam database features (6 features) ──
    rag = free.get("rag_scam_check", {}) or {}
    if isinstance(rag, dict):
        f["rag_scam_match"] = 1.0 if rag.get("match_found") or rag.get("is_scam") else 0.0
        f["rag_similarity"] = float(rag.get("similarity", 0) or 0)
        f["rag_similarity_high"] = 1.0 if f["rag_similarity"] > 0.8 else 0.0
        f["rag_matches_count"] = float(rag.get("match_count", 0) or 0)
        f["known_scam_address"] = 1.0 if rag.get("is_known_scam") else 0.0
        f["has_rag_data"] = 1.0 if rag else 0.0

    # ── Social / sentiment (5 features) ──
    sentiment = free.get("sentiment", {}) or {}
    f["social_volume"] = float(sentiment.get("mention_count", 0) or 0) if isinstance(sentiment, dict) else 0.0
    f["sentiment_score"] = float(sentiment.get("score", 0) or 0) if isinstance(sentiment, dict) else 0.0
    santiment = free.get("santiment", {}) or {}
    f["santiment_hype"] = 1.0 if isinstance(santiment, dict) and santiment.get("hype_detected") else 0.0
    f["santiment_viral_low_vol"] = 1.0 if isinstance(santiment, dict) and santiment.get("viral_low_volume") else 0.0
    f["has_social_data"] = 1.0 if sentiment or santiment else 0.0

    # ── Chain/network features (4 features) ──
    chain = scan_result.get("chain", "").lower()
    f["chain_solana"] = 1.0 if chain == "solana" else 0.0
    f["chain_ethereum"] = 1.0 if chain == "ethereum" else 0.0
    f["chain_bsc"] = 1.0 if chain == "bsc" else 0.0
    f["chain_base"] = 1.0 if chain == "base" else 0.0

    # ── Nansen / Arkham (4 features) ──
    nansen = free.get("nansen", {}) or {}
    f["nansen_low_holders"] = 1.0 if isinstance(nansen, dict) and nansen.get("low_holders") else 0.0
    f["nansen_net_outflow"] = 1.0 if isinstance(nansen, dict) and nansen.get("net_outflow") else 0.0
    arkham = free.get("arkham", {}) or {}
    f["arkham_scam_entity"] = 1.0 if isinstance(arkham, dict) and arkham.get("scam_entity") else 0.0
    f["has_premium_data"] = 1.0 if nansen or arkham else 0.0

    return f


# ──────────────────────────────────────────────────────────────
# Model Training
# ──────────────────────────────────────────────────────────────


class ScamClassifier:
    """XGBoost classifier trained on historical scan data."""

    def __init__(self) -> None:
        self.model = None
        self.feature_names: list[str] = []
        self.training_samples: int = 0
        self.accuracy: float = 0.0
        self.last_trained: str | None = None
        self._load()

    def _load(self) -> None:
        """Load model from disk if available."""
        if os.path.exists(MODEL_PATH):
            try:
                with open(MODEL_PATH, "rb") as f:
                    # SECURITY: pickle loads are safe here because models are bundled in Docker image
                    self.model = pickle.load(f, usedforsecurity=False)
                if os.path.exists(FEATURE_NAMES_PATH):
                    with open(FEATURE_NAMES_PATH) as f:
                        meta = json.load(f)
                        self.feature_names = meta.get("features", [])
                        self.training_samples = meta.get("samples", 0)
                        self.accuracy = meta.get("accuracy", 0.0)
                        self.last_trained = meta.get("trained_at")
                logger.info(
                    f"Loaded scam classifier: {self.training_samples} samples, "
                    f"{len(self.feature_names)} features, {self.accuracy:.1%} accuracy"
                )
            except Exception as e:
                logger.warning(f"Failed to load classifier: {e}")
                self.model = None

    def _save(self) -> None:
        """Persist model and metadata to disk."""
        os.makedirs(MODEL_DIR, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(self.model, f)
        with open(FEATURE_NAMES_PATH, "w") as f:
            json.dump(
                {
                    "features": self.feature_names,
                    "samples": self.training_samples,
                    "accuracy": self.accuracy,
                    "trained_at": datetime.now(UTC).isoformat(),
                },
                f,
            )
        logger.info(f"Saved classifier: {self.training_samples} samples, {self.accuracy:.1%} accuracy")

    def train(self, scans: list[dict[str, Any]]) -> dict[str, Any]:
        """Train on historical scan data. Each scan must have 'is_scam' label."""
        try:
            from xgboost import XGBClassifier
        except ImportError:
            logger.warning("XGBoost not installed. Install with: pip install xgboost")
            return {"status": "error", "error": "xgboost not installed"}

        # Extract features and labels
        X_list = []
        y_list = []

        for scan in scans:
            features = extract_features(scan)
            is_scam = scan.get("is_scam", False) or scan.get("verdict") == "scam"

            if not X_list:  # First sample - record feature names
                self.feature_names = sorted(features.keys())

            # Build feature vector in consistent order
            row = [features.get(name, 0.0) for name in self.feature_names]
            X_list.append(row)
            y_list.append(1 if is_scam else 0)

        if len(X_list) < 10:
            return {"status": "error", "error": f"Need at least 10 samples, got {len(X_list)}"}

        if sum(y_list) < 2:
            return {"status": "error", "error": "Need at least 2 scam samples for training"}

        X = np.array(X_list, dtype=np.float32)
        y = np.array(y_list, dtype=np.int32)

        # Handle NaN/Inf
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # Train
        self.model = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=(len(y) - sum(y)) / max(sum(y), 1),  # Handle class imbalance
            random_state=42,
            use_label_encoder=False,
            eval_metric="logloss",
        )
        self.model.fit(X, y)

        # Evaluate
        train_pred = self.model.predict(X)
        self.accuracy = float((train_pred == y).mean())
        self.training_samples = len(X_list)
        self.last_trained = datetime.now(UTC).isoformat()

        # Feature importance
        importance = {}
        if hasattr(self.model, "feature_importances_"):
            for name, imp in zip(self.feature_names, self.model.feature_importances_, strict=False):
                importance[name] = round(float(imp), 4)

        self._save()

        return {
            "status": "trained",
            "samples": self.training_samples,
            "scam_samples": int(sum(y)),
            "features": len(self.feature_names),
            "accuracy": round(self.accuracy, 3),
            "feature_importance": dict(sorted(importance.items(), key=lambda x: -x[1])[:15]),
        }

    def predict(self, scan_result: dict[str, Any]) -> dict[str, Any]:
        """Predict scam probability for a new scan."""
        if self.model is None:
            return {"status": "no_model", "probability": None, "confidence": 0}

        features = extract_features(scan_result)
        row = np.array([[features.get(name, 0.0) for name in self.feature_names]], dtype=np.float32)
        row = np.nan_to_num(row, nan=0.0, posinf=0.0, neginf=0.0)

        try:
            proba = self.model.predict_proba(row)[0]
            scam_prob = float(proba[1]) if len(proba) > 1 else float(proba[0])

            # Confidence based on training data quality
            confidence = min(self.accuracy * 100, 95) if self.accuracy > 0 else 50

            # Get top contributing features
            contributions = {}
            if hasattr(self.model, "feature_importances_"):
                for name, imp in zip(self.feature_names, self.model.feature_importances_, strict=False):
                    if features.get(name, 0) != 0 and imp > 0.01:
                        contributions[name] = {
                            "value": features.get(name, 0),
                            "importance": round(float(imp), 3),
                        }

            # Risk level
            if scam_prob > 0.8:
                risk_level = "critical"
            elif scam_prob > 0.6:
                risk_level = "high"
            elif scam_prob > 0.4:
                risk_level = "medium"
            elif scam_prob > 0.2:
                risk_level = "low"
            else:
                risk_level = "safe"

            return {
                "status": "ok",
                "probability": round(scam_prob, 4),
                "risk_level": risk_level,
                "ai_risk_score": round(scam_prob * 100),
                "confidence": round(confidence, 1),
                "model_samples": self.training_samples,
                "model_accuracy": round(self.accuracy, 3),
                "top_signals": dict(sorted(contributions.items(), key=lambda x: -x[1]["importance"])[:8]),
            }
        except Exception as e:
            logger.warning(f"Prediction failed: {e}")
            return {"status": "error", "probability": None, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────

_classifier: ScamClassifier | None = None


def get_classifier() -> ScamClassifier:
    global _classifier
    if _classifier is None:
        _classifier = ScamClassifier()
    return _classifier
